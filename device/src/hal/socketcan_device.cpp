#include "km3/hal/socketcan_device.hpp"

#include <algorithm>
#include <cerrno>
#include <climits>
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
#include <unistd.h>

#include "km3/clock.hpp"

namespace km3 {
namespace {

ErrorCode IoError(int error) noexcept {
    if (error == EPERM || error == EACCES)
        return ErrorCode::EPermission;
    if (error == EAGAIN || error == ENOBUFS)
        return ErrorCode::EDeviceBusy;
    if (error == ENODEV || error == ENETDOWN || error == ENXIO)
        return ErrorCode::EDeviceOffline;
    return ErrorCode::EDeviceError;
}

// Bounded rtnetlink exchange, non-RT only. Never change link flags or bitrate.
Status ExchangeLink(int ifindex, bool restart, void* response, std::size_t capacity,
                    std::size_t& received, int& system_error) noexcept {
    system_error = 0;
    const int sock = ::socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, NETLINK_ROUTE);
    if (sock < 0) {
        system_error = errno;
        return Status(IoError(errno));
    }
    struct Request {
        nlmsghdr header;
        ifinfomsg link;
        std::uint8_t attrs[64];
    } request{};
    request.header.nlmsg_len = NLMSG_LENGTH(sizeof(ifinfomsg));
    request.header.nlmsg_type = restart ? RTM_NEWLINK : RTM_GETLINK;
    request.header.nlmsg_flags =
        static_cast<std::uint16_t>(NLM_F_REQUEST | (restart ? NLM_F_ACK : 0));
    request.header.nlmsg_seq = 1;
    request.link.ifi_family = AF_UNSPEC;
    request.link.ifi_index = ifindex;
    if (restart) {
        // IFLA_LINKINFO { IFLA_INFO_KIND="can", IFLA_INFO_DATA { CAN_RESTART=1 } }
        auto* base = reinterpret_cast<std::uint8_t*>(&request);
        auto append = [&](std::uint16_t type, const void* data, std::size_t size) {
            auto* attr = reinterpret_cast<rtattr*>(base + NLMSG_ALIGN(request.header.nlmsg_len));
            attr->rta_type = type;
            attr->rta_len = static_cast<std::uint16_t>(RTA_LENGTH(size));
            if (size != 0)
                std::memcpy(RTA_DATA(attr), data, size);
            request.header.nlmsg_len = static_cast<std::uint32_t>(
                NLMSG_ALIGN(request.header.nlmsg_len) + RTA_ALIGN(attr->rta_len));
            return attr;
        };
        auto* link = append(IFLA_LINKINFO, nullptr, 0);
        append(IFLA_INFO_KIND, "can", 4);
        auto* data = append(IFLA_INFO_DATA, nullptr, 0);
        const std::uint32_t one = 1;
        append(IFLA_CAN_RESTART, &one, sizeof(one));
        data->rta_len = static_cast<std::uint16_t>(base + request.header.nlmsg_len -
                                                   reinterpret_cast<std::uint8_t*>(data));
        link->rta_len = static_cast<std::uint16_t>(base + request.header.nlmsg_len -
                                                   reinterpret_cast<std::uint8_t*>(link));
    }
    sockaddr_nl kernel{};
    kernel.nl_family = AF_NETLINK;
    ErrorCode code = ErrorCode::Ok;
    if (::sendto(sock, &request, request.header.nlmsg_len, 0, reinterpret_cast<sockaddr*>(&kernel),
                 sizeof(kernel)) < 0) {
        system_error = errno;
        code = IoError(errno);
    } else {
        const auto deadline = MonotonicUs() + 100000;
        for (;;) {
            const auto now = MonotonicUs();
            if (now >= deadline) {
                system_error = ETIMEDOUT;
                code = ErrorCode::ECommandTimeout;
                break;
            }
            pollfd pfd{sock, POLLIN, 0};
            const int wait_ms = static_cast<int>((deadline - now + 999) / 1000);
            const int ready = ::poll(&pfd, 1, wait_ms);
            if (ready < 0 && errno == EINTR)
                continue;
            if (ready <= 0) {
                system_error = ready == 0 ? ETIMEDOUT : errno;
                code = ready == 0 ? ErrorCode::ECommandTimeout : IoError(errno);
                break;
            }
            iovec io{response, capacity};
            sockaddr_nl sender{};
            msghdr msg{};
            msg.msg_name = &sender;
            msg.msg_namelen = sizeof(sender);
            msg.msg_iov = &io;
            msg.msg_iovlen = 1;
            const auto count = ::recvmsg(sock, &msg, MSG_DONTWAIT);
            if (count < 0 && (errno == EINTR || errno == EAGAIN))
                continue;
            if (count < 0) {
                system_error = errno;
                code = IoError(errno);
                break;
            }
            if ((msg.msg_flags & MSG_TRUNC) != 0 || sender.nl_pid != 0 ||
                count < static_cast<ssize_t>(sizeof(nlmsghdr))) {
                code = ErrorCode::EDeviceError;
                break;
            }
            const auto* header = static_cast<const nlmsghdr*>(response);
            if (header->nlmsg_len < sizeof(nlmsghdr) ||
                header->nlmsg_len > static_cast<std::size_t>(count)) {
                system_error = EPROTO;
                code = ErrorCode::EDeviceError;
                break;
            }
            if (header->nlmsg_seq != 1)
                continue;
            if (header->nlmsg_type == NLMSG_ERROR) {
                if (header->nlmsg_len < NLMSG_LENGTH(sizeof(nlmsgerr))) {
                    code = ErrorCode::EDeviceError;
                } else {
                    const auto* error = static_cast<const nlmsgerr*>(NLMSG_DATA(header));
                    system_error = -error->error;
                    code = error->error == 0 ? ErrorCode::Ok : IoError(-error->error);
                }
            } else if (restart) {
                continue;  // Recovery requires the explicit ACK.
            }
            received = static_cast<std::size_t>(count);
            break;
        }
    }
    ::close(sock);
    return Status(code);
}

// Iterate nested attributes with length checks before every access.
template <typename Fn>
bool WalkAttrs(const void* bytes, std::size_t length, Fn fn) noexcept {
    const auto* pos = static_cast<const std::uint8_t*>(bytes);
    while (length != 0) {
        if (length < sizeof(rtattr))
            return false;
        const auto* attr = reinterpret_cast<const rtattr*>(pos);
        if (attr->rta_len < sizeof(rtattr) || attr->rta_len > length)
            return false;
        const auto size = static_cast<std::size_t>(attr->rta_len - sizeof(rtattr));
        if (!fn(static_cast<unsigned>(attr->rta_type & NLA_TYPE_MASK), pos + sizeof(rtattr), size))
            return false;
        const auto step = static_cast<std::size_t>(RTA_ALIGN(attr->rta_len));
        if (step > length)
            return attr->rta_len == length;
        pos += step;
        length -= step;
    }
    return true;
}

template <typename T>
bool CopyValue(const void* p, std::size_t length, T& out) noexcept {
    if (length < sizeof(T))
        return false;
    std::memcpy(&out, p, sizeof(T));
    return true;
}

}  // namespace

SocketCanDevice::SocketCanDevice(BusEndpoint endpoint, bool confirm_tx)
    : endpoint_(std::move(endpoint)), confirm_tx_(confirm_tx) {}
SocketCanDevice::~SocketCanDevice() {
    Close();
}

Status SocketCanDevice::Encode(const CanFrame& frame, struct can_frame& raw) noexcept {
    // FD on a classic SocketCAN backend is a wiring/backend error; never downgrade.
    if (frame.fd || frame.bitrate_switch || !frame.IsDlcValid() ||
        frame.id > (frame.extended ? CAN_EFF_MASK : CAN_SFF_MASK)) {
        return Status(ErrorCode::EInvalidRequest);
    }
    raw = {};
    raw.can_id =
        frame.id | (frame.extended ? CAN_EFF_FLAG : 0u) | (frame.remote ? CAN_RTR_FLAG : 0u);
    raw.can_dlc = frame.dlc;  // Validate original dlc before touching the wire.
    if (!frame.remote)
        std::copy_n(frame.data.begin(), frame.dlc, raw.data);
    return Status::Ok();
}

Result<CanFrame> SocketCanDevice::Decode(const struct can_frame& raw) noexcept {
    if ((raw.can_id & CAN_ERR_FLAG) != 0 || raw.can_dlc > CAN_MAX_DLEN ||
        ((raw.can_id & CAN_EFF_FLAG) == 0 && (raw.can_id & CAN_EFF_MASK) > CAN_SFF_MASK)) {
        return Result<CanFrame>::Err(ErrorCode::EInvalidRequest);
    }
    CanFrame frame{};
    frame.extended = (raw.can_id & CAN_EFF_FLAG) != 0;
    frame.remote = (raw.can_id & CAN_RTR_FLAG) != 0;
    frame.id = raw.can_id & (frame.extended ? CAN_EFF_MASK : CAN_SFF_MASK);
    frame.dlc = raw.can_dlc;
    if (!frame.remote)
        std::copy_n(raw.data, raw.can_dlc, frame.data.begin());
    return Result<CanFrame>::Ok(frame);
}

Status SocketCanDevice::ParseLinkMessage(const void* bytes, std::size_t length, int ifindex,
                                         SocketCanLinkInfo& info) noexcept {
    if (bytes == nullptr || reinterpret_cast<std::uintptr_t>(bytes) % alignof(nlmsghdr) != 0)
        return Status(ErrorCode::EInvalidRequest);
    if (length < NLMSG_LENGTH(sizeof(ifinfomsg)))
        return Status(ErrorCode::EDeviceError);
    const auto* msg = static_cast<const nlmsghdr*>(bytes);
    if (msg->nlmsg_type != RTM_NEWLINK || msg->nlmsg_len > length ||
        msg->nlmsg_len < NLMSG_LENGTH(sizeof(ifinfomsg)))
        return Status(ErrorCode::EDeviceError);
    const auto* link = static_cast<const ifinfomsg*>(NLMSG_DATA(msg));
    if (link->ifi_index != ifindex)
        return Status(ErrorCode::EDeviceError);
    SocketCanLinkInfo parsed{};
    parsed.up = (link->ifi_flags & IFF_UP) != 0;
    parsed.driver_echo = (link->ifi_flags & IFF_ECHO) != 0;
    const bool valid = WalkAttrs(
        IFLA_RTA(link), IFLA_PAYLOAD(msg), [&](unsigned type, const void* p, std::size_t n) {
            if (type != IFLA_LINKINFO)
                return true;
            return WalkAttrs(p, n, [&](unsigned nested, const void* q, std::size_t size) {
                if (nested == IFLA_INFO_KIND) {
                    parsed.is_can = size == 4 && std::memcmp(q, "can", 4) == 0;
                } else if (nested == IFLA_INFO_XSTATS) {
                    can_device_stats counters{};
                    if (!CopyValue(q, size, counters))
                        return false;
                    parsed.has_stats = true;
                    parsed.bus_off = counters.bus_off;
                    parsed.bus_error = counters.bus_error;
                    parsed.error_warning = counters.error_warning;
                    parsed.error_passive = counters.error_passive;
                    parsed.restarts = counters.restarts;
                } else if (nested == IFLA_INFO_DATA) {
                    return WalkAttrs(q, size,
                                     [&](unsigned field, const void* value, std::size_t len) {
                                         if (field == IFLA_CAN_BITTIMING) {
                                             can_bittiming timing{};
                                             if (!CopyValue(value, len, timing))
                                                 return false;
                                             parsed.has_bitrate = true;
                                             parsed.bitrate = timing.bitrate;
                                         } else if (field == IFLA_CAN_STATE) {
                                             if (!CopyValue(value, len, parsed.state))
                                                 return false;
                                             parsed.has_state = true;
                                         } else if (field == IFLA_CAN_BERR_COUNTER) {
                                             can_berr_counter counters{};
                                             if (!CopyValue(value, len, counters))
                                                 return false;
                                             parsed.tx_error_counter = counters.txerr;
                                             parsed.rx_error_counter = counters.rxerr;
                                         }
                                         return true;
                                     });
                }
                return true;
            });
        });
    if (!valid || !parsed.is_can)
        return Status(ErrorCode::EDeviceError);
    info = parsed;
    return Status::Ok();
}

Status SocketCanDevice::RefreshLink() const noexcept {
    alignas(nlmsghdr) std::uint8_t bytes[8192]{};
    std::size_t length = 0;
    int system_error = 0;
    auto result = ExchangeLink(ifindex_, false, bytes, sizeof(bytes), length, system_error);
    if (system_error != 0)
        system_error_ = system_error;
    SocketCanLinkInfo info{};
    if (result.ok())
        result = ParseLinkMessage(bytes, length, ifindex_, info);
    link_error_ = result.code();
    if (!result.ok())
        return result;
    link_info_ = info;
    if (info.has_stats)
        stats_.bus_off_count = std::max(stats_.bus_off_count, std::uint64_t{info.bus_off});
    if (!info.up)
        state_ = DeviceStatusEnum::Error;
    else if (info.has_state && info.state == CAN_STATE_BUS_OFF)
        state_ = DeviceStatusEnum::BusOff;
    else if (info.has_state && info.state >= CAN_STATE_STOPPED)
        state_ = DeviceStatusEnum::Error;
    else
        state_ = DeviceStatusEnum::Open;
    return Status::Ok();
}

Status SocketCanDevice::Open() noexcept {
    if (IsOpen())
        return Status::Ok();
    if (endpoint_.logical.empty() || endpoint_.device.empty() ||
        endpoint_.device.size() >= IFNAMSIZ || endpoint_.baudrate == 0 ||
        endpoint_.baudrate > Capabilities().max_bitrate || endpoint_.protocol == "canfd") {
        return Status(ErrorCode::EInvalidConfig);
    }
    ifindex_ = static_cast<int>(::if_nametoindex(endpoint_.device.c_str()));
    if (ifindex_ == 0) {
        system_error_ = errno;
        return Status(ErrorCode::ENotFound);
    }
    auto link = RefreshLink();
    if (!link.ok())
        return link;
    if (!link_info_.has_bitrate || link_info_.bitrate != endpoint_.baudrate ||
        !link_info_.has_state || !link_info_.up)
        return Status(ErrorCode::EInvalidConfig);
    if (confirm_tx_ && (!link_info_.driver_echo || !filters_.empty()))
        return Status(ErrorCode::EInvalidConfig);
    const int sock = ::socket(PF_CAN, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, CAN_RAW);
    if (sock < 0) {
        system_error_ = errno;
        return Status(IoError(errno));
    }
    const can_err_mask_t errors = CAN_ERR_MASK;
    if (::setsockopt(sock, SOL_CAN_RAW, CAN_RAW_ERR_FILTER, &errors, sizeof(errors)) != 0) {
        system_error_ = errno;
        const auto code = IoError(errno);
        ::close(sock);
        return Status(code);
    }
    // 关掉本机回灌。内核默认是**开**（CAN_RAW_LOOPBACK = 1），而本类只做收发，
    // 不回看自己发的帧：开着它，每一帧都要多走一次内核回灌，纯属浪费。
    //
    // 更重要的是**取证后果**：loopback 开着时，本机 `candump` 能看到自己发出的
    // 帧，因此「candump 里有 0x080」**不能**证明该帧上过物理总线 —— 它可能只是
    // 写进 socket 就被回灌了。关掉之后，本地看不到自己的帧，
    // 再配合 `ip -statistics` 的 TX 计数增量（CAN 上只有被 ACK 的帧才计入），
    // 才能把「发出去了」和「真上了总线」分开。
    //
    // 与 Python 侧 tools/hwtui/canbus.py 的 `loopback=False, receive_own_messages=False`
    // 保持同一语义（那两个选项是两件事：前者控控制器是否回灌，后者控本套接字
    // 是否收它）。本类要正常收别的节点，因此只关 LOOPBACK，
    // 不设 CAN_RAW_RECV_OWN_MSGS —— 后者本就不影响回灌。
    // Explicit PDO completion mode requires IFF_ECHO: reject software fallback
    // echo, which only proves enqueue. Pure SYNC/default keeps loopback disabled.
    const int loopback = confirm_tx_ ? 1 : 0;
    if (::setsockopt(sock, SOL_CAN_RAW, CAN_RAW_LOOPBACK, &loopback, sizeof(loopback)) != 0) {
        system_error_ = errno;
        const auto code = IoError(errno);
        ::close(sock);
        return Status(code);
    }
    if (confirm_tx_) {
        const int own = 1;
        if (::setsockopt(sock, SOL_CAN_RAW, CAN_RAW_RECV_OWN_MSGS, &own, sizeof(own)) != 0) {
            system_error_ = errno;
            ::close(sock);
            return Status(ErrorCode::EDeviceError);
        }
    }
    sockaddr_can addr{};
    addr.can_family = AF_CAN;
    addr.can_ifindex = ifindex_;
    if (::bind(sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        system_error_ = errno;
        const auto code = IoError(errno);
        ::close(sock);
        return Status(code);
    }
    fd_ = sock;
    tx_quarantined_ = false;
    pending_head_ = pending_count_ = 0;
    // Empty filter list means receive all (same as MockCanDevice), including errors.
    if (!filters_.empty()) {
        auto status = SetFilters(filters_);
        if (!status.ok()) {
            Close();
            return status;
        }
    }
    return Status::Ok();
}

Status SocketCanDevice::Close() noexcept {
    int error = 0;
    if (fd_ >= 0 && ::close(fd_) < 0)
        error = errno;
    fd_ = -1;
    state_ = DeviceStatusEnum::Closed;
    return error == 0 ? Status::Ok() : Status(IoError(error));
}

Status SocketCanDevice::RecordTxError(ErrorCode code) noexcept {
    ++stats_.tx_errors;
    stats_.last_error_us = MonotonicUs();
    return Status(code);
}

Status SocketCanDevice::Send(const CanFrame& frame) noexcept {
    if (confirm_tx_ || tx_quarantined_)
        return RecordTxError(ErrorCode::ECommandRejected);
    return SendRaw(frame);
}

Status SocketCanDevice::SendRaw(const CanFrame& frame) noexcept {
    if (tx_quarantined_)
        return RecordTxError(ErrorCode::ECommandRejected);
    struct can_frame raw {};
    const auto encoded = Encode(frame, raw);
    if (!encoded.ok())
        return RecordTxError(encoded.code());
    if (!IsOpen())
        return RecordTxError(ErrorCode::EDeviceOffline);
    // A single nonblocking syscall: no retries, no waiting on a full transmit queue.
    const auto count = ::send(fd_, &raw, sizeof(raw), MSG_DONTWAIT | MSG_NOSIGNAL);
    if (count != static_cast<ssize_t>(sizeof(raw))) {
        system_error_ = count < 0 ? errno : EIO;
        return RecordTxError(count < 0 ? IoError(system_error_) : ErrorCode::EDeviceError);
    }
    ++stats_.tx_frames;
    last_ok_us_ = MonotonicUs();
    return Status::Ok();
}

Status SocketCanDevice::SendConfirmed(const CanFrame& frame, std::uint64_t deadline_us) noexcept {
    if (!confirm_tx_ || tx_quarantined_)
        return Status(ErrorCode::ECommandRejected);
    if (MonotonicUs() >= deadline_us)
        return Status(ErrorCode::ECommandTimeout);
    struct can_frame expected {};
    const auto encoded = Encode(frame, expected);
    if (!encoded)
        return encoded;
    const auto sent = SendRaw(frame);
    if (!sent)
        return sent;
    // One outstanding frame only; confirm SYNC too, otherwise its delayed echo
    // could be mistaken for later traffic. Timeout/overflow quarantines this
    // socket until close/reopen; payload equality is not a transaction number.
    tx_quarantined_ = true;
    for (unsigned budget = 0; budget < 256 && MonotonicUs() < deadline_us; ++budget) {
        struct can_frame raw {};
        iovec iov{&raw, sizeof(raw)};
        msghdr msg{};
        msg.msg_iov = &iov;
        msg.msg_iovlen = 1;
        const auto received = ::recvmsg(fd_, &msg, MSG_DONTWAIT | MSG_TRUNC);
        if (received < 0 && (errno == EAGAIN || errno == EINTR)) {
            const auto now = MonotonicUs();
            if (now >= deadline_us)
                break;
            pollfd pfd{fd_, POLLIN, 0};
            const auto wait_ms =
                static_cast<int>(std::min<std::uint64_t>((deadline_us - now + 999) / 1000, 1));
            if (::poll(&pfd, 1, wait_ms) < 0 && errno != EINTR)
                return RecordTxError(ErrorCode::EDeviceError);
            continue;
        }
        if (received != static_cast<ssize_t>(sizeof(raw)) ||
            (msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) != 0)
            return RecordTxError(ErrorCode::EDeviceError);
        if ((raw.can_id & CAN_ERR_FLAG) != 0) {
            ObserveError(raw);
            return RecordTxError(ErrorCode::EDeviceError);
        }
        if ((msg.msg_flags & MSG_CONFIRM) != 0) {
            if (raw.can_id == expected.can_id && raw.can_dlc == expected.can_dlc &&
                std::memcmp(raw.data, expected.data, expected.can_dlc) == 0) {
                tx_quarantined_ = false;
                return Status::Ok();
            }
            return RecordTxError(ErrorCode::EDeviceError);
        }
        // Other sockets' local echo is never an ACK. Preserve actual receive
        // traffic, including TPDO received while waiting for TX completion.
        const auto decoded = Decode(raw);
        if (!decoded || pending_count_ == pending_rx_.size())
            return RecordTxError(ErrorCode::EDeviceError);
        pending_rx_[(pending_head_ + pending_count_) % pending_rx_.size()] = decoded.value();
        ++pending_count_;
        ++stats_.rx_frames;
    }
    return RecordTxError(ErrorCode::ECommandTimeout);
}

void SocketCanDevice::ObserveError(const struct can_frame& raw) noexcept {
    if ((raw.can_id & CAN_ERR_FLAG) == 0)
        return;
    ++stats_.rx_errors;
    stats_.last_error_us = MonotonicUs();
    if ((raw.can_id & CAN_ERR_BUSOFF) != 0) {
        ++observed_bus_off_;
        stats_.bus_off_count = std::max(stats_.bus_off_count, observed_bus_off_);
        state_ = DeviceStatusEnum::BusOff;
    } else if ((raw.can_id & CAN_ERR_RESTARTED) != 0) {
        state_ = IsOpen() ? DeviceStatusEnum::Open : DeviceStatusEnum::Closed;
    }
}

Result<CanFrame> SocketCanDevice::Receive(int timeout_ms) {
    if (pending_count_ != 0) {
        const auto frame = pending_rx_[pending_head_];
        pending_head_ = (pending_head_ + 1) % pending_rx_.size();
        --pending_count_;
        return Result<CanFrame>::Ok(frame);
    }
    if (timeout_ms < 0)
        return Result<CanFrame>::Err(ErrorCode::EInvalidRequest);
    if (!IsOpen())
        return Result<CanFrame>::Err(ErrorCode::EDeviceOffline);
    const auto deadline = MonotonicUs() + static_cast<std::uint64_t>(timeout_ms) * 1000;
    for (;;) {
        const auto now = MonotonicUs();
        const auto remaining = deadline > now ? (deadline - now + 999) / 1000 : 0;
        pollfd pfd{fd_, POLLIN, 0};
        const auto ready =
            ::poll(&pfd, 1, static_cast<int>(std::min<std::uint64_t>(remaining, INT_MAX)));
        if (ready < 0 && errno == EINTR && MonotonicUs() < deadline)
            continue;
        if (ready == 0 || (ready < 0 && errno == EINTR))
            return Result<CanFrame>::Err(ErrorCode::ECommandTimeout);
        if (ready < 0 ||
            ((pfd.revents & POLLIN) == 0 && (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0)) {
            ++stats_.rx_errors;
            stats_.last_error_us = MonotonicUs();
            return Result<CanFrame>::Err(ready < 0 ? IoError(errno) : ErrorCode::EDeviceError);
        }
        struct can_frame raw {};
        const auto count = ::recv(fd_, &raw, sizeof(raw), MSG_DONTWAIT | MSG_TRUNC);
        if (count < 0 && (errno == EAGAIN || errno == EINTR)) {
            if (MonotonicUs() < deadline)
                continue;
            return Result<CanFrame>::Err(ErrorCode::ECommandTimeout);
        }
        if (count != static_cast<ssize_t>(sizeof(raw))) {
            ++stats_.rx_errors;
            stats_.last_error_us = MonotonicUs();
            return Result<CanFrame>::Err(count < 0 ? IoError(errno) : ErrorCode::EDeviceError);
        }
        if ((raw.can_id & CAN_ERR_FLAG) != 0) {
            ObserveError(raw);
            // One notification per poll when timeout=0: bounded even under error storms.
            if (timeout_ms == 0 || MonotonicUs() >= deadline)
                return Result<CanFrame>::Err(ErrorCode::EDeviceError);
            continue;
        }
        auto result = Decode(raw);
        if (result.ok()) {
            ++stats_.rx_frames;
            last_ok_us_ = MonotonicUs();
        } else {
            ++stats_.rx_errors;
            stats_.last_error_us = MonotonicUs();
        }
        return result;
    }
}

Status SocketCanDevice::SetFilters(const std::vector<CanFilter>& filters) {
    if ((confirm_tx_ && !filters.empty()) || filters.size() > 512)
        return Status(ErrorCode::EInvalidRequest);
    std::vector<struct can_filter> raw;
    raw.reserve(std::max<std::size_t>(1, filters.size()));
    for (const auto& filter : filters) raw.push_back({filter.id, filter.mask});
    if (raw.empty())
        raw.push_back({0, 0});
    if (IsOpen() && ::setsockopt(fd_, SOL_CAN_RAW, CAN_RAW_FILTER, raw.data(),
                                 static_cast<socklen_t>(raw.size() * sizeof(raw[0]))) != 0) {
        system_error_ = errno;
        return Status(IoError(errno));
    }
    filters_ = filters;
    return Status::Ok();
}

CanBusStats SocketCanDevice::Stats() const {
    if (IsOpen())
        RefreshLink();
    return stats_;
}

DeviceStatus SocketCanDevice::StatusOf() const {
    const auto stats = Stats();
    DeviceStatus result{};
    result.state = IsOpen() ? state_ : DeviceStatusEnum::Closed;
    result.last_ok_us = last_ok_us_;
    result.error_count = stats.tx_errors + stats.rx_errors;
    if (link_error_ != ErrorCode::Ok)
        result.detail = "SocketCAN netlink telemetry unavailable";
    else if (state_ == DeviceStatusEnum::BusOff)
        result.detail = "SocketCAN bus off";
    return result;
}

Status SocketCanDevice::RecoverFromBusOff() {
    if (!IsOpen())
        return Status(ErrorCode::EDeviceOffline);
    auto status = RefreshLink();
    if (!status.ok())
        return status;
    if (state_ != DeviceStatusEnum::BusOff)
        return Status(ErrorCode::ECommandRejected);
    alignas(nlmsghdr) std::uint8_t bytes[512]{};
    std::size_t length = 0;
    int system_error = 0;
    status = ExchangeLink(ifindex_, true, bytes, sizeof(bytes), length, system_error);
    if (system_error != 0)
        system_error_ = system_error;
    if (!status.ok())
        return status;  // EPERM is explicit; never shell out or flap link.
    status = RefreshLink();
    if (!status.ok())
        return status;
    return state_ == DeviceStatusEnum::Open ? Status::Ok() : Status(ErrorCode::EDeviceError);
}

}  // namespace km3
