// ISOLATED FIXTURE ONLY: this executable defines PMP4010_OD_VERIFIED=1.
// No SocketCAN implementation or hardware is linked. Production OD is unchanged.
#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <new>
#include "../driver/pmp4010_driver.hpp"
#include "../driver/od_access_generated.hpp"
#include "canopen_vectors_generated.hpp"
#include "test_harness.hpp"

static bool count_allocations = false;
static unsigned allocations = 0;
void* operator new(std::size_t size) {
    if (count_allocations) ++allocations;
    if (auto* p = std::malloc(size == 0 ? 1 : size)) return p;
    throw std::bad_alloc();
}
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }

namespace {
using namespace km3;
using namespace km3::pmp4010;

// Fixed storage deliberately excludes the allocating MockCanDevice. Replies are
// injected by Send, so the stale-queue drain cannot accidentally consume them.
class FakeCan final : public ICanDevice {
public:
    std::string name = "can-2";
    bool open = true;
    bool scripted = false, mismatch = false, silence = false, noise = false;
    bool malformed = false, bad_echo = false, wrong_abort = false;
    bool hold_state = false, stale_home = false, home_error = false;
    std::uint16_t sw = 0x40, control = 0, selector = 0;
    std::uint8_t mode = 1, homing = 17;
    std::uint32_t position = 0xFFFFFF9Cu, velocity = 0x80000000u;
    std::int32_t lower_limit = -65535, upper_limit = 65535;
    std::uint16_t abort_index = 0, silent_index = 0;
    int abort_sub = -1;
    std::uint32_t abort_code = 0;
    std::uint16_t fail_write = 0;
    std::uint16_t lose_control_ack = 0xFFFFu;
    std::uint16_t abort_control = 0xFFFFu;
    unsigned homing_reads = 0;
    const golden::Case* golden_case = nullptr;
    std::size_t cursor = 0, sent_count = 0;
    std::array<CanFrame, 2048> sent{};
    std::array<CanFrame, 512> queue{};
    std::size_t head = 0, tail = 0;
    CanBackend Backend() const noexcept override { return CanBackend::SocketCan; }
    CanCapabilities Capabilities() const override { return {}; }
    const std::string& LogicalName() const noexcept override { return name; }
    Status Open() noexcept override { open = true; return Status::Ok(); }
    Status Close() noexcept override { open = false; return Status::Ok(); }
    bool IsOpen() const noexcept override { return open; }
    DeviceStatus StatusOf() const override { return {}; }
    Status SetFilters(const std::vector<CanFilter>&) override { return Status::Ok(); }
    CanBusStats Stats() const override { return {}; }
    Status RecoverFromBusOff() override { return Status::Ok(); }
    void Queue(const CanFrame& f) noexcept { if (tail < queue.size()) queue[tail++] = f; else mismatch = true; }
    Result<CanFrame> Receive(int) override {
        if (head < tail) return Result<CanFrame>::Ok(queue[head++]);
        if (head == tail) head = tail = 0;
        return Result<CanFrame>::Err(ErrorCode::ECommandTimeout);
    }
    Status Send(const CanFrame& frame) noexcept override {
        if (sent_count < sent.size()) sent[sent_count++] = frame;
        else mismatch = true;
        if (frame.id == 0 || frame.data[0] == 0x80) return Status::Ok();
        if (scripted) {
            if (cursor >= golden_case->count) { mismatch = true; return Status::Ok(); }
            const auto& pair = golden_case->pairs[cursor++];
            if (frame.id != pair.request.id || frame.dlc != 8 ||
                !std::equal(pair.request.data, pair.request.data + 8, frame.data.begin())) mismatch = true;
            CanFrame response;
            response.id = pair.response.id; response.dlc = 8;
            std::copy_n(pair.response.data, 8, response.data.begin());
            Queue(response);
            return Status::Ok();
        }
        if (silence) return Status::Ok();
        const auto index = static_cast<std::uint16_t>(frame.data[1] | (std::uint16_t{frame.data[2]} << 8u));
        const auto sub = frame.data[3];
        CanFrame reply;
        reply.id = 0x580 + (frame.id - 0x600); reply.dlc = 8;
        std::copy_n(frame.data.begin(), 4, reply.data.begin());
        if (index == silent_index) return Status::Ok();
        if (index == abort_index && (abort_sub < 0 || sub == abort_sub)) {
            reply.data[0] = 0x80;
            canopen::Encode32(reply.data.data() + 4, abort_code);
        } else if (frame.data[0] == 0x40) {
            std::uint16_t width = 4;
            std::uint32_t value = 0x12345678;
            for (const auto& a : od::kAccess) {
                const auto& e = od::kEntries[a.entry];
                if (e.index == index && e.sub == sub) width = a.width;
            }
            if (index == 0x6041) {
                if (mode == 6 && control == 0x1F && !stale_home) {
                    ++homing_reads;
                    sw = home_error ? 0x2027 : homing_reads > 1 ? 0x1427 : 0x27;
                }
                value = sw;
            } else if (index == 0x6061) value = mode;
            else if (index == 0x2002) value = selector;
            else if (index == 0x6098) value = homing;
            else if (index == 0x6064) value = position;
            else if (index == 0x606C) value = velocity;
            else if (index == 0x607D) value = static_cast<std::uint32_t>(sub == 1 ? lower_limit : upper_limit);
            reply.data[0] = static_cast<std::uint8_t>(0x43 | ((4 - width) << 2u));
            canopen::Encode32(reply.data.data() + 4, value);
        } else {
            reply.data[0] = 0x60;
            const auto value = canopen::Decode32(frame.data.data() + 4);
            if (index == 0x6040) {
                control = static_cast<std::uint16_t>(value);
                if (!hold_state) {
                    if (value == 0) sw = (sw & 8u) ? 8 : 0x40;
                    else if (value == 0x80) sw = 0x40;
                    else if (value == 6) sw = 0x21;
                    else if (value == 7) sw = 0x23;
                    else if (value == 15) sw = stale_home && mode == 6 ? 0x1427 : 0x27;
                    else if (value == 31) sw = mode == 6 ? stale_home ? 0x1427 : 0x27 : 0x1027;
                    else if (value == 2) sw = 7;
                }
                if (value == abort_control) {
                    reply.data[0] = 0x80;
                    canopen::Encode32(reply.data.data() + 4, 0x06090030u);
                }
                if (value == lose_control_ack) { lose_control_ack = 0xFFFFu; return Status::Ok(); }
            } else if (index == 0x6060) mode = static_cast<std::uint8_t>(value);
            else if (index == 0x6098) homing = static_cast<std::uint8_t>(value);
            if (index == fail_write) return Status(ErrorCode::EDeviceOffline);
        }
        if (noise) {
            auto other = reply; other.id = 0x582; Queue(other);
            other = reply; other.extended = true; Queue(other);
            other = reply; other.remote = true; Queue(other);
            other = reply; other.fd = true; Queue(other);
            other = reply; other.data[1] ^= 1u; Queue(other);
        }
        if (wrong_abort) {
            auto other = reply; other.data[0] = 0x80; other.data[1] ^= 1u;
            canopen::Encode32(other.data.data() + 4, 0x06020000u); Queue(other);
        }
        if (malformed) reply.dlc = 7;
        if (bad_echo) reply.data[3] ^= 1u;
        Queue(reply);
        return Status::Ok();
    }
};
DriverConfig Config() {
    DriverConfig config;
    config.logical_bus = "can-2"; config.sdo_timeout_ms = 5;
    config.homing_method = 17; config.homing_timeout_ms = 5;
    for (unsigned i = 0; i < config.nodes.size(); ++i) {
        config.nodes[i].node_id = static_cast<std::uint8_t>(i + 1);
        config.nodes[i].raise_direction_sign = -1;
    }
    return config;
}
struct Fixture {
    FakeCan can;
    Pmp4010Driver driver{can, Config()};
    Fixture() { (void)driver.Open(); }
};
std::size_t ControlCount(const FakeCan& can, std::uint16_t value) {
    std::size_t count = 0;
    for (std::size_t i = 0; i < can.sent_count; ++i) {
        const auto& f = can.sent[i];
        if (f.data[0] == 0x2B && f.data[1] == 0x40 && f.data[2] == 0x60 &&
            f.data[4] == static_cast<std::uint8_t>(value) && f.data[5] == static_cast<std::uint8_t>(value >> 8u)) ++count;
    }
    return count;
}
std::size_t MotionWriteCount(const FakeCan& can) {
    std::size_t count = 0;
    for (std::size_t i = 0; i < can.sent_count; ++i) {
        const auto& f = can.sent[i];
        const auto index = static_cast<std::uint16_t>(f.data[1] | (std::uint16_t{f.data[2]} << 8u));
        if ((f.data[0] & 0xE0u) == 0x20u &&
            (index == 0x6040 || index == 0x607A || index == 0x60FF)) ++count;
    }
    return count;
}
}  // namespace

KM3_TEST(shared_golden_dialogues_execute_the_cpp_protocol) {
    for (const auto& test : golden::cases) {
        FakeCan can;
        can.scripted = true; can.golden_case = &test;
        canopen::SdoClient client(can);
        const auto deadline = MonotonicUs() + 100000u;
        if (test.write) {
            const auto status = client.Download(static_cast<std::uint8_t>(test.node), static_cast<std::uint16_t>(test.index),
                static_cast<std::uint8_t>(test.sub), test.payload, test.size, deadline);
            KM3_CHECK_EQ(status.code(), test.abort ? ErrorCode::ECommandRejected : test.protocol_error ? ErrorCode::EDeviceError : ErrorCode::Ok);
        } else {
            const auto result = client.Upload(static_cast<std::uint8_t>(test.node), static_cast<std::uint16_t>(test.index),
                static_cast<std::uint8_t>(test.sub), deadline);
            KM3_CHECK_EQ(result.code(), test.abort ? ErrorCode::ECommandRejected : test.protocol_error ? ErrorCode::EDeviceError : ErrorCode::Ok);
            if (result) {
                KM3_CHECK_EQ(result.value().size, test.size);
                KM3_CHECK(std::equal(test.payload, test.payload + test.size, result.value().bytes.begin()));
            }
        }
        KM3_CHECK_EQ(client.LastAbortCode(), test.abort);
        KM3_CHECK_EQ(can.cursor, test.count);
        KM3_CHECK(!can.mismatch);
    }
}
KM3_TEST(typed_signed_values_widths_and_status_reads) {
    Fixture f;
    KM3_CHECK_EQ(f.driver.ReadStatusword(1).value(), 0x40);
    KM3_CHECK_EQ(f.driver.GetOperationMode(1).value(), 1);
    KM3_CHECK_EQ(f.driver.GetActualPosition(1).value(), -100);
    KM3_CHECK_EQ(f.driver.GetActualVelocity(1).value(), std::numeric_limits<std::int32_t>::min());
    f.can.sw = 0x27;
    KM3_REQUIRE(f.driver.SetTargetPosition(1, -123).ok());
    const auto& p = f.can.sent[f.can.sent_count - 1];
    KM3_CHECK_EQ(p.data[0], 0x23);
    KM3_CHECK_EQ(canopen::Decode32(p.data.data() + 4), static_cast<std::uint32_t>(-123));
    const auto before_velocity = f.can.sent_count;
    KM3_CHECK_EQ(f.driver.SetTargetVelocity(1, std::numeric_limits<std::int32_t>::min()).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(f.can.sent_count, before_velocity);
    KM3_REQUIRE(f.driver.WriteObject(1, 0x6060, 0, 6).ok());
    KM3_CHECK_EQ(f.can.sent[f.can.sent_count - 1].data[0], 0x2F);
    KM3_REQUIRE(f.driver.WriteObject(1, 0x1017, 0, 0x1234).ok());
    KM3_CHECK_EQ(f.can.sent[f.can.sent_count - 1].data[0], 0x2B);
}
KM3_TEST(metadata_and_validation_reject_before_send) {
    Fixture f;
    const auto before = f.can.sent_count;
    KM3_CHECK_EQ(f.driver.WriteObject(1, 0x6041, 0, 1).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.driver.WriteObject(1, 0x6060, 0, 256).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.driver.ReadObject(1, od::kIndexUnset, 0).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.driver.ReadObject(99, 0x6041, 0).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.driver.Home(99).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.driver.SendNmt(static_cast<NmtCommand>(3), 0).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.can.sent_count, before);
    auto config = Config(); config.sdo_timeout_ms = 0;
    Pmp4010Driver no_timeout(f.can, config);
    KM3_REQUIRE(no_timeout.Open().ok());
    KM3_CHECK_EQ(no_timeout.ReadStatusword(1).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(f.can.sent_count, before);
}
KM3_TEST(correlation_noise_and_abort_diagnostics) {
    Fixture f;
    f.can.noise = true; f.can.wrong_abort = true;
    KM3_REQUIRE(f.driver.ReadStatusword(1).ok());
    KM3_CHECK(f.driver.Stats().rx_frames >= 7);
    f.can.noise = false; f.can.wrong_abort = false;
    f.can.abort_index = 0x6041; f.can.abort_code = 0xDEADBEEFu;
    KM3_CHECK_EQ(f.driver.ReadStatusword(1).code(), ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(f.driver.LastSdoAbortCode(), 0xDEADBEEFu);
}
KM3_TEST(absolute_timeout_malformed_and_stale_queue) {
    Fixture f;
    CanFrame stale;
    stale.id = 0x581; stale.dlc = 8; stale.data[0] = 0x4B; stale.data[1] = 0x41; stale.data[2] = 0x60; stale.data[4] = 0xFF;
    f.can.Queue(stale);
    KM3_CHECK_EQ(f.driver.ReadStatusword(1).value(), 0x40);
    f.can.bad_echo = true;
    const auto before = MonotonicUs();
    KM3_CHECK_EQ(f.driver.ReadStatusword(1).code(), ErrorCode::ECommandTimeout);
    KM3_CHECK(MonotonicUs() - before < 100000u);
    KM3_CHECK_EQ(f.driver.Stats().sdo_timeouts, 1u);
    Fixture malformed; malformed.can.malformed = true;
    KM3_CHECK_EQ(malformed.driver.ReadStatusword(1).code(), ErrorCode::EDeviceError);
    KM3_CHECK_EQ(malformed.can.sent[malformed.can.sent_count - 1].data[0], 0x80);
}
KM3_TEST(enable_fault_reset_and_confirmed_state_transitions) {
    Fixture f;
    f.can.sw = 8;
    KM3_REQUIRE(f.driver.Enable(1).ok());
    KM3_CHECK_EQ(f.can.sw, 0x27);
    KM3_CHECK_EQ(ControlCount(f.can, 0), 2u);
    KM3_CHECK_EQ(ControlCount(f.can, 0x80), 1u);
    KM3_CHECK_EQ(ControlCount(f.can, 6), 1u);
    KM3_CHECK_EQ(ControlCount(f.can, 7), 1u);
    KM3_CHECK_EQ(ControlCount(f.can, 15), 1u);
    const auto count = f.can.sent_count;
    KM3_REQUIRE(f.driver.Enable(1).ok());
    KM3_CHECK_EQ(f.can.sent_count, count + 3u);
    KM3_REQUIRE(f.driver.Disable(1).ok()); KM3_CHECK_EQ(f.can.sw, 0x40);
    KM3_REQUIRE(f.driver.Shutdown(1).ok()); KM3_CHECK_EQ(f.can.sw, 0x21);
    KM3_REQUIRE(f.driver.SwitchOn(1).ok()); KM3_CHECK_EQ(f.can.sw, 0x23);
    KM3_REQUIRE(f.driver.EnableOperation(1).ok()); KM3_CHECK_EQ(f.can.sw, 0x27);
    KM3_REQUIRE(f.driver.Stop(1).ok()); KM3_CHECK_EQ(f.can.sw, 7);
}
KM3_TEST(enable_selector_modes_and_transition_timeout_fail_closed) {
    Fixture f;
    f.can.selector = 4;
    KM3_CHECK_EQ(f.driver.Enable(1).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(ControlCount(f.can, 6), 0u);
    f.can.selector = 0;
    // 2/7 与 3/9 都在拒绝之列：前者使能终值不同（0x7F / 0x1F），后者
    // 从站按 60FFh 残留值运行而速度标度换算链未实现。逐模式的完整
    // 拒绝集合见 speed_mode_cannot_be_enabled_because_scale_chain_is_absent。
    for (std::uint8_t mode : std::array<std::uint8_t, 5>{2, 3, 7, 9, 0}) {
        f.can.mode = mode;
        KM3_CHECK_EQ(f.driver.Enable(1).code(), ErrorCode::EInvalidConfig);
    }
    f.can.mode = 1; f.can.hold_state = true;
    KM3_CHECK_EQ(f.driver.Enable(1).code(), ErrorCode::ECommandTimeout);
    KM3_CHECK_EQ(ControlCount(f.can, 15), 0u);
    Fixture fault; fault.can.sw = 8;
    KM3_CHECK_EQ(fault.driver.SetTargetPosition(1, 1).code(), ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(ControlCount(f.can, 31), 0u);
}
KM3_TEST(speed_mode_cannot_be_enabled_because_scale_chain_is_absent) {
    // 本用例钉住一条**安全不变式**：速度模式下驱动不得使能。
    //
    // 为什么它值得单独立一条：SetTargetVelocity 已经无条件拒绝，看起来
    // 「速度写不进去」就够了。但真实的暴露面不是驱动写值，而是
    // **60FFh 里已有的残留值**——实测该电机 60FFh = 6553600 = 3000 rpm
    // （额定转速）。只要模式被别人（厂家工具 / 上层）置为 3，再让驱动
    // 使能，从站就会按那个残留值转起来。所以在**使能这一侧**也必须有闸门。
    //
    // 断言两件事：(1) 拒绝且不发任何控制字；(2) 模式 1/4/6/8/10 仍可使能，
    // 即这道闸门只挡下面列出的那些，没有误伤。
    //
    // 拒绝集合分两组理由（详见 CanEnableInMode 注释）：
    //   3(PV)、9(CSV) —— 从站按 60FFh 残留值运行，换算链未实现；
    //   2(VM)、7(IP) —— 使能终值是 0x7F / 0x1F，本驱动只实现 0x0F。
    // 9 曾经漏在「可使能」一侧（注释说排除 CSV 而代码没排除），
    // 本条用例就是钉住它不能回去。
    for (std::uint8_t mode : std::array<std::uint8_t, 6>{3, 9, 2, 7, 0, 5}) {
        Fixture f;
        f.can.mode = mode;
        f.can.sw = 0x40;
        const auto before = f.can.sent_count;
        KM3_CHECK_EQ(f.driver.Enable(1).code(), ErrorCode::EInvalidConfig);
        // 断言的是**语义**而不是精确帧数：拒绝发生在读模式下完之后，
        // 所以会看到若干只读请求（RequireCia402 读 2002h:01h、模式读 6061h）。
        // 精确计数会把「多读一个对象」这种无关改动变成测试失败，
        // 而真正要钉的是「一个控制字都没发出去」。
        KM3_CHECK(f.can.sent_count > before);          // 读了（诊断信息）
        KM3_CHECK(f.can.sent_count <= before + 4u);    // 但只有读，没有写风暴
        KM3_CHECK_EQ(ControlCount(f.can, 6), 0u);
        KM3_CHECK_EQ(ControlCount(f.can, 7), 0u);
        KM3_CHECK_EQ(ControlCount(f.can, 15), 0u);
        KM3_CHECK_EQ(f.can.sw, 0x40);                  // 状态字未被推动
    }
    // EnableOperation 与 Enable 走同一个判据，单独验一遍：
    Fixture g;
    g.can.mode = 3;
    KM3_CHECK_EQ(g.driver.EnableOperation(1).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(ControlCount(g.can, 15), 0u);

    for (std::uint8_t mode : std::array<std::uint8_t, 5>{1, 4, 6, 8, 10}) {
        Fixture f;
        f.can.mode = mode;
        f.can.sw = 0x40;
        KM3_REQUIRE(f.driver.Enable(1).ok());
        KM3_CHECK_EQ(f.can.sw, 0x27);
    }
}

KM3_TEST(mode_change_direction_and_pp_handshake) {
    Fixture f;
    KM3_REQUIRE(f.driver.SetOperationMode(1, 3).ok());
    f.can.sw = 0x27;
    KM3_CHECK_EQ(f.driver.SetOperationMode(1, 6).code(), ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(f.driver.SetVelocity(1, 123, Direction::Raise).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(f.driver.SetVelocity(1, -1, Direction::Raise).code(), ErrorCode::EInvalidRequest);
    f.can.mode = 1;
    KM3_REQUIRE(f.driver.MoveTo(1, -100).ok());
    KM3_CHECK_EQ(ControlCount(f.can, 31), 1u);
    KM3_CHECK_EQ(f.can.control, 15);
    f.can.lose_control_ack = 31;
    KM3_CHECK_EQ(f.driver.MoveTo(1, 10).code(), ErrorCode::ECommandTimeout);
    // A timed-out command quarantines the node; no uncertain cleanup can send.
    KM3_CHECK_EQ(f.can.control, 31);
    KM3_CHECK(f.driver.Stats().cleanup_failures > 0);
    Fixture aborted; aborted.can.sw = 0x27; aborted.can.abort_control = 31;
    KM3_CHECK_EQ(aborted.driver.MoveTo(1, 10).code(), ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(aborted.can.control, 2);
    KM3_CHECK_EQ(aborted.driver.LastSdoAbortCode(), 0x06090030u);
}
KM3_TEST(home_success_stale_completion_error_and_lost_enable_ack_cleanup) {
    Fixture f;
    KM3_REQUIRE(f.driver.Home(1).ok());
    KM3_CHECK_EQ(f.can.mode, 6); KM3_CHECK_EQ(f.can.homing, 17); KM3_CHECK_EQ(f.can.control, 15);
    f.can.stale_home = true;
    KM3_CHECK_EQ(f.driver.Home(1).code(), ErrorCode::ECommandTimeout);
    KM3_CHECK_EQ(f.can.control, 2);
    f.can.stale_home = false; f.can.home_error = true; f.can.homing_reads = 0;
    KM3_CHECK_EQ(f.driver.Home(1).code(), ErrorCode::EDeviceError);
    KM3_CHECK_EQ(f.can.control, 2);
    Fixture lost; lost.can.lose_control_ack = 15;
    KM3_CHECK_EQ(lost.driver.Home(1).code(), ErrorCode::ECommandTimeout);
    KM3_CHECK_EQ(lost.can.control, 15);
    KM3_CHECK(lost.driver.Stats().cleanup_failures > 0);
}
KM3_TEST(home_invalid_config_selector_and_nmt_snapshot) {
    Fixture f;
    f.can.selector = 4;
    KM3_CHECK_EQ(f.driver.Home(1).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(ControlCount(f.can, 0), 0u);
    auto config = Config(); config.homing_method = 1;
    Pmp4010Driver unsupported(f.can, config); KM3_REQUIRE(unsupported.Open().ok());
    const auto count = f.can.sent_count;
    KM3_CHECK_EQ(unsupported.Home(1).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(f.can.sent_count, count);
    KM3_REQUIRE(f.driver.SendNmt(NmtCommand::Start, 0).ok());
    KM3_CHECK_EQ(f.can.sent[f.can.sent_count - 1].id, 0u);
    KM3_CHECK_EQ(f.can.sent[f.can.sent_count - 1].dlc, 2);
    const auto state = f.driver.ProbeNode(1);
    KM3_REQUIRE(state.ok()); KM3_CHECK(state.value().online);
    KM3_CHECK_EQ(state.value().actual_position_counts, -100);
}
KM3_TEST(bounded_internals_and_vector_compatibility_do_not_allocate) {
    Fixture f;
    std::vector<std::uint8_t> out{0xDE, 0xAD};
    KM3_CHECK_EQ(f.driver.SdoRead(1, 0x6041, 0, out).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.can.sent_count, 0u);
    out.reserve(canopen::Payload::kCapacity);
    allocations = 0; count_allocations = true;
    const auto st = f.driver.SdoRead(1, 0x6041, 0, out);
    const auto enabled = f.driver.Enable(1);
    const auto pos = f.driver.GetActualPosition(1);
    count_allocations = false;
    KM3_CHECK(st.ok()); KM3_CHECK(enabled.ok()); KM3_CHECK(pos.ok());
    KM3_CHECK_EQ(allocations, 0u);
    KM3_CHECK_EQ(out.size(), 2u); KM3_CHECK_EQ(out[0], 0x40);
    f.can.silence = true;
    const auto old = out;
    KM3_CHECK_EQ(f.driver.SdoRead(1, 0x6041, 0, out).code(), ErrorCode::ECommandTimeout);
    KM3_CHECK_EQ(out, old);
}
KM3_TEST(segmented_strings_bounds_toggle_size_and_download_width3) {
    // Exercise malformed wire paths with complete scripted exchanges.
    golden::Pair pairs[3] = {
        {{0x601,{0x40,8,0x10,0,0,0,0,0}}, {0x581,{0x41,8,0x10,0,9,0,0,0}}},
        {{0x601,{0x60,0,0,0,0,0,0,0}}, {0x581,{0,1,2,3,4,5,6,7}}},
        {{0x601,{0x70,0,0,0,0,0,0,0}}, {0x581,{0x1B,8,9,0,0,0,0,0}}},
    };
    golden::Case test{"string",1,0x1008,0,false,0,pairs,3,nullptr,9};
    FakeCan can; can.scripted = true; can.golden_case = &test;
    Pmp4010Driver driver(can, Config()); KM3_REQUIRE(driver.Open().ok());
    allocations = 0; count_allocations = true;
    const auto text = driver.ReadString(1, 0x1008, 0);
    count_allocations = false;
    KM3_REQUIRE(text.ok()); KM3_CHECK_EQ(text.value().size, 9u); KM3_CHECK_EQ(text.value().bytes[8], 9);
    KM3_CHECK_EQ(allocations, 0u); KM3_CHECK(!can.mismatch);
    can.cursor = 0; pairs[2].response.data[0] = 0x0B; // wrong toggle
    KM3_CHECK_EQ(driver.ReadString(1, 0x1008, 0).code(), ErrorCode::EDeviceError);
    can.cursor = 0; pairs[2].response.data[0] = 0x19; //3 bytes exceeds declared9
    KM3_CHECK_EQ(driver.ReadString(1, 0x1008, 0).code(), ErrorCode::EDeviceError);
    can.cursor = 0; pairs[0].response.data[4] = 1; pairs[0].response.data[5] = 1; //257 >256
    KM3_CHECK_EQ(driver.ReadString(1, 0x1008, 0).code(), ErrorCode::EDeviceError);
    FakeCan raw; canopen::SdoClient client(raw);
    const std::uint8_t bytes[3] = {1,2,3};
    KM3_REQUIRE(client.Download(1, 0x1000, 0, bytes, 3, MonotonicUs() + 5000).ok());
    KM3_CHECK_EQ(raw.sent[0].data[0], 0x27);
}
KM3_TEST(position_limits_fail_closed_inclusive_and_generic_writes_cannot_bypass) {
    for (const auto value : {std::numeric_limits<std::int32_t>::min(), -65536, 65536,
                              std::numeric_limits<std::int32_t>::max()}) {
        Fixture f; f.can.sw = 0x27;
        KM3_CHECK_EQ(f.driver.SetTargetPosition(1, value).code(), ErrorCode::EInvalidRequest);
        KM3_CHECK_EQ(f.driver.MoveTo(1, value).code(), ErrorCode::EInvalidRequest);
        KM3_CHECK_EQ(f.driver.WriteObject(1, 0x607A, 0, static_cast<std::uint32_t>(value)).code(), ErrorCode::EInvalidRequest);
        std::vector<std::uint8_t> bytes(4);
        canopen::Encode32(bytes.data(), static_cast<std::uint32_t>(value));
        KM3_CHECK_EQ(f.driver.SdoWrite(1, 0x607A, 0, bytes).code(), ErrorCode::EInvalidRequest);
        KM3_CHECK_EQ(MotionWriteCount(f.can), 0u);
        // Validation needs read requests; OD-closed tests separately require zero total frames.
        KM3_CHECK(f.can.sent_count > 0);
    }
    for (const auto value : {-65535, 65535}) {
        Fixture f; f.can.sw = 0x27;
        KM3_REQUIRE(f.driver.SetTargetPosition(1, value).ok());
        KM3_REQUIRE(f.driver.MoveTo(1, value).ok());
        KM3_CHECK(MotionWriteCount(f.can) > 0);
    }
    for (const auto sub : {1, 2}) {
        Fixture f; f.can.sw = 0x27; f.can.abort_index = 0x607D;
        f.can.abort_sub = sub; f.can.abort_code = 0x06020000u;
        KM3_CHECK_EQ(f.driver.SetTargetPosition(1, 0).code(), ErrorCode::ECommandRejected);
        KM3_CHECK_EQ(f.driver.MoveTo(1, 0).code(), ErrorCode::ECommandRejected);
        KM3_CHECK_EQ(MotionWriteCount(f.can), 0u);
    }
    Fixture timeout; timeout.can.sw = 0x27; timeout.can.silent_index = 0x607D;
    KM3_CHECK_EQ(timeout.driver.MoveTo(1, 0).code(), ErrorCode::ECommandTimeout);
    KM3_CHECK_EQ(MotionWriteCount(timeout.can), 0u);
    Fixture inverted; inverted.can.sw = 0x27; inverted.can.lower_limit = 1; inverted.can.upper_limit = -1;
    KM3_CHECK_EQ(inverted.driver.MoveTo(1, 0).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(MotionWriteCount(inverted.can), 0u);
}
KM3_TEST(velocity_without_verified_conversion_rejects_all_write_paths) {
    Fixture f; f.can.sw = 0x27; f.can.mode = 3;
    const auto before = f.can.sent_count;
    for (const auto value : {0, 1, std::numeric_limits<std::int32_t>::max(),
                              std::numeric_limits<std::int32_t>::min()}) {
        KM3_CHECK_EQ(f.driver.SetTargetVelocity(1, value).code(), ErrorCode::EInvalidConfig);
        KM3_CHECK_EQ(f.driver.WriteObject(1, 0x60FF, 0, static_cast<std::uint32_t>(value)).code(), ErrorCode::EInvalidConfig);
        std::vector<std::uint8_t> bytes(4); canopen::Encode32(bytes.data(), static_cast<std::uint32_t>(value));
        KM3_CHECK_EQ(f.driver.SdoWrite(1, 0x60FF, 0, bytes).code(), ErrorCode::EInvalidConfig);
    }
    KM3_CHECK_EQ(f.driver.SetVelocity(1, 1, Direction::Raise).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(f.driver.SetVelocity(1, 1, Direction::Lower).code(), ErrorCode::EInvalidConfig);
    KM3_CHECK_EQ(MotionWriteCount(f.can), 0u);
    KM3_CHECK_EQ(f.can.sent_count, before);
}
KM3_TEST(all_controlword_primitives_require_cia402_and_selector_failure_denies) {
    using Primitive = Status (Pmp4010Driver::*)(std::uint8_t);
    const Primitive primitives[] = {&Pmp4010Driver::Enable, &Pmp4010Driver::Disable,
        &Pmp4010Driver::Shutdown, &Pmp4010Driver::SwitchOn, &Pmp4010Driver::EnableOperation,
        &Pmp4010Driver::QuickStop, &Pmp4010Driver::FaultReset, &Pmp4010Driver::Stop,
        &Pmp4010Driver::Home};
    for (auto primitive : primitives) {
        for (int failure = 0; failure < 3; ++failure) {
            Fixture f;
            if (failure == 0) f.can.selector = 4;
            else if (failure == 1) { f.can.abort_index = 0x2002; f.can.abort_code = 0x06020000u; }
            else f.can.silent_index = 0x2002;
            const auto st = (f.driver.*primitive)(1);
            KM3_CHECK_EQ(st.code(), failure == 0 ? ErrorCode::EInvalidConfig :
                failure == 1 ? ErrorCode::ECommandRejected : ErrorCode::ECommandTimeout);
            KM3_CHECK_EQ(MotionWriteCount(f.can), 0u);
        }
    }
    for (int failure = 0; failure < 2; ++failure) {
        Fixture f;
        if (failure == 0) f.can.selector = 4;
        else { f.can.abort_index = 0x2002; f.can.abort_code = 0x06020000u; }
        KM3_CHECK(!f.driver.WriteObject(1, 0x6040, 0, 15).ok());
        std::vector<std::uint8_t> bytes{15, 0};
        KM3_CHECK(!f.driver.SdoWrite(1, 0x6040, 0, bytes).ok());
        KM3_CHECK_EQ(MotionWriteCount(f.can), 0u);
    }
    Fixture read_only; read_only.can.selector = 4;
    KM3_REQUIRE(read_only.driver.ReadStatusword(1).ok());
    KM3_CHECK_EQ(read_only.can.sent_count, 1u); // ordinary reads add no selector transaction
}
KM3_TEST(string_access_comes_from_generated_od_and_unknown_objects_never_send) {
    Fixture f;
    std::size_t strings = 0;
    for (const auto& access : od::kAccess) {
        if (!access.variable_length) continue;
        ++strings;
        KM3_CHECK_EQ(access.width, canopen::Payload::kCapacity);
        KM3_CHECK(!access.writable);
        KM3_CHECK(od::kEntries[access.entry].verified);
    }
    KM3_CHECK_EQ(strings, 3u);
    KM3_CHECK_EQ(f.driver.ReadBytes(1, 0x100B, 0).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.driver.ReadString(1, 0x6041, 0).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.driver.ReadString(1, 0x1008, 1).code(), ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(f.can.sent_count, 0u);
}
KM3_TEST(timeout_quarantines_node_until_external_recovery_prevents_late_reply_reuse) {
    FakeCan can; canopen::SdoClient client(can);
    can.silence = true;
    KM3_CHECK_EQ(client.Upload(1, 0x6041, 0, MonotonicUs() + 1000u).code(), ErrorCode::ECommandTimeout);
    KM3_CHECK(client.IsQuarantined(1));
    CanFrame stale; stale.id = 0x581; stale.dlc = 8;
    stale.data[0] = 0x4B; stale.data[1] = 0x41; stale.data[2] = 0x60; stale.data[4] = 0x27;
    can.Queue(stale); can.silence = false; can.sw = 8;
    const auto before = can.sent_count;
    KM3_CHECK_EQ(client.Upload(1, 0x6041, 0, MonotonicUs() + 5000u).code(), ErrorCode::EDeviceBusy);
    KM3_CHECK_EQ(client.Upload(1, 0x6064, 0, MonotonicUs() + 5000u).code(), ErrorCode::EDeviceBusy);
    const std::uint8_t value[2] = {6, 0};
    KM3_CHECK_EQ(client.Download(1, 0x6040, 0, value, 2, MonotonicUs() + 5000u).code(), ErrorCode::EDeviceBusy);
    KM3_CHECK_EQ(can.sent_count, before);
    const auto other = client.Upload(2, 0x6041, 0, MonotonicUs() + 5000u);
    KM3_REQUIRE(other.ok()); KM3_CHECK_EQ(canopen::Decode32(other.value().bytes.data()), 8u);
    KM3_CHECK(client.IsQuarantined(1));
    // Fixture simulates independent transport+peer recovery and removal of old replies.
    can.head = can.tail = 0;
    KM3_REQUIRE(client.ConfirmExternalRecovery(1).ok());
    KM3_CHECK(!client.IsQuarantined(1));
    const auto recovered = client.Upload(1, 0x6041, 0, MonotonicUs() + 5000u);
    KM3_REQUIRE(recovered.ok()); KM3_CHECK_EQ(canopen::Decode32(recovered.value().bytes.data()), 8u);
}
KM3_TEST(all_upload_command_bytes_match_independent_acceptance_table) {
    // Independent table includes all 8 expedited commands, particularly 53/57/5B/5F.
    // 42 is deliberately excluded; 40 is the valid size-unspecified segmented case.
    constexpr std::array<unsigned, 8> expedited{0x43, 0x47, 0x4B, 0x4F, 0x53, 0x57, 0x5B, 0x5F};
    constexpr std::array<std::size_t, 8> sizes{4, 3, 2, 1, 4, 3, 2, 1};
    for (unsigned cs = 0; cs < 256; ++cs) {
        golden::Pair pairs[2] = {
            {{0x601,{0x40,8,0x10,0,0,0,0,0}}, {0x581,{static_cast<unsigned char>(cs),8,0x10,0,1,2,3,4}}},
            {{0x601,{0x60,0,0,0,0,0,0,0}}, {0x581,{0x0F,0,0,0,0,0,0,0}}},
        };
        golden::Case test{"all-upload-cs",1,0x1008,0,false,0,pairs,2,nullptr,0};
        FakeCan can; can.scripted = true; can.golden_case = &test;
        canopen::SdoClient client(can);
        const auto result = client.Upload(1, 0x1008, 0, MonotonicUs() + 5000u);
        const auto found = std::find(expedited.begin(), expedited.end(), cs);
        if (found != expedited.end()) {
            KM3_REQUIRE(result.ok());
            KM3_CHECK_EQ(result.value().size, sizes[static_cast<std::size_t>(found - expedited.begin())]);
        } else if (cs == 0x40) {
            KM3_REQUIRE(result.ok()); KM3_CHECK_EQ(result.value().size, 0u);
        } else {
            KM3_CHECK(!result.ok());
        }
        KM3_CHECK(!can.mismatch);
    }
}
KM3_TEST_MAIN()
