// Compile this source alone: including the implementation keeps its parser and
// injected test seam private to this standalone tool's public declaration.
#include "../socketcan_checked.cpp"
#include <iostream>

namespace {
using namespace pmp4010_cpp_test::preflight_detail;
using Bytes = std::vector<unsigned char>;
int assertions = 0;
void Check(bool condition, const char* message) {
    ++assertions;
    if (!condition) throw std::runtime_error(message);
}
template<class F> void Reject(F operation, const char* message) {
    bool rejected = false;
    try { operation(); } catch (const std::exception&) { rejected = true; }
    Check(rejected, message);
}
template<class T> Bytes Native(const T& value) {
    Bytes result(sizeof(value));
    std::memcpy(result.data(), &value, sizeof(value));
    return result;
}
Bytes Text(const std::string& value) {
    Bytes result(value.begin(), value.end());
    result.push_back(0);
    return result;
}
void AppendAttribute(Bytes& output, unsigned type, const Bytes& payload) {
    rtattr header{};
    header.rta_type = static_cast<unsigned short>(type);
    header.rta_len = static_cast<unsigned short>(RTA_LENGTH(payload.size()));
    const auto start = output.size();
    output.resize(start + RTA_ALIGN(header.rta_len), 0);
    std::memcpy(output.data() + start, &header, sizeof(header));
    std::memcpy(output.data() + start + sizeof(header), payload.data(), payload.size());
}
Bytes Packet(Bytes payload, unsigned type = RTM_NEWLINK, unsigned seq = 17,
             unsigned port = 123, unsigned flags = 0) {
    nlmsghdr header{};
    header.nlmsg_len = static_cast<unsigned>(NLMSG_LENGTH(payload.size()));
    header.nlmsg_type = static_cast<unsigned short>(type);
    header.nlmsg_flags = static_cast<unsigned short>(flags);
    header.nlmsg_seq = seq;
    header.nlmsg_pid = port;
    Bytes packet(NLMSG_ALIGN(header.nlmsg_len), 0);
    std::memcpy(packet.data(), &header, sizeof(header));
    std::memcpy(packet.data() + NLMSG_HDRLEN, payload.data(), payload.size());
    return packet;
}
Bytes LinkPacket(std::optional<unsigned> state = CAN_STATE_ERROR_ACTIVE,
                 std::optional<unsigned> bitrate = 1000000, unsigned flags = IFF_UP,
                 int index = 7, const std::string& kind = "can", const std::string& name = "can-test") {
    ifinfomsg info{};
    info.ifi_index = index;
    info.ifi_flags = flags;
    auto payload = Native(info);
    AppendAttribute(payload, IFLA_IFNAME, Text(name));
    Bytes can;
    if (state) AppendAttribute(can, IFLA_CAN_STATE, Native(*state));
    if (bitrate) {
        can_bittiming timing{};
        timing.bitrate = *bitrate;
        AppendAttribute(can, IFLA_CAN_BITTIMING, Native(timing));
    }
    Bytes link;
    // Deliberately put DATA before KIND: netlink attribute order is arbitrary.
    AppendAttribute(link, IFLA_INFO_DATA | NLA_F_NESTED, can);
    AppendAttribute(link, IFLA_INFO_KIND, Text(kind));
    AppendAttribute(payload, IFLA_LINKINFO | NLA_F_NESTED, link);
    return Packet(payload);
}
Link Parse(const Bytes& bytes, unsigned seq = 17, unsigned port = 123,
           unsigned sender = 0, int flags = 0) {
    auto result = ParseReply(bytes.data(), bytes.size(), seq, port, sender, flags);
    if (!result) throw std::runtime_error("missing link");
    return *result;
}
void ParserTests() {
    auto healthy = Parse(LinkPacket());
    Validate(healthy, "can-test", 1000000);
    Check(healthy.index == 7 && healthy.state && *healthy.state == 0 &&
          healthy.bitrate == 1000000, "healthy fields");
    Reject([&] { Validate(Parse(LinkPacket(std::nullopt)), "can-test", 1000000); }, "missing state accepted");
    Reject([&] { Validate(Parse(LinkPacket(0, std::nullopt)), "can-test", 1000000); }, "missing bitrate accepted");
    Reject([&] { Validate(Parse(LinkPacket(0, 500000)), "can-test", 1000000); }, "wrong bitrate accepted");
    Reject([&] { Validate(Parse(LinkPacket(0, 1000000, 0)), "can-test", 1000000); }, "down accepted");
    for (unsigned state : {CAN_STATE_ERROR_WARNING, CAN_STATE_ERROR_PASSIVE, CAN_STATE_BUS_OFF,
                           CAN_STATE_STOPPED, CAN_STATE_SLEEPING}) {
        Reject([&] { Validate(Parse(LinkPacket(state)), "can-test", 1000000); }, "unhealthy state accepted");
    }
    Reject([&] { Validate(Parse(LinkPacket(0, 1000000, IFF_UP, 8)), "can-test", 1000000, 7); }, "identity changed");
    Reject([&] { Validate(Parse(LinkPacket(0, 1000000, IFF_UP, 0)), "can-test", 1000000); }, "zero index");
    Reject([&] { Validate(Parse(LinkPacket(0, 1000000, IFF_UP, 7, "vcan")), "can-test", 1000000); }, "vcan accepted");
    Reject([&] { Validate(healthy, "other", 1000000); }, "wrong name accepted");
    Reject([&] { Parse(LinkPacket(), 18); }, "wrong sequence accepted");
    Reject([&] { Parse(LinkPacket(), 17, 124); }, "wrong destination port accepted");
    Reject([&] { Parse(LinkPacket(), 17, 123, 999); }, "non-kernel sender accepted");
    Reject([&] { Parse(LinkPacket(), 17, 123, 0, MSG_TRUNC); }, "truncation accepted");
    Reject([&] { Parse(LinkPacket(), 17, 123, 0, MSG_CTRUNC); }, "control truncation accepted");
    auto truncated = LinkPacket();
    truncated.pop_back();
    Reject([&] { Parse(truncated); }, "short payload accepted");
    Reject([&] { Parse(Bytes(3)); }, "short header accepted");
    Reject([&] { Parse(Bytes{}); }, "empty reply accepted");
    auto malformed = LinkPacket();
    nlmsghdr invalid{};
    std::memcpy(&invalid, malformed.data(), sizeof(invalid));
    invalid.nlmsg_len = sizeof(nlmsghdr) - 1;
    std::memcpy(malformed.data(), &invalid, sizeof(invalid));
    Reject([&] { Parse(malformed); }, "invalid nlmsg length accepted");
    Reject([&] { Parse(Packet(Native(ifinfomsg{}), RTM_NEWLINK, 17, 123, NLM_F_DUMP_INTR)); }, "interrupted dump accepted");
    Reject([&] { Parse(Packet(Native(ifinfomsg{}), RTM_NEWLINK, 17, 123, NLM_F_MULTI)); }, "multipart accepted");
    nlmsgerr error{};
    error.error = -ENODEV;
    Reject([&] { Parse(Packet(Native(error), NLMSG_ERROR)); }, "kernel error accepted");
    Reject([&] { Parse(Packet(Bytes(1), NLMSG_ERROR)); }, "short kernel error accepted");
    error.error = 0;
    const auto ack = Packet(Native(error), NLMSG_ERROR);
    Check(!ParseReply(ack.data(), ack.size(), 17, 123, 0, 0), "ack claimed link");
    Reject([&] { Parse(Packet({}, NLMSG_DONE)); }, "done claimed link");
    auto duplicate = LinkPacket();
    const auto extra = LinkPacket();
    duplicate.insert(duplicate.end(), extra.begin(), extra.end());
    Reject([&] { Parse(duplicate); }, "multiple links accepted");
    auto payload = Native(ifinfomsg{});
    AppendAttribute(payload, IFLA_IFNAME, Text("can-test"));
    AppendAttribute(payload, IFLA_IFNAME, Text("can-test"));
    Reject([&] { Parse(Packet(payload)); }, "duplicate name accepted");
    payload = Native(ifinfomsg{});
    AppendAttribute(payload, IFLA_IFNAME, Bytes{'b', 'a', 'd'});
    Reject([&] { Parse(Packet(payload)); }, "unterminated name accepted");
    payload = Native(ifinfomsg{});
    rtattr bad{};
    bad.rta_len = 1;
    const auto bad_attribute = Native(bad);
    payload.insert(payload.end(), bad_attribute.begin(), bad_attribute.end());
    Reject([&] { Parse(Packet(payload)); }, "bad attribute length accepted");
    payload = Native(ifinfomsg{});
    Bytes can;
    AppendAttribute(can, IFLA_CAN_STATE, Native(0u));
    AppendAttribute(can, IFLA_CAN_STATE, Native(0u));
    Bytes link;
    AppendAttribute(link, IFLA_INFO_KIND, Text("can"));
    AppendAttribute(link, IFLA_INFO_DATA, can);
    AppendAttribute(payload, IFLA_LINKINFO, link);
    Reject([&] { Parse(Packet(payload)); }, "duplicate state accepted");
}
struct Counts {
    unsigned queries = 0, locks = 0, constructs = 0, sends = 0, receives = 0, destroyed = 0;
    int receive_timeout = -1;
    bool send_error = false, receive_error = false;
};
class FakeBus final : public nimotion::CanBus {
public:
    explicit FakeBus(Counts& counts) : counts_(counts) { ++counts_.constructs; }
    ~FakeBus() override { ++counts_.destroyed; }
    void send(const nimotion::Frame&) override {
        ++counts_.sends;
        if (counts_.send_error) throw std::runtime_error("injected send failure");
    }
    bool receive(nimotion::Frame& frame, int timeout) override {
        ++counts_.receives;
        counts_.receive_timeout = timeout;
        if (counts_.receive_error) throw std::runtime_error("injected receive failure");
        frame.id = 0x581;
        return true;
    }
private:
    Counts& counts_;
};
std::unique_ptr<nimotion::CanBus> Build(Counts& counts, QueryFunction query,
                                     const std::string& name = "can-test", unsigned bitrate = 1000000) {
    return MakeChecked(name, bitrate, std::move(query), [&] {
        ++counts.locks;
        return std::make_shared<int>(1);
    }, [&] { return std::make_unique<FakeBus>(counts); });
}
void BusTests() {
    const auto healthy = Parse(LinkPacket());
    for (const auto& packet : {LinkPacket(std::nullopt), LinkPacket(0, std::nullopt),
            LinkPacket(0, 500000), LinkPacket(0, 1000000, 0), LinkPacket(CAN_STATE_BUS_OFF),
            LinkPacket(0, 1000000, IFF_UP, 7, "vcan")}) {
        Counts counts;
        Reject([&] { Build(counts, [&] { ++counts.queries; return Parse(packet); }); }, "bad preflight accepted");
        Check(counts.sends == 0 && counts.constructs == 0 && counts.locks == 0 && counts.queries == 1,
              "preflight touched transport/lock");
    }
    Counts counts;
    auto current = healthy;
    auto bus = Build(counts, [&] { ++counts.queries; return current; });
    Check(counts.queries == 3 && counts.locks == 1 && counts.constructs == 1 && !counts.sends,
          "startup query/construct counts");
    nimotion::Frame download;
    download.id = 0x601; download.size = 8; download.data[0] = 0x23;
    bus->send(download);
    bus->send(download);
    Check(counts.queries == 5 && counts.sends == 2, "download not checked every send");
    nimotion::Frame reply;
    Check(bus->receive(reply, 47) && reply.id == 0x581 && counts.receive_timeout == 47 && counts.queries == 5,
          "receive forwarding changed");
    ++current.index;
    Reject([&] { bus->send(download); }, "changed interface sent");
    Check(counts.sends == 2 && counts.queries == 6, "failure forwarded frame");
    current = healthy;
    Reject([&] { bus->send(download); }, "isolated bus recovered automatically");
    Reject([&] { bus->receive(reply, 0); }, "isolated receive forwarded");
    Check(counts.sends == 2 && counts.queries == 6 && counts.receives == 1, "isolated bus reused IO");
    bus.reset();
    Check(counts.destroyed == 1, "underlying bus not closed");
    for (unsigned failure_phase : {2u, 3u}) {
        Counts changed;
        Reject([&] { Build(changed, [&] {
            ++changed.queries;
            auto link = healthy;
            if (changed.queries == failure_phase) ++link.index;
            return link;
        }); }, "identity change during construction accepted");
        Check(changed.sends == 0 && changed.constructs == failure_phase - 2 &&
              changed.destroyed == changed.constructs, "construction race sent or leaked bus");
    }
    Counts locked;
    Reject([&] { MakeChecked("can-test", 1000000, [&] { ++locked.queries; return healthy; },
        []() -> std::shared_ptr<void> { throw std::runtime_error("busy"); },
        [&] { return std::make_unique<FakeBus>(locked); }); }, "lock failure accepted");
    Check(locked.constructs == 0 && locked.sends == 0, "lock failure created transport");
    Counts invalid;
    Reject([&] { Build(invalid, [&] { ++invalid.queries; return healthy; }, ""); }, "empty interface accepted");
    Reject([&] { Build(invalid, [&] { ++invalid.queries; return healthy; }, std::string("can\0bad", 7)); }, "embedded NUL accepted");
    Reject([&] { Build(invalid, [&] { ++invalid.queries; return healthy; }, "can-test", 0); }, "zero bitrate accepted");
    Check(invalid.queries == 0 && invalid.constructs == 0, "invalid config performed IO");
    Counts transport;
    auto broken = Build(transport, [&] { ++transport.queries; return healthy; });
    transport.send_error = true;
    Reject([&] { broken->send(download); }, "transport send exception swallowed");
    transport.send_error = false;
    Reject([&] { broken->send(download); }, "transport failure not isolated");
    Check(transport.sends == 1 && transport.queries == 4, "failed send retried");
    for (const auto& packet : {LinkPacket(std::nullopt), LinkPacket(0, std::nullopt),
            LinkPacket(0, 500000), LinkPacket(0, 1000000, 0), LinkPacket(CAN_STATE_BUS_OFF)}) {
        Counts runtime;
        auto link = healthy;
        auto checked = Build(runtime, [&] { ++runtime.queries; return link; });
        link = Parse(packet);
        Reject([&] { checked->send(download); }, "runtime preflight failure accepted");
        link = healthy;
        Reject([&] { checked->send(download); }, "runtime preflight failure recovered");
        Check(runtime.sends == 0 && runtime.queries == 4, "runtime failure forwarded/requeried");
    }
    Counts receiving;
    auto receiver = Build(receiving, [&] { ++receiving.queries; return healthy; });
    receiving.receive_error = true;
    Reject([&] { receiver->receive(reply, 0); }, "receive exception swallowed");
    Reject([&] { receiver->send(download); }, "receive failure not isolated");
    Check(receiving.sends == 0 && receiving.queries == 3, "receive failure allowed sends");
    Counts lifetime;
    bool unlocked = false, closed_before_unlock = false;
    auto holding = MakeChecked("can-test", 1000000, [&] { return healthy; }, [&] {
        return std::shared_ptr<void>(new int(1), [&](void* value) {
            closed_before_unlock = lifetime.destroyed == 1;
            unlocked = true;
            delete static_cast<int*>(value);
        });
    }, [&] { return std::make_unique<FakeBus>(lifetime); });
    Check(!unlocked, "interface lock released before bus destruction");
    holding.reset();
    Check(unlocked && closed_before_unlock, "interface unlocked before SocketCan closes");
}
void LockTests() {
    char pattern[] = "/tmp/pmp4010-preflight-test-XXXXXX";
    char* created = ::mkdtemp(pattern);
    if (!created) throw std::runtime_error("mkdtemp");
    struct Cleanup {
        std::string directory;
        ~Cleanup() {
            for (const auto* name : {"/lock", "/alias", "/symlink", "/fifo", "/unsafe"})
                ::unlink((directory + name).c_str());
            ::rmdir(directory.c_str());
        }
    } cleanup{created};
    const auto path = cleanup.directory + "/lock";
    auto first = LockFile(path);
    struct stat original{};
    Check(::stat(path.c_str(), &original) == 0 && S_ISREG(original.st_mode) &&
          !(original.st_mode & 0077), "lock file permissions");
    Reject([&] { LockFile(path); }, "duplicate tool lock acquired");
    first.reset();
    auto second = LockFile(path);
    struct stat reused{};
    Check(::stat(path.c_str(), &reused) == 0 && reused.st_ino == original.st_ino &&
          reused.st_mode == original.st_mode && reused.st_size == original.st_size,
          "existing lock replaced or modified");
    second.reset();
    const auto symlink = cleanup.directory + "/symlink";
    Check(::symlink(path.c_str(), symlink.c_str()) == 0, "create test symlink");
    Reject([&] { LockFile(symlink); }, "symlink lock followed");
    const auto alias = cleanup.directory + "/alias";
    Check(::link(path.c_str(), alias.c_str()) == 0, "create hardlink");
    Reject([&] { LockFile(path); }, "hardlink lock accepted");
    const auto fifo = cleanup.directory + "/fifo";
    Check(::mkfifo(fifo.c_str(), 0600) == 0, "create fifo");
    Reject([&] { LockFile(fifo); }, "non-regular lock accepted");
    const auto unsafe = cleanup.directory + "/unsafe";
    FileDescriptor descriptor(::open(unsafe.c_str(), O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600));
    Check(descriptor.Get() >= 0 && ::fchmod(descriptor.Get(), 0620) == 0, "prepare unsafe test lock");
    Reject([&] { LockFile(unsafe); }, "writable-by-group lock accepted");
    struct stat unchanged{};
    Check(::fstat(descriptor.Get(), &unchanged) == 0 && (unchanged.st_mode & 0777) == 0620,
          "existing unsafe file permissions changed");
}
} // namespace
int main() {
    try {
        ParserTests();
        BusTests();
        LockTests();
        std::cout << "bus_preflight_tests: " << assertions << " assertions passed; fake rtnetlink and transport only\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "bus_preflight_tests failed after " << assertions << " assertions: " << error.what() << '\n';
        return 1;
    }
}
