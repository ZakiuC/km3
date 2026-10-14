#pragma once
// 安全监控。见 docs/架构设计/设备端程序架构.md §2.1.5。
//
// 职责边界：
//   本模块**只做判定与裁决**，不直接碰总线 —— 触发停止时它请求
//   MotionController::EmergencyStop()，而不是自己去发帧。
//   这样"判定"可以脱离硬件被完整测试，也避免两处都能发停止帧而互相打架。
//
// 与硬件看门狗（MAX6369）的关系：
//   本模块决定"是否允许喂狗"，但**不直接喂**。MAX6369 是独立于 Linux 的
//   复位源，用于内核级卡死；把它绑到本进程的某个循环上会让"进程活着但
//   业务卡死"继续喂狗。喂狗条件必须由能判断"关键功能是否持续前进"的
//   监督者裁决，见 §2.1.5 的喂狗条件表。
//
// 全部状态为 POD，可在 RT 线程内使用，无分配、无锁。

#include <cstdint>

#include "km3/error_code.hpp"
#include "km3/result.hpp"

namespace km3::control {

// 当前命令通道。看门狗阈值随通道变化（通信协议规范 §八）。
enum class CommandChannel : std::uint8_t {
    Direct = 0,   // G30 ↔ 设备直连
    Relay = 1,    // 服务器中转 / G30 桥接
};

const char* ToString(CommandChannel c) noexcept;

// 安全动作。区分"停机"与"仅告警"很重要：
// 温度轻微超标应告警并降额，而不是直接摔停在半空。
enum class SafetyAction : std::uint8_t {
    None = 0,
    Warn,
    StopMotion,       // 停止运动，保持当前姿态
    EmergencyStop,    // 立即停止所有执行器
};

// 检查项。POD，逐项可测。
struct SafetyChecks {
    bool critical_sensors_online = true;
    bool sensor_data_valid = true;
    bool within_position_limits = true;
    bool within_speed_limits = true;
    bool within_current_limits = true;
    bool temperature_normal = true;
    bool temperature_warn = false;   // 已偏高但未越限：降额告警，不摔停
    bool communication_healthy = true;
    std::uint64_t last_command_us = 0;
};

// 裁决结果。含触发原因与建议动作，便于上报与测试断言具体原因。
struct SafetyVerdict {
    SafetyAction action = SafetyAction::None;
    ErrorCode reason = ErrorCode::Ok;
    bool watchdog_expired = false;

    bool Trip() const noexcept { return action >= SafetyAction::StopMotion; }
};

// 看门狗与安全阈值。全部来自配置快照，不在代码里写死。
struct SafetyThresholds {
    std::uint64_t watchdog_direct_us = 500000;    // 0.5s
    std::uint64_t watchdog_relay_us = 3000000;     // 3.0s
    float position_min_mm = 0.0F;
    float position_max_mm = 100000.0F;
    float speed_max_mps = 0.5F;
    float current_max_a = 20.0F;
    float temperature_max_c = 80.0F;
    float temperature_warn_c = 65.0F;
};

class SafetyMonitor {
public:
    SafetyMonitor() noexcept = default;
    explicit SafetyMonitor(SafetyThresholds thresholds) noexcept;

    // ---- 看门狗 ----
    // 装载/重置计时。任何一条**已生效**的命令（含被拒但已收到的）都算一次喂狗，
    // 因为看门狗的语义是"命令通道还活着"，不是"命令被接受了"。
    void ArmCommandWatchdog(std::uint64_t now_us) noexcept;

    // 切换通道。**必须重置计时**：否则从直连（500ms）切到中转（3s）时，
    // 已经走掉的时间会立刻触发一次假超时，把设备摔进安全模式。
    // 反过来从 3s 切到 500ms 同理。见 通信协议规范 §八。
    void SetChannel(CommandChannel ch, std::uint64_t now_us) noexcept;

    CommandChannel Channel() const noexcept { return channel_; }
    std::uint64_t WatchdogUs() const noexcept;
    std::uint64_t DeadlineUs() const noexcept { return deadline_us_; }

    // ---- 判定 ----
    // 传入本周期采集到的检查项与当前单调时刻，返回裁决。
    // 判定顺序按严重度：先致命的（传感器丢失、越限、超温），后者不掩盖前者。
    SafetyVerdict Evaluate(const SafetyChecks& checks, std::uint64_t now_us) const noexcept;

    // 看门狗是否已超时（单独的判定，便于在无完整检查项时也能测）。
    bool WatchdogExpired(std::uint64_t now_us) const noexcept;

    // ---- 喂硬件看门狗的许可 ----
    // 只有全部条件成立才允许喂。任一不成立即返回 false，让 MAX6369 复位。
    // 这是**有意做成否定式**：新增一个安全条件时，忘记接进来会表现为"不喂狗"
    // （保守、会复位），而不是"照常喂狗"（危险、卡死不复位）。
    bool MayFeedHardwareWatchdog(const SafetyChecks& checks, std::uint64_t now_us) const noexcept;

    const SafetyThresholds& Thresholds() const noexcept { return thr_; }

private:
    SafetyThresholds thr_{};
    CommandChannel channel_ = CommandChannel::Direct;
    std::uint64_t deadline_us_ = 0;
    bool armed_ = false;
};

}  // namespace km3::control
