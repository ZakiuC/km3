#include "socketcan_checked.hpp"
#include "../../docs/硬件手册/PMP4010/示例/socketcan.hpp"
#include <atomic>
#include <functional>
#include <optional>
#include <vector>
#include <linux/can/netlink.h>
#include <linux/if_link.h>
#include <linux/rtnetlink.h>
#include <sys/file.h>
#include <sys/stat.h>

namespace pmp4010_cpp_test {
namespace preflight_detail {
class FileDescriptor {
public:
    explicit FileDescriptor(int fd = -1) : fd_(fd) {}
    ~FileDescriptor() { if (fd_ >= 0) ::close(fd_); }
    FileDescriptor(const FileDescriptor&) = delete;
    FileDescriptor& operator=(const FileDescriptor&) = delete;
    int Get() const { return fd_; }
private:
    int fd_;
};
[[noreturn]] void Fail(const std::string& message) {
    throw std::runtime_error("CAN 前检失败：" + message);
}
[[noreturn]] void SystemFail(const std::string& operation) {
    const int error = errno;
    Fail(operation + ": " + std::strerror(error));
}
struct Link {
    int index = 0;
    unsigned flags = 0;
    std::string name, kind;
    std::optional<std::uint32_t> state, bitrate;
};
template<class T> T Read(const unsigned char* data, std::size_t size) {
    if (size < sizeof(T)) Fail("rtnetlink 属性被截断");
    T value{};
    std::memcpy(&value, data, sizeof(value));
    return value;
}
template<class F> void Attributes(const unsigned char* data, std::size_t size, F visit) {
    while (size) {
        const auto attr = Read<rtattr>(data, size);
        if (attr.rta_len < sizeof(rtattr) || attr.rta_len > size)
            Fail("rtnetlink 属性长度无效");
        visit(attr.rta_type & NLA_TYPE_MASK, data + sizeof(rtattr), attr.rta_len - sizeof(rtattr));
        const auto step = std::size_t(RTA_ALIGN(attr.rta_len));
        if (step > size) Fail("rtnetlink 属性填充被截断");
        data += step;
        size -= step;
    }
}
std::string String(const unsigned char* data, std::size_t size) {
    if (!size || data[size - 1] != 0 || std::memchr(data, 0, size - 1))
        Fail("rtnetlink 字符串无效");
    return std::string(reinterpret_cast<const char*>(data), size - 1);
}
void CanAttributes(Link& link, const unsigned char* data, std::size_t size) {
    Attributes(data, size, [&](unsigned type, const unsigned char* value, std::size_t length) {
        if (type == IFLA_CAN_STATE) {
            if (link.state || length != sizeof(std::uint32_t)) Fail("CAN state 属性无效");
            link.state = Read<std::uint32_t>(value, length);
        } else if (type == IFLA_CAN_BITTIMING) {
            if (link.bitrate || length != sizeof(can_bittiming)) Fail("CAN bitrate 属性无效");
            link.bitrate = Read<can_bittiming>(value, length).bitrate;
        }
    });
}
Link ParseLink(const unsigned char* data, std::size_t size) {
    const auto info = Read<ifinfomsg>(data, size);
    Link link;
    link.index = info.ifi_index;
    link.flags = info.ifi_flags;
    bool got_name = false, got_link_info = false;
    Attributes(data + sizeof(info), size - sizeof(info),
        [&](unsigned type, const unsigned char* value, std::size_t length) {
        if (type == IFLA_IFNAME) {
            if (got_name) Fail("重复接口名称");
            got_name = true;
            link.name = String(value, length);
        } else if (type == IFLA_LINKINFO) {
            if (got_link_info) Fail("重复链路信息");
            got_link_info = true;
            bool got_kind = false, got_data = false;
            const unsigned char* can_data = nullptr;
            std::size_t can_size = 0;
            Attributes(value, length, [&](unsigned nested_type, const unsigned char* nested_value, std::size_t nested_size) {
                if (nested_type == IFLA_INFO_KIND) {
                    if (got_kind) Fail("重复链路类型");
                    got_kind = true;
                    link.kind = String(nested_value, nested_size);
                } else if (nested_type == IFLA_INFO_DATA) {
                    if (got_data) Fail("重复 CAN 信息");
                    got_data = true;
                    can_data = nested_value;
                    can_size = nested_size;
                }
            });
            // INFO_DATA's schema depends on KIND; attribute order is immaterial.
            if (link.kind == "can" && got_data) CanAttributes(link, can_data, can_size);
        }
    });
    return link;
}
// The dedicated unicast socket has no subscriptions and only one request.
// Unexpected sequence/port, incomplete packets and kernel errors fail closed.
std::optional<Link> ParseReply(const unsigned char* data, std::size_t size,
                               std::uint32_t sequence, std::uint32_t port,
                               std::uint32_t sender_port, int receive_flags) {
    if (sender_port != 0 || (receive_flags & (MSG_TRUNC | MSG_CTRUNC)))
        Fail("rtnetlink 来源错误或消息被截断");
    if (!size) Fail("rtnetlink 空响应");
    std::optional<Link> result;
    while (size) {
        const auto header = Read<nlmsghdr>(data, size);
        if (header.nlmsg_len < sizeof(header) || header.nlmsg_len > size)
            Fail("rtnetlink 消息长度无效");
        if (header.nlmsg_seq != sequence || header.nlmsg_pid != port)
            Fail("rtnetlink sequence/port 不匹配");
        if (header.nlmsg_flags & (NLM_F_DUMP_INTR | NLM_F_MULTI))
            Fail("意外的 rtnetlink 分段响应");
        const auto* payload = data + sizeof(header);
        const auto length = header.nlmsg_len - sizeof(header);
        if (header.nlmsg_type == NLMSG_ERROR) {
            const auto error = Read<nlmsgerr>(payload, length);
            if (error.error) {
                const auto code = error.error < 0 ? -std::int64_t(error.error) : std::int64_t(error.error);
                Fail("rtnetlink 内核错误 " + std::to_string(code));
            }
        } else if (header.nlmsg_type == RTM_NEWLINK) {
            if (result) Fail("rtnetlink 多个接口响应");
            result = ParseLink(payload, length);
        } else {
            Fail("rtnetlink 未返回完整接口信息");
        }
        const auto step = std::size_t(NLMSG_ALIGN(header.nlmsg_len));
        if (step > size) Fail("rtnetlink 消息填充被截断");
        data += step;
        size -= step;
    }
    return result;
}
void Validate(const Link& link, const std::string& name, unsigned bitrate, int expected_index = 0) {
    if (link.name != name || link.index <= 0 || (expected_index && link.index != expected_index))
        Fail("接口名称或 ifindex 身份发生变化");
    if (link.kind != "can") Fail("接口类型不是 CAN");
    if (!(link.flags & IFF_UP)) Fail("接口未 UP");
    if (!link.state) Fail("缺少 CAN state");
    if (*link.state != CAN_STATE_ERROR_ACTIVE) Fail("CAN 状态不是 ERROR-ACTIVE");
    if (!link.bitrate) Fail("缺少 CAN bitrate");
    if (!bitrate || *link.bitrate != bitrate)
        Fail("CAN bitrate 不匹配，期望 " + std::to_string(bitrate) + "，实际 " + std::to_string(*link.bitrate));
}
Link Query(const std::string& name) {
    // One deadline covers interruptions and all receive attempts. Never shell out
    // or issue RTM_NEWLINK, ioctl writes, NMT, or a CAN frame here.
    const auto end = nimotion::Clock::now() + std::chrono::milliseconds(300);
    FileDescriptor socket(::socket(AF_NETLINK, SOCK_RAW | SOCK_NONBLOCK | SOCK_CLOEXEC, NETLINK_ROUTE));
    if (socket.Get() < 0) SystemFail("rtnetlink socket");
    sockaddr_nl local{};
    local.nl_family = AF_NETLINK;
    if (::bind(socket.Get(), reinterpret_cast<sockaddr*>(&local), sizeof(local)) < 0) SystemFail("rtnetlink bind");
    socklen_t address_size = sizeof(local);
    if (::getsockname(socket.Get(), reinterpret_cast<sockaddr*>(&local), &address_size) < 0) SystemFail("rtnetlink getsockname");
    if (address_size != sizeof(local) || local.nl_family != AF_NETLINK || !local.nl_pid)
        Fail("rtnetlink 本地端口无效");
    static std::atomic<std::uint32_t> next_sequence{1};
    const auto sequence = next_sequence.fetch_add(1, std::memory_order_relaxed);
    std::vector<unsigned char> request(NLMSG_SPACE(sizeof(ifinfomsg)) + RTA_SPACE(name.size() + 1), 0);
    nlmsghdr header{};
    header.nlmsg_len = static_cast<std::uint32_t>(request.size());
    header.nlmsg_type = RTM_GETLINK;
    header.nlmsg_flags = NLM_F_REQUEST;
    header.nlmsg_seq = sequence;
    header.nlmsg_pid = local.nl_pid;
    std::memcpy(request.data(), &header, sizeof(header));
    ifinfomsg info{};
    info.ifi_family = AF_UNSPEC;
    std::memcpy(request.data() + NLMSG_HDRLEN, &info, sizeof(info));
    rtattr attr{};
    attr.rta_type = IFLA_IFNAME;
    attr.rta_len = static_cast<unsigned short>(RTA_LENGTH(name.size() + 1));
    const auto offset = NLMSG_SPACE(sizeof(info));
    std::memcpy(request.data() + offset, &attr, sizeof(attr));
    std::memcpy(request.data() + offset + sizeof(attr), name.c_str(), name.size() + 1);
    sockaddr_nl kernel{};
    kernel.nl_family = AF_NETLINK;
    const auto sent = ::sendto(socket.Get(), request.data(), request.size(), 0,
                               reinterpret_cast<sockaddr*>(&kernel), sizeof(kernel));
    if (sent < 0) SystemFail("rtnetlink sendto");
    if (static_cast<std::size_t>(sent) != request.size()) Fail("rtnetlink 请求发送不完整");
    while (nimotion::Clock::now() < end) {
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(end - nimotion::Clock::now()).count();
        pollfd wait{socket.Get(), POLLIN, 0};
        const int ready = ::poll(&wait, 1, static_cast<int>(std::max<std::int64_t>(1, remaining)));
        if (ready < 0) {
            if (errno == EINTR) continue;
            SystemFail("rtnetlink poll");
        }
        if (!ready) continue;
        if (wait.revents & (POLLERR | POLLHUP | POLLNVAL)) Fail("rtnetlink socket 异常");
        unsigned char buffer[32768];
        sockaddr_nl source{};
        iovec io{buffer, sizeof(buffer)};
        msghdr message{};
        message.msg_name = &source;
        message.msg_namelen = sizeof(source);
        message.msg_iov = &io;
        message.msg_iovlen = 1;
        const auto count = ::recvmsg(socket.Get(), &message, MSG_DONTWAIT);
        if (count < 0) {
            if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
            SystemFail("rtnetlink recvmsg");
        }
        if (message.msg_namelen != sizeof(source) || source.nl_family != AF_NETLINK)
            Fail("rtnetlink 来源地址无效");
        auto link = ParseReply(buffer, static_cast<std::size_t>(count), sequence,
                               local.nl_pid, source.nl_pid, message.msg_flags);
        if (link) return *link;
    }
    Fail("rtnetlink 查询超时（300ms）");
}
std::shared_ptr<FileDescriptor> LockFile(const std::string& path) {
    auto lock = std::make_shared<FileDescriptor>(::open(path.c_str(), O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600));
    if (lock->Get() < 0) SystemFail("打开接口锁");
    struct stat status{};
    if (::fstat(lock->Get(), &status) < 0) SystemFail("检查接口锁");
    if (!S_ISREG(status.st_mode) || status.st_nlink != 1 || status.st_uid != ::geteuid() || (status.st_mode & 0022))
        Fail("接口锁不是当前用户拥有的安全普通文件");
    if (::flock(lock->Get(), LOCK_EX | LOCK_NB) < 0)
        Fail("同接口已有本工具实例，或无法取得接口锁");
    // Keep the inode: unlinking a live/persisted lock allows two owners of
    // different inodes. Never chmod, read, write, or truncate existing files.
    return lock;
}
std::shared_ptr<FileDescriptor> Lock(const std::string& name) {
    static constexpr char digits[] = "0123456789abcdef";
    std::string path = "/tmp/pmp4010-cpp-manual-can-";
    for (unsigned char value : name) {
        path += digits[value >> 4];
        path += digits[value & 15];
    }
    return LockFile(path + ".lock");
}
using QueryFunction = std::function<Link()>;
class CheckedBus final : public nimotion::CanBus {
public:
    CheckedBus(std::string name, unsigned bitrate, int index, QueryFunction query,
               std::shared_ptr<void> lock, std::unique_ptr<nimotion::CanBus> bus)
        : name_(std::move(name)), bitrate_(bitrate), index_(index), query_(std::move(query)),
          lock_(std::move(lock)), bus_(std::move(bus)) {}
    void send(const nimotion::Frame& frame) override {
        if (failed_) Fail("通道已隔离，请退出后重新检查");
        try {
            Validate(query_(), name_, bitrate_, index_);
            bus_->send(frame);
        } catch (...) { failed_ = true; throw; }
    }
    bool receive(nimotion::Frame& frame, int timeout_ms) override {
        if (failed_) Fail("通道已隔离，请退出后重新检查");
        try { return bus_->receive(frame, timeout_ms); }
        catch (...) { failed_ = true; throw; }
    }
private:
    std::string name_;
    unsigned bitrate_;
    int index_;
    QueryFunction query_;
    std::shared_ptr<void> lock_; // Outlives bus_: SocketCan closes before unlock.
    std::unique_ptr<nimotion::CanBus> bus_;
    bool failed_ = false;
};
std::unique_ptr<nimotion::CanBus> MakeChecked(
    const std::string& name, unsigned bitrate, QueryFunction query,
    const std::function<std::shared_ptr<void>()>& acquire_lock,
    const std::function<std::unique_ptr<nimotion::CanBus>()>& make_bus) {
    if (name.empty() || name.size() >= IFNAMSIZ || name.find('\0') != std::string::npos || !bitrate)
        Fail("接口名称或期望 bitrate 无效");
    const auto first = query();
    Validate(first, name, bitrate);
    auto lock = acquire_lock();
    if (!lock) Fail("未持有接口锁");
    Validate(query(), name, bitrate, first.index);
    auto bus = make_bus();
    if (!bus) Fail("未创建 CAN 传输");
    // Catch a rename/replacement during SocketCan's own name lookup/bind.
    Validate(query(), name, bitrate, first.index);
    return std::make_unique<CheckedBus>(name, bitrate, first.index, std::move(query),
                                        std::move(lock), std::move(bus));
}
} // namespace preflight_detail
std::unique_ptr<nimotion::CanBus> MakeCheckedSocketCan(const std::string& interface, unsigned expected_bitrate) {
    return preflight_detail::MakeChecked(interface, expected_bitrate,
        [interface] { return preflight_detail::Query(interface); },
        [interface] { return preflight_detail::Lock(interface); },
        [interface] { return std::make_unique<nimotion::SocketCan>(interface); });
}
} // namespace pmp4010_cpp_test
