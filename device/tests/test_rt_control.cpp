// 控制律与安全监控的契约测试。见 docs/架构设计/设备端程序架构.md §2.1.1 / §2.1.5。
//
// 测试原则：断言**契约**，不断言臆造的厂家字节。
//   爬绳器/吊点/抬降电机的寄存器地址与标度尚未与手册核对，
//   因此这里只验证"越限不发送""总线缺失显式失败""能力不匹配被拒"
//   这类与手册无关、但错了会出事的性质。

#include <cstdint>
#include <vector>

#include "km3/hal/mock_devices.hpp"
#include "km3/rt_control/motion_controller.hpp"
#include "km3/rt_control/safety_monitor.hpp"
#include "test_harness.hpp"

namespace {

using km3::ErrorCode;
using km3::MockCanDevice;
using km3::MockRs485Device;
using km3::Rs485Backend;
using km3::SerialConfig;
using km3::CanBackend;

// 模拟件的寄存器文件只有 256 个（构造函数 assign(256,0)），
// 因此测试把寄存器地址改到低位，避免被 EInvalidRequest 挡住而误判成控制律的问题。
km3::control::ControlLimits SmallLimits() {
    km3::control::ControlLimits lim;
    lim.climb_slave_addr = 1;
    lim.climb_speed_reg = 0x0010;
    lim.suspension_slave_addr = 2;
    lim.suspension_target_reg = 0x0011;
    lim.relay_slave_addr = 3;
    lim.relay_brush_reg = 0x0012;
    return lim;
}

struct Bench {
    MockCanDevice esc{"can-1", CanBackend::SocketCan};
    MockCanDevice canopen{"can-2", CanBackend::SocketCan};
    MockCanDevice fd{"usb-can-1", CanBackend::UsbCan};
    MockRs485Device servo{"rs485-1", Rs485Backend::NativeSerial, SerialConfig{}};
    MockRs485Device relay{"usb-rs485-4", Rs485Backend::UsbSerial, SerialConfig{}};

    km3::control::ControlBuses Buses() {
        km3::control::ControlBuses b;
        b.esc_bus = &esc;
        b.canopen_bus = &canopen;
        b.fd_bus = &fd;
        b.servo_bus = &servo;
        b.relay_bus = &relay;
        return b;
    }

    void OpenAll() {
        esc.Open();
        canopen.Open();
        fd.Open();
        servo.Open();
        relay.Open();
    }
};

}  // namespace

// ---- 准入：越限必须在发帧之前被拒 ----

KM3_TEST(ClimbRopeOverLimitIsRejectedAndSendsNothing) {
    Bench b;
    b.OpenAll();
    km3::control::MotionController ctrl(b.Buses(), SmallLimits());

    KM3_CHECK(ctrl.ClimbRope(9.9F, km3::control::Direction::Up).code() ==
              ErrorCode::ECommandRejected);
    // 关键：不仅返回错误，而且**一帧都没发**。
    // 若只断言返回码，一个"先发送再检查"的实现也能通过。
    KM3_CHECK_EQ(b.servo.SentBytes().size(), std::size_t{0});
    KM3_CHECK_EQ(ctrl.State().rejected_commands, std::uint32_t{1});
    KM3_CHECK(ctrl.State().mode == km3::control::MotionMode::Idle);
}

KM3_TEST(ClimbRopeWithinLimitSendsAndUpdatesState) {
    Bench b;
    b.OpenAll();
    km3::control::MotionController ctrl(b.Buses(), SmallLimits());

    KM3_REQUIRE(ctrl.ClimbRope(0.2F, km3::control::Direction::Up).ok());
    KM3_CHECK_EQ(b.servo.SentBytes().size(), std::size_t{1});
    KM3_CHECK(ctrl.State().mode == km3::control::MotionMode::Climbing);
    // 方向必须体现在符号上，否则"上升"和"下降"在下游无法区分。
    KM3_CHECK(ctrl.State().climb_speed_mps > 0.0F);

    KM3_REQUIRE(ctrl.ClimbRope(0.2F, km3::control::Direction::Down).ok());
    KM3_CHECK(ctrl.State().climb_speed_mps < 0.0F);
}

KM3_TEST(ClimbRopeNullBusFailsExplicitlyInsteadOfSilentlySucceeding) {
    km3::control::ControlBuses buses;   // 全空
    km3::control::MotionController ctrl(buses, SmallLimits());
    // 配置缺一路总线时静默返回 Ok，会让操作员以为命令生效了 —— 必须显式失败。
    KM3_CHECK(ctrl.ClimbRope(0.1F, km3::control::Direction::Up).code() ==
              ErrorCode::EInvalidConfig);
}

KM3_TEST(SuspensionPointBeyondRangeIsRejected) {
    Bench b;
    b.OpenAll();
    auto lim = SmallLimits();
    lim.position_min_steps = -100;
    lim.position_max_steps = 100;
    km3::control::MotionController ctrl(b.Buses(), lim);

    KM3_CHECK(ctrl.MoveSuspensionPoint(101).code() == ErrorCode::ECommandRejected);
    KM3_CHECK(ctrl.MoveSuspensionPoint(-101).code() == ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(b.servo.SentBytes().size(), std::size_t{0});
    KM3_REQUIRE(ctrl.MoveSuspensionPoint(100).ok());
    KM3_CHECK_EQ(b.servo.SentBytes().size(), std::size_t{1});
}

// ---- 推进电调 ----

KM3_TEST(ThrusterPercentOverLimitIsRejected) {
    Bench b;
    b.OpenAll();
    km3::control::MotionController ctrl(b.Buses(), SmallLimits());

    KM3_CHECK(ctrl.SetThrusterSpeed(km3::control::ThrusterId::Left, 120.0F).code() ==
              ErrorCode::ECommandRejected);
    KM3_CHECK_EQ(b.esc.SentFrames().size(), std::size_t{0});
    KM3_REQUIRE(ctrl.SetThrusterSpeed(km3::control::ThrusterId::Left, 50.0F).ok());
    KM3_CHECK_EQ(b.esc.SentFrames().size(), std::size_t{1});
}

// ---- CANfd 能力边界：本板最重要的一条硬件事实 ----

KM3_TEST(FdMotorOnClassicCanIsRejectedNotSilentlyDropped) {
    // 把标称 CANfd 的 P65MAX-S 接到主板 CAN（仅 2.0B）是接线错误。
    // 软件不能"兼容"它，但也不能让帧悄无声息地消失。
    MockCanDevice classic("can-2", CanBackend::SocketCan);
    classic.Open();
    km3::control::ControlBuses buses;
    buses.fd_bus = &classic;
    km3::control::MotionController ctrl(buses, SmallLimits());

    km3::control::LiftCommand cmd;
    cmd.node_id = 1;
    cmd.target_steps = 10;

    KM3_CHECK(ctrl.SetLiftMotorB(cmd).code() == ErrorCode::EInvalidRequest);
    KM3_CHECK_EQ(classic.SentFrames().size(), std::size_t{0});
    KM3_CHECK_EQ(ctrl.State().rejected_commands, std::uint32_t{1});
}

KM3_TEST(FdMotorOnUsbCanBackendIsAccepted) {
    Bench b;
    b.OpenAll();
    km3::control::MotionController ctrl(b.Buses(), SmallLimits());

    km3::control::LiftCommand cmd;
    cmd.node_id = 1;
    cmd.target_steps = 10;

    KM3_REQUIRE(ctrl.SetLiftMotorB(cmd).ok());
    KM3_REQUIRE(b.fd.SentFrames().size() == 1u);
    // 帧本身必须真的是 FD 帧，且载荷长度合法（校验基于原始 dlc，无钳制）。
    KM3_CHECK(b.fd.SentFrames()[0].fd);
    KM3_CHECK(b.fd.SentFrames()[0].IsDlcValid());
}

KM3_TEST(CanopenLiftMotorSendsSdoDownload) {
    Bench b;
    b.OpenAll();
    km3::control::MotionController ctrl(b.Buses(), SmallLimits());

    km3::control::LiftCommand cmd;
    cmd.node_id = 5;
    cmd.target_steps = 1234;

    KM3_REQUIRE(ctrl.SetLiftMotorA(cmd).ok());
    KM3_REQUIRE(b.canopen.SentFrames().size() == 1u);
    const auto& f = b.canopen.SentFrames()[0];
    KM3_CHECK_EQ(f.dlc, std::uint8_t{8});
    // COB-ID = 0x600 + node_id，CANopen 下行 SDO 的固定约定。
    KM3_CHECK_EQ(f.id, 0x600u + 5u);
    KM3_CHECK_EQ(f.data[0], std::uint8_t{0x23});   // expedited download, 4 bytes
    KM3_CHECK_EQ(f.data[1], std::uint8_t{0x7A});   // 0x607A 低字节
    KM3_CHECK_EQ(f.data[2], std::uint8_t{0x60});   // 0x607A 高字节
}

// ---- 继电器：无反馈的开关量 ----

KM3_TEST(BrushSpinIsPlainOnOffWithNoSpeedControl) {
    Bench b;
    b.OpenAll();
    km3::control::MotionController ctrl(b.Buses(), SmallLimits());

    KM3_REQUIRE(ctrl.SetBrushSpin(true).ok());
    KM3_REQUIRE(ctrl.SetBrushSpin(false).ok());
    // 只有开与关两种写入，不存在"设定转速"。这是机构约束，不是实现选择。
    KM3_CHECK_EQ(b.relay.SentBytes().size(), std::size_t{2});
}

// ---- 紧急停止：尽力而为，不被单条总线故障阻断 ----

KM3_TEST(EmergencyStopStopsEveryBusButReportsNoFatalOnPartialFailure) {
    Bench b;
    b.OpenAll();
    km3::control::MotionController ctrl(b.Buses(), SmallLimits());

    KM3_REQUIRE(ctrl.ClimbRope(0.2F, km3::control::Direction::Up).ok());
    b.servo.ClearSent();
    b.esc.ClearSent();

    // 让 CAN 侧发送失败，验证停止动作不被它阻断。
    b.esc.Faults().fail_send = true;

    KM3_CHECK(ctrl.EmergencyStop().ok());       // 不因单点失败返回错误
    KM3_CHECK(ctrl.State().mode == km3::control::MotionMode::EmergencyStop);
    KM3_CHECK(ctrl.State().climb_speed_mps == 0.0F);
    // 伺服侧仍收到停止写入 —— 这是"未被阻断"的证据。
    KM3_CHECK_EQ(b.servo.SentBytes().size(), std::size_t{1});
    // 失败被计数，供上报，而不是被吞掉。
    KM3_CHECK(ctrl.State().bus_errors > 0u);
}

KM3_TEST(ClearSafeModeDoesNotResumeMotion) {
    Bench b;
    b.OpenAll();
    km3::control::MotionController ctrl(b.Buses(), SmallLimits());

    ctrl.EnterSafeMode();
    KM3_CHECK(ctrl.State().mode == km3::control::MotionMode::SafeMode);
    ctrl.ClearSafeMode();
    // 只清模式，不自动恢复动作：操作员确认后必须重新下发命令。
    KM3_CHECK(ctrl.State().mode == km3::control::MotionMode::Idle);
    KM3_CHECK(ctrl.State().climb_speed_mps == 0.0F);
}

// ---- 限值判定只判定，不动作 ----

KM3_TEST(EvaluateLimitsReportsButTakesNoAction) {
    Bench b;
    b.OpenAll();
    auto lim = SmallLimits();
    lim.current_max_a = 5.0F;
    km3::control::MotionController ctrl(b.Buses(), lim);

    ctrl.UpdateFeedback(0.0F, 9.0F, 20.0F, false, 1000);
    KM3_CHECK(ctrl.EvaluateLimits() == ErrorCode::EDeviceError);
    // 判定不改变模式：触发停止是 SafetyMonitor 的职责。
    KM3_CHECK(ctrl.State().mode == km3::control::MotionMode::Idle);

    ctrl.UpdateFeedback(0.0F, 1.0F, 20.0F, false, 2000);
    KM3_CHECK(ctrl.EvaluateLimits() == ErrorCode::Ok);
}

// ================= SafetyMonitor =================

KM3_TEST(WatchdogUnarmedIsConsideredExpired) {
    km3::control::SafetyMonitor mon;
    // 从未装载 = 从未建立命令通道 = 视为超时。
    // 启动时不该有执行器处于"以为有人在指挥"的状态。
    KM3_CHECK(mon.WatchdogExpired(0));
    KM3_CHECK(mon.WatchdogExpired(1'000'000));
}

KM3_TEST(WatchdogExpiresAtChannelSpecificThreshold) {
    km3::control::SafetyMonitor mon;
    mon.ArmCommandWatchdog(1000);

    // 直连：500ms
    KM3_CHECK(!mon.WatchdogExpired(1000 + 499'000));
    KM3_CHECK(mon.WatchdogExpired(1000 + 500'001));

    // 切到中转：3s。切换时必须重置计时，否则刚切过去就会假超时。
    mon.SetChannel(km3::control::CommandChannel::Relay, 1'000'000);
    KM3_CHECK(!mon.WatchdogExpired(1'000'000 + 2'900'000));
    KM3_CHECK(mon.WatchdogExpired(1'000'000 + 3'000'001));
}

KM3_TEST(SetChannelToSameChannelDoesNotExtendWatchdog) {
    km3::control::SafetyMonitor mon;
    mon.ArmCommandWatchdog(0);
    // 反复"确认通道"不应无限延长看门狗，否则超时保护形同虚设。
    mon.SetChannel(km3::control::CommandChannel::Direct, 400'000);
    KM3_CHECK(mon.WatchdogExpired(500'001));
}

KM3_TEST(ChannelSwitchResetsWatchdogInsteadOfImmediatelyTripping) {
    km3::control::SafetyMonitor mon;
    mon.ArmCommandWatchdog(0);                          // 直连，500ms
    mon.SetChannel(km3::control::CommandChannel::Relay, 400'000);
    // 若不重置，此刻已消耗 400ms 而中转阈值是 3s，尚且安全；
    // 反向才是危险侧：从中转切回直连时若不重置，会立刻假超时。
    mon.SetChannel(km3::control::CommandChannel::Direct, 2'000'000);
    KM3_CHECK(!mon.WatchdogExpired(2'000'000));
    KM3_CHECK(mon.WatchdogExpired(2'000'000 + 500'001));
}

KM3_TEST(SafetyVerdictSeverityOrderingIsStable) {
    km3::control::SafetyMonitor mon;
    mon.ArmCommandWatchdog(0);

    km3::control::SafetyChecks ok;
    ok.last_command_us = 0;
    KM3_CHECK(mon.Evaluate(ok, 1000).action == km3::control::SafetyAction::None);

    // 传感器丢失：最严重，即使同时有多个问题也应报它。
    auto bad = ok;
    bad.critical_sensors_online = false;
    bad.within_speed_limits = false;
    const auto v = mon.Evaluate(bad, 1000);
    KM3_CHECK(v.action == km3::control::SafetyAction::EmergencyStop);
    KM3_CHECK(!v.watchdog_expired);

    // 只看门狗超时。
    const auto wd = mon.Evaluate(ok, 600'000);
    KM3_CHECK(wd.action == km3::control::SafetyAction::EmergencyStop);
    KM3_CHECK(wd.watchdog_expired);
    KM3_CHECK(wd.reason == ErrorCode::ECommandTimeout);

    // 温度偏高但未越限：只告警降额，不能摔停在半空。
    auto warm = ok;
    warm.temperature_warn = true;
    KM3_CHECK(mon.Evaluate(warm, 1000).action == km3::control::SafetyAction::Warn);
}

KM3_TEST(MayFeedHardwareWatchdogIsConservativeByDefault) {
    km3::control::SafetyMonitor mon;
    mon.ArmCommandWatchdog(0);
    km3::control::SafetyChecks checks;

    KM3_CHECK(mon.MayFeedHardwareWatchdog(checks, 1000));

    // 任一安全条件不成立即不得喂狗：MAX6369 是独立于 Linux 的复位源，
    // 由"进程还活着但业务已卡死"的系统喂狗等于自废保护。
    auto bad = checks;
    bad.critical_sensors_online = false;
    KM3_CHECK(!mon.MayFeedHardwareWatchdog(bad, 1000));

    auto bad2 = checks;
    bad2.communication_healthy = false;
    // 通信不健康本身不阻止喂狗（喂狗条件表未列入），但看门狗超时会。
    KM3_CHECK(mon.MayFeedHardwareWatchdog(bad2, 1000));
    KM3_CHECK(!mon.MayFeedHardwareWatchdog(checks, 600'000));
}

KM3_TEST_MAIN()
