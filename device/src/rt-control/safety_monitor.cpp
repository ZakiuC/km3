#include "km3/rt_control/safety_monitor.hpp"

namespace km3::control {

const char* ToString(CommandChannel c) noexcept {
    switch (c) {
        case CommandChannel::Direct: return "direct";
        case CommandChannel::Relay: return "relay";
    }
    return "unknown";
}

SafetyMonitor::SafetyMonitor(SafetyThresholds thresholds) noexcept : thr_(thresholds) {}

std::uint64_t SafetyMonitor::WatchdogUs() const noexcept {
    return (channel_ == CommandChannel::Direct) ? thr_.watchdog_direct_us
                                                : thr_.watchdog_relay_us;
}

void SafetyMonitor::ArmCommandWatchdog(std::uint64_t now_us) noexcept {
    deadline_us_ = now_us + WatchdogUs();
    armed_ = true;
}

void SafetyMonitor::SetChannel(CommandChannel ch, std::uint64_t now_us) noexcept {
    if (ch == channel_) {
        // 同一通道重复设置不重置计时，否则频繁的"确认通道"消息会无限延长看门狗，
        // 让超时保护形同虚设。
        return;
    }
    channel_ = ch;
    // 通道变了就必须重置计时。否则直连(500ms)切到中转(3s)时，
    // 已经走掉的时间会立刻触发一次假超时，把设备摔进安全模式；
    // 反向切换同理。见 通信协议规范 §八。
    ArmCommandWatchdog(now_us);
}

bool SafetyMonitor::WatchdogExpired(std::uint64_t now_us) const noexcept {
    if (!armed_) {
        // 从未装载 = 从未建立过命令通道 = 视为已超时。
        // 保守方向是对的：启动时不该有任何执行器处于"以为有人在指挥"的状态。
        return true;
    }
    // 无符号减法天然处理回绕；单调时钟不会回退，但仍不做有符号比较。
    return static_cast<std::int64_t>(now_us - deadline_us_) > 0;
}

SafetyVerdict SafetyMonitor::Evaluate(const SafetyChecks& checks,
                                      std::uint64_t now_us) const noexcept {
    SafetyVerdict v;

    // 判定顺序按严重度，致命项先返回，避免被次要项掩盖。
    if (!checks.critical_sensors_online) {
        v.action = SafetyAction::EmergencyStop;
        v.reason = ErrorCode::EDeviceError;
        return v;
    }
    if (!checks.within_position_limits) {
        v.action = SafetyAction::EmergencyStop;
        v.reason = ErrorCode::ECommandRejected;
        return v;
    }
    if (!checks.within_current_limits) {
        v.action = SafetyAction::EmergencyStop;
        v.reason = ErrorCode::EDeviceError;
        return v;
    }
    if (!checks.temperature_normal) {
        v.action = SafetyAction::EmergencyStop;
        v.reason = ErrorCode::EDeviceError;
        return v;
    }
    if (!checks.sensor_data_valid) {
        v.action = SafetyAction::StopMotion;
        v.reason = ErrorCode::EVerifyFailed;
        return v;
    }
    if (!checks.within_speed_limits) {
        v.action = SafetyAction::StopMotion;
        v.reason = ErrorCode::ECommandRejected;
        return v;
    }
    if (!checks.communication_healthy) {
        v.action = SafetyAction::StopMotion;
        v.reason = ErrorCode::EDeviceOffline;
        return v;
    }

    if (WatchdogExpired(now_us)) {
        v.action = SafetyAction::EmergencyStop;
        v.reason = ErrorCode::ECommandTimeout;
        v.watchdog_expired = true;
        return v;
    }

    // 告警级放在最后：它不该掩盖上面任何一项。
    if (checks.temperature_warn) {
        v.action = SafetyAction::Warn;
        v.reason = ErrorCode::Ok;
        return v;
    }
    return v;
}

bool SafetyMonitor::MayFeedHardwareWatchdog(const SafetyChecks& checks,
                                            std::uint64_t now_us) const noexcept {
    // 刻意写成"全部条件成立才允许"：将来新增一条安全条件时，
    // 忘记接进来会表现为不喂狗（保守，会复位），而不是照常喂狗（危险，卡死不复位）。
    if (!checks.critical_sensors_online) return false;
    if (!checks.sensor_data_valid) return false;
    if (!checks.within_position_limits) return false;
    if (!checks.within_current_limits) return false;
    if (!checks.temperature_normal) return false;
    if (WatchdogExpired(now_us)) return false;
    return true;
}

}  // namespace km3::control
