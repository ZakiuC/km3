// Socket syscalls are wrapped ONLY in this test executable. No real CAN/netlink
// sockets are opened; recovery ACK, errno and timeout paths are deterministic.
#include <array>
#include <cerrno>
#include <cstring>
#include <linux/can/error.h>
#include <linux/can/netlink.h>
#include <linux/can/raw.h>
#include <linux/if_link.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <net/if.h>

// glibc definitions must precede Linux flags to avoid duplicate net structs.
#include <linux/if.h>

#include <poll.h>
#include <sys/socket.h>

#include "km3/clock.hpp"
#include "km3/hal/socketcan_device.hpp"
#include "test_harness.hpp"

namespace {
constexpr int kIfindex = 42;
constexpr int kNetlinkFd = 10001;
constexpr int kCanFd = 10002;
struct Fixture {
    int socket_error = 0;
    int tx_error = 0;
    int ack_error = 0;
    bool timeout = false;
    bool driver_echo = false;
    bool own = false;
    int loopback = -1;
    int confirm_flags = MSG_CONFIRM;
    bool wrong_confirm = false;
    bool provide_tpdo = false;
    bool queue_confirm_on_send = false;
    bool interrupted = false;
    bool malformed_reply = false;
    bool truncated_reply = false;
    bool recovery_requested = false;
    bool subscribed_errors = false;
    bool fd_enabled = false;
    bool nonblocking = true;
    unsigned tx_calls = 0;
    std::size_t filter_count = 0;
    unsigned restart_calls = 0;
    unsigned data_available = 0;
    std::uint32_t state = CAN_STATE_ERROR_ACTIVE;
    std::uint32_t bitrate = 1000000;
    std::uint32_t bus_off = 0;
    can_frame rx{};
    can_frame sent{};
} fake;

struct LinkMessage {
    alignas(nlmsghdr) std::array<std::uint8_t, 1024> bytes{};
    nlmsghdr& Header() { return *reinterpret_cast<nlmsghdr*>(bytes.data()); }
    rtattr* Add(std::uint16_t type, const void* data, std::size_t length) {
        auto* attr = reinterpret_cast<rtattr*>(bytes.data() + NLMSG_ALIGN(Header().nlmsg_len));
        attr->rta_type = type;
        attr->rta_len = static_cast<std::uint16_t>(RTA_LENGTH(length));
        if (length)
            std::memcpy(RTA_DATA(attr), data, length);
        Header().nlmsg_len =
            static_cast<std::uint32_t>(NLMSG_ALIGN(Header().nlmsg_len) + RTA_ALIGN(attr->rta_len));
        return attr;
    }
    void Finish(rtattr* attr) {
        attr->rta_len = static_cast<std::uint16_t>(bytes.data() + Header().nlmsg_len -
                                                   reinterpret_cast<std::uint8_t*>(attr));
    }
    LinkMessage() {
        Header().nlmsg_type = RTM_NEWLINK;
        Header().nlmsg_seq = 1;
        Header().nlmsg_len = NLMSG_LENGTH(sizeof(ifinfomsg));
        auto* info = static_cast<ifinfomsg*>(NLMSG_DATA(&Header()));
        info->ifi_index = kIfindex;
        info->ifi_flags = IFF_UP | (fake.driver_echo ? IFF_ECHO : 0);
        auto* link = Add(IFLA_LINKINFO, nullptr, 0);
        Add(IFLA_INFO_KIND, "can", 4);
        can_device_stats stats{};
        stats.bus_off = fake.bus_off;
        stats.bus_error = 7;
        stats.restarts = 3;
        Add(IFLA_INFO_XSTATS, &stats, sizeof(stats));
        auto* data = Add(IFLA_INFO_DATA, nullptr, 0);
        can_bittiming timing{};
        timing.bitrate = fake.bitrate;
        Add(IFLA_CAN_BITTIMING, &timing, sizeof(timing));
        Add(IFLA_CAN_STATE, &fake.state, sizeof(fake.state));
        can_berr_counter error_counters{12, 34};
        Add(IFLA_CAN_BERR_COUNTER, &error_counters, sizeof(error_counters));
        Finish(data);
        Finish(link);
    }
};

km3::BusEndpoint Endpoint() {
    return {"test-can", "testcan", 1000000, "canopen"};
}
}  // namespace

extern "C" {
unsigned __wrap_if_nametoindex(const char* name) {
    return std::strcmp(name, "testcan") == 0 ? kIfindex : 0;
}
int __wrap_socket(int domain, int type, int) {
    if (fake.socket_error != 0) {
        errno = fake.socket_error;
        return -1;
    }
    fake.nonblocking = fake.nonblocking && (type & SOCK_NONBLOCK) != 0;
    return domain == AF_NETLINK ? kNetlinkFd : kCanFd;
}
int __wrap_close(int) {
    return 0;
}
int __wrap_bind(int, const sockaddr*, socklen_t) {
    return 0;
}
int __wrap_setsockopt(int, int, int option, const void* bytes, socklen_t length) {
    if (option == CAN_RAW_ERR_FILTER) {
        fake.subscribed_errors = length == sizeof(can_err_mask_t) &&
                                 *static_cast<const can_err_mask_t*>(bytes) == CAN_ERR_MASK;
    }
    if (option == CAN_RAW_LOOPBACK)
        fake.loopback = *static_cast<const int*>(bytes);
    if (option == CAN_RAW_RECV_OWN_MSGS)
        fake.own = *static_cast<const int*>(bytes) == 1;
    if (option == CAN_RAW_FD_FRAMES)
        fake.fd_enabled = true;
    if (option == CAN_RAW_FILTER)
        fake.filter_count = length / sizeof(can_filter);
    return 0;
}
ssize_t __wrap_sendto(int, const void* bytes, std::size_t length, int, const sockaddr*, socklen_t) {
    const auto* request = static_cast<const nlmsghdr*>(bytes);
    fake.recovery_requested = request->nlmsg_type == RTM_NEWLINK;
    if (fake.recovery_requested) {
        ++fake.restart_calls;
        // Recover must carry a CAN_RESTART nested attribute, never link flags.
        const auto* link = static_cast<const ifinfomsg*>(NLMSG_DATA(request));
        if (link->ifi_flags != 0 || link->ifi_change != 0) {
            errno = EINVAL;
            return -1;
        }
        bool found = false;
        auto remaining = static_cast<int>(IFLA_PAYLOAD(request));
        for (auto* attr = IFLA_RTA(link); RTA_OK(attr, remaining);
             attr = RTA_NEXT(attr, remaining)) {
            int nested_len = static_cast<int>(RTA_PAYLOAD(attr));
            for (auto* nested = static_cast<rtattr*>(RTA_DATA(attr)); RTA_OK(nested, nested_len);
                 nested = RTA_NEXT(nested, nested_len)) {
                if (nested->rta_type != IFLA_INFO_DATA)
                    continue;
                int field_len = static_cast<int>(RTA_PAYLOAD(nested));
                auto* field = static_cast<rtattr*>(RTA_DATA(nested));
                found = RTA_OK(field, field_len) && field->rta_type == IFLA_CAN_RESTART &&
                        RTA_PAYLOAD(field) == 4 &&
                        *static_cast<std::uint32_t*>(RTA_DATA(field)) == 1;
            }
        }
        if (!found) {
            errno = EINVAL;
            return -1;
        }
    }
    return static_cast<ssize_t>(length);
}
int __wrap_poll(pollfd* fds, nfds_t, int) {
    if (fake.interrupted) {
        fake.interrupted = false;
        errno = EINTR;
        return -1;
    }
    if (fake.timeout || (fds[0].fd == kCanFd && fake.data_available == 0))
        return 0;
    fds[0].revents = POLLIN;
    return 1;
}
ssize_t __wrap_recvmsg(int fd, msghdr* msg, int) {
    if (fd == kCanFd) {
        if (!fake.data_available) {
            errno = EAGAIN;
            return -1;
        }
        --fake.data_available;
        auto raw = fake.sent;
        msg->msg_flags = fake.confirm_flags;
        if (fake.provide_tpdo) {
            fake.provide_tpdo = false;
            raw = fake.rx;
            msg->msg_flags = 0;
        } else if (fake.wrong_confirm) {
            raw.can_id ^= 1;
        }
        std::memcpy(msg->msg_iov[0].iov_base, &raw, sizeof(raw));
        return sizeof(raw);
    }
    auto* sender = static_cast<sockaddr_nl*>(msg->msg_name);
    sender->nl_pid = 0;
    msg->msg_flags = fake.truncated_reply ? MSG_TRUNC : 0;
    if (fake.recovery_requested) {
        struct Reply {
            nlmsghdr header;
            nlmsgerr error;
        } reply{};
        reply.header.nlmsg_len = sizeof(reply);
        reply.header.nlmsg_type = NLMSG_ERROR;
        reply.header.nlmsg_seq = 1;
        reply.error.error = -fake.ack_error;
        std::memcpy(msg->msg_iov[0].iov_base, &reply, sizeof(reply));
        if (fake.ack_error == 0)
            fake.state = CAN_STATE_ERROR_ACTIVE;
        return sizeof(reply);
    }
    LinkMessage reply;
    if (fake.malformed_reply)
        reply.Header().nlmsg_len = 0;
    const auto length = fake.malformed_reply ? sizeof(nlmsghdr) : reply.Header().nlmsg_len;
    std::memcpy(msg->msg_iov[0].iov_base, reply.bytes.data(), length);
    return static_cast<ssize_t>(length);
}
ssize_t __wrap_send(int, const void* bytes, std::size_t length, int flags) {
    ++fake.tx_calls;
    fake.nonblocking = fake.nonblocking && (flags & MSG_DONTWAIT) != 0;
    if (fake.tx_error != 0) {
        errno = fake.tx_error;
        return -1;
    }
    std::memcpy(&fake.sent, bytes, sizeof(fake.sent));
    if (fake.queue_confirm_on_send)
        ++fake.data_available;
    return static_cast<ssize_t>(length);
}
ssize_t __wrap_recv(int, void* bytes, std::size_t length, int flags) {
    fake.nonblocking = fake.nonblocking && (flags & MSG_DONTWAIT) != 0;
    if (fake.data_available == 0) {
        errno = EAGAIN;
        return -1;
    }
    --fake.data_available;
    std::memcpy(bytes, &fake.rx, std::min(length, sizeof(fake.rx)));
    return sizeof(fake.rx);
}
}  // extern C

KM3_TEST(classic_frames_roundtrip_standard_extended_and_remote) {
    for (bool extended : {false, true}) {
        for (bool remote : {false, true}) {
            for (std::uint8_t length = 0; length <= 8; ++length) {
                km3::CanFrame frame{};
                frame.id = extended ? CAN_EFF_MASK : CAN_SFF_MASK;
                frame.extended = extended;
                frame.remote = remote;
                frame.dlc = length;
                for (std::uint8_t i = 0; i < length; ++i) frame.data[i] = i;
                can_frame raw{};
                KM3_REQUIRE(km3::SocketCanDevice::Encode(frame, raw).ok());
                KM3_CHECK_EQ(raw.can_id & CAN_EFF_MASK, frame.id);
                KM3_CHECK_EQ((raw.can_id & CAN_EFF_FLAG) != 0, extended);
                KM3_CHECK_EQ((raw.can_id & CAN_RTR_FLAG) != 0, remote);
                KM3_CHECK_EQ(raw.can_dlc, length);
                for (std::uint8_t i = 0; i < length; ++i)
                    KM3_CHECK_EQ(raw.data[i], remote ? 0u : static_cast<unsigned>(i));
                const auto decoded = km3::SocketCanDevice::Decode(raw);
                KM3_REQUIRE(decoded.ok());
                KM3_CHECK_EQ(decoded.value().id, frame.id);
                KM3_CHECK_EQ(decoded.value().dlc, frame.dlc);
                KM3_CHECK_EQ(decoded.value().extended, extended);
                KM3_CHECK_EQ(decoded.value().remote, remote);
                if (!remote)
                    KM3_CHECK_EQ(decoded.value().data, frame.data);
            }
        }
    }
}

KM3_TEST(invalid_raw_dlc_ids_and_fd_are_rejected_before_any_syscall) {
    fake = {};
    km3::SocketCanDevice can(Endpoint());
    KM3_REQUIRE(can.Open().ok());
    km3::CanFrame frame{};
    for (const auto length : {9, 64, 255}) {
        frame.dlc = static_cast<std::uint8_t>(length);
        KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::EInvalidRequest);
        can_frame raw{};
        raw.can_dlc = frame.dlc;
        KM3_CHECK(!km3::SocketCanDevice::Decode(raw).ok());
    }
    frame.dlc = 0;
    frame.fd = true;
    KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::EInvalidRequest);
    frame.fd = false;
    frame.bitrate_switch = true;
    KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::EInvalidRequest);
    frame.bitrate_switch = false;
    frame.id = 0x800;
    KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(fake.tx_calls, 0u);
    KM3_CHECK(!can.Capabilities().supports_fd);
    KM3_CHECK_EQ(can.Capabilities().max_bitrate, 1000000u);
    KM3_CHECK(can.Backend() == km3::CanBackend::SocketCan);
}

KM3_TEST(nonblocking_io_empty_bus_timeouts_filters_and_error_counters) {
    fake = {};
    km3::SocketCanDevice can(Endpoint());
    KM3_REQUIRE(can.Open().ok());
    KM3_CHECK(fake.subscribed_errors && !fake.fd_enabled && fake.nonblocking);
    KM3_CHECK(can.Receive(0).code() == km3::ErrorCode::ECommandTimeout);
    KM3_CHECK(can.Receive(10).code() == km3::ErrorCode::ECommandTimeout);
    KM3_CHECK(can.Receive(-1).code() == km3::ErrorCode::EInvalidRequest);
    KM3_REQUIRE(can.SetFilters({{0x80, CAN_SFF_MASK}}).ok());
    KM3_CHECK_EQ(fake.filter_count, 1u);
    KM3_REQUIRE(can.SetFilters({}).ok());
    KM3_CHECK_EQ(fake.filter_count, 1u);  // explicit match-all, not receive-none
    KM3_CHECK(can.SetFilters(std::vector<km3::CanFilter>(513)).code() ==
              km3::ErrorCode::EInvalidRequest);
    km3::CanFrame frame{};
    frame.id = 0x80;
    KM3_REQUIRE(can.Send(frame).ok());
    fake.rx = fake.sent;
    fake.data_available = 1;
    KM3_REQUIRE(can.Receive(0).ok());
    fake.tx_error = ENOBUFS;
    KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::EDeviceBusy);
    KM3_CHECK_EQ(can.LastSystemError(), ENOBUFS);
    fake.tx_error = ENETDOWN;
    KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::EDeviceOffline);
    const auto stats = can.Stats();
    KM3_CHECK_EQ(stats.tx_frames, 1u);
    KM3_CHECK_EQ(stats.rx_frames, 1u);
    KM3_CHECK_EQ(stats.tx_errors, 2u);
    KM3_CHECK_EQ(stats.rx_errors, 0u);
    KM3_CHECK(stats.last_error_us > 0);
    KM3_CHECK(fake.nonblocking);
    KM3_CHECK(can.Close().ok());
    KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::EDeviceOffline);
}

KM3_TEST(netlink_nested_health_and_malformed_unaligned_messages) {
    fake = {};
    fake.bus_off = 4;
    LinkMessage message;
    km3::SocketCanLinkInfo info{};
    KM3_REQUIRE(km3::SocketCanDevice::ParseLinkMessage(message.bytes.data(),
                                                       message.Header().nlmsg_len, kIfindex, info)
                    .ok());
    KM3_CHECK(info.has_bitrate && info.has_state && info.has_stats && info.up && info.is_can);
    KM3_CHECK_EQ(info.bitrate, 1000000u);
    KM3_CHECK_EQ(info.bus_off, 4u);
    KM3_CHECK_EQ(info.bus_error, 7u);
    KM3_CHECK_EQ(info.restarts, 3u);
    KM3_CHECK_EQ(info.tx_error_counter, 12u);
    KM3_CHECK_EQ(info.rx_error_counter, 34u);
    KM3_CHECK(!km3::SocketCanDevice::ParseLinkMessage(nullptr, 100, kIfindex, info).ok());
    KM3_CHECK(!km3::SocketCanDevice::ParseLinkMessage(
                   message.bytes.data() + 1, message.Header().nlmsg_len - 1, kIfindex, info)
                   .ok());
    KM3_CHECK(!km3::SocketCanDevice::ParseLinkMessage(
                   message.bytes.data(), message.Header().nlmsg_len - 1, kIfindex, info)
                   .ok());
    KM3_CHECK(!km3::SocketCanDevice::ParseLinkMessage(
                   message.bytes.data(), message.Header().nlmsg_len, kIfindex + 1, info)
                   .ok());
    auto* attr = reinterpret_cast<rtattr*>(message.bytes.data() + NLMSG_LENGTH(sizeof(ifinfomsg)));
    attr->rta_len = 1;
    KM3_CHECK(!km3::SocketCanDevice::ParseLinkMessage(message.bytes.data(),
                                                      message.Header().nlmsg_len, kIfindex, info)
                   .ok());
}

KM3_TEST(bus_off_notifications_are_not_data_and_recovery_requires_kernel_ack) {
    fake = {};
    fake.state = CAN_STATE_BUS_OFF;
    fake.bus_off = 3;
    km3::SocketCanDevice can(Endpoint());
    KM3_REQUIRE(can.Open().ok());
    fake.rx.can_id = CAN_ERR_FLAG | CAN_ERR_BUSOFF;
    fake.rx.can_dlc = CAN_ERR_DLC;
    fake.data_available = 1;
    KM3_CHECK(can.Receive(0).code() == km3::ErrorCode::EDeviceError);
    const auto before = can.Stats();
    KM3_CHECK_EQ(before.bus_off_count, 3u);  // max, not kernel+notification
    KM3_CHECK_EQ(before.rx_errors, 1u);
    KM3_CHECK_EQ(before.rx_frames, 0u);
    KM3_CHECK(can.StatusOf().state == km3::DeviceStatusEnum::BusOff);
    fake.ack_error = EPERM;
    KM3_CHECK(can.RecoverFromBusOff().code() == km3::ErrorCode::EPermission);
    KM3_CHECK_EQ(can.LastSystemError(), EPERM);
    fake.ack_error = 0;
    fake.interrupted = true;
    KM3_REQUIRE(can.RecoverFromBusOff().ok());
    KM3_CHECK_EQ(fake.restart_calls, 2u);
    KM3_CHECK(can.StatusOf().state == km3::DeviceStatusEnum::Open);
    KM3_CHECK(can.RecoverFromBusOff().code() == km3::ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(fake.tx_calls, 0u);  // recovery never sends a CAN/motor frame
    KM3_CHECK_EQ(can.LinkInfo().bus_off, 3u);
    KM3_CHECK(can.Close().ok());
    KM3_CHECK(can.RecoverFromBusOff().code() == km3::ErrorCode::EDeviceOffline);
}

KM3_TEST(link_queries_report_timeout_malformed_truncation_and_errno_without_fallback) {
    fake = {};
    fake.timeout = true;
    km3::SocketCanDevice timeout(Endpoint());
    KM3_CHECK(timeout.Open().code() == km3::ErrorCode::ECommandTimeout);
    KM3_CHECK_EQ(timeout.LastSystemError(), ETIMEDOUT);
    fake = {};
    fake.malformed_reply = true;
    km3::SocketCanDevice malformed(Endpoint());
    KM3_CHECK(malformed.Open().code() == km3::ErrorCode::EDeviceError);
    fake = {};
    fake.truncated_reply = true;
    km3::SocketCanDevice truncated(Endpoint());
    KM3_CHECK(truncated.Open().code() == km3::ErrorCode::EDeviceError);
    fake = {};
    fake.socket_error = EACCES;
    km3::SocketCanDevice denied(Endpoint());
    KM3_CHECK(denied.Open().code() == km3::ErrorCode::EPermission);
    KM3_CHECK_EQ(denied.LastSystemError(), EACCES);
    fake = {};
    fake.bitrate = 500000;
    km3::SocketCanDevice mismatch(Endpoint());
    KM3_CHECK(mismatch.Open().code() == km3::ErrorCode::EInvalidConfig);
    KM3_CHECK(!mismatch.IsOpen());
    fake = {};
    km3::SocketCanDevice healthy(Endpoint());
    KM3_REQUIRE(healthy.Open().ok());
    fake.timeout = true;
    healthy.Stats();
    KM3_CHECK(healthy.LastLinkError() == km3::ErrorCode::ECommandTimeout);
    KM3_CHECK(!healthy.StatusOf().detail.empty());
}

KM3_TEST(tx_confirmation_is_opt_in_requires_driver_echo_and_preserves_tpdo) {
    fake = {};
    km3::SocketCanDevice ordinary(Endpoint());
    KM3_REQUIRE(ordinary.Open().ok());
    KM3_CHECK_EQ(fake.loopback, 0);
    KM3_CHECK(!ordinary.SupportsTxConfirmation());
    km3::SocketCanDevice unsupported(Endpoint(), true);
    KM3_CHECK(!unsupported.Open().ok());
    fake.driver_echo = true;
    km3::SocketCanDevice can(Endpoint(), true);
    KM3_REQUIRE(can.Open().ok());
    KM3_CHECK_EQ(fake.loopback, 1);
    KM3_CHECK(fake.own);
    fake.rx.can_id = 0x281;
    fake.rx.can_dlc = 8;
    fake.rx.data[0] = 0x91;
    fake.provide_tpdo = true;
    fake.data_available = 2;
    km3::CanFrame frame{};
    frame.id = 0x301;
    frame.dlc = 8;
    KM3_REQUIRE(can.SendConfirmed(frame, km3::MonotonicUs() + 100000).ok());
    const auto tpdo = can.Receive(0);
    KM3_REQUIRE(tpdo.ok());
    KM3_CHECK_EQ(tpdo.value().id, 0x281u);
    KM3_CHECK_EQ(tpdo.value().data[0], 0x91u);
}

KM3_TEST(ordinary_echo_wrong_confirmation_and_queued_only_do_not_release_fence) {
    for (unsigned mode = 0; mode < 3; ++mode) {
        fake = {};
        fake.driver_echo = true;
        km3::SocketCanDevice can(Endpoint(), true);
        KM3_REQUIRE(can.Open().ok());
        fake.data_available = mode == 2 ? 0 : 1;
        fake.confirm_flags = mode == 0 ? MSG_DONTROUTE : MSG_CONFIRM;
        fake.wrong_confirm = mode == 1;
        km3::CanFrame frame{};
        frame.id = 0x301;
        KM3_CHECK(!can.SendConfirmed(frame, km3::MonotonicUs() + 2000).ok());
        KM3_CHECK_EQ(fake.tx_calls, 1u);
        KM3_CHECK(!can.SendConfirmed(frame, km3::MonotonicUs() + 100000).ok());
        KM3_CHECK_EQ(fake.tx_calls, 1u);  // quarantined; stale echo cannot unblock
    }
}

KM3_TEST(quarantine_blocks_plain_send_and_late_confirmation_is_not_consumed) {
    fake = {};
    fake.driver_echo = true;
    km3::SocketCanDevice can(Endpoint(), true);
    KM3_REQUIRE(can.Open().ok());
    km3::CanFrame frame{};
    frame.id = 0x301;
    KM3_REQUIRE(can.SendConfirmed(frame, km3::MonotonicUs() + 2000).code() ==
                km3::ErrorCode::ECommandTimeout);
    KM3_CHECK_EQ(fake.tx_calls, 1u);
    // The delayed matching completion cannot release this quarantined socket.
    fake.data_available = 1;
    frame.id = 0x80;
    KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(fake.tx_calls, 1u);
    frame.id = 0x301;
    KM3_CHECK(can.SendConfirmed(frame, km3::MonotonicUs() + 100000).code() ==
              km3::ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(fake.tx_calls, 1u);
    KM3_CHECK_EQ(fake.data_available, 1u);
}

KM3_TEST(confirmation_mode_plain_send_cannot_create_an_orphan_completion) {
    fake = {};
    fake.driver_echo = true;
    fake.queue_confirm_on_send = true;
    km3::SocketCanDevice can(Endpoint(), true);
    KM3_REQUIRE(can.Open().ok());
    km3::CanFrame frame{};
    frame.id = 0x301;
    frame.dlc = 8;
    frame.data[0] = 0x42;
    KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(fake.tx_calls, 0u);
    KM3_CHECK_EQ(fake.data_available, 0u);
    // Identical payload is safe only because plain Send could not enqueue an
    // earlier copy: precisely one completion is generated and then consumed.
    KM3_REQUIRE(can.SendConfirmed(frame, km3::MonotonicUs() + 100000).ok());
    KM3_CHECK_EQ(fake.tx_calls, 1u);
    KM3_CHECK_EQ(fake.data_available, 0u);
    KM3_CHECK(can.Send(frame).code() == km3::ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(fake.tx_calls, 1u);
    KM3_CHECK_EQ(fake.data_available, 0u);
}

KM3_TEST_MAIN()
