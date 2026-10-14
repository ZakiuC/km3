#pragma once
// 实时控制律。见 docs/架构设计/设备端程序架构.md §2.1.1 / §2.1.5。
//
// 设计边界（重要）：
//   本模块只做一件事：把一条**已经裁决过**的控制意图变成总线事务，
//   并把反馈读回来。它不做鉴权、不做命令队列、不做网络、不做序列化 ——
//   那些属于边界线程与网络层。
//   理由：实时线程必须保持无锁、无分配、无系统调用；任何"顺便"的逻辑
//   都是延迟来源，且延迟是间歇性的、只在特定时序下出现，最难复现。
//
// 实时纪律（调用方必须遵守）：
//   - 本层所有方法不得在 RT 线程内触发分配。为此内部持有**预留好的**
//     scratch 缓冲，构造期 reserve 一次，运行期只做原地覆盖。
//   - 方法一律 noexcept：异常不能穿过实时路径（见 开发规范.md §1.4）。
//   - 底层 HAL 不做内部加锁，同一总线不得被多个线程并发访问。

#include <cstdint>
#include <vector>

#include "km3/hal/can_device.hpp"
#include "km3/hal/rs485_device.hpp"
#include "km3/result.hpp"
#include "km3/rt_types.hpp"

namespace km3::control {

enum class Direction : std::uint8_t { Up = 0, Down = 1 };
enum class ThrusterId : std::uint8_t { Left = 0, Right = 1 };

enum class MotionMode : std::uint8_t {
    Idle = 0,
    Climbing,
    Positioning,
    EmergencyStop,
    SafeMode,
};

const char* ToString(Direction d) noexcept;
const char* ToString(ThrusterId t) noexcept;
const char* ToString(MotionMode m) noexcept;

// 控制律对外汇报的运动状态。POD，可直接进环形缓冲。
struct MotionState {
    std::uint64_t ts_us = 0;
    MotionMode mode = MotionMode::Idle;
    float climb_speed_mps = 0.0F;
    float position_mm = 0.0F;
    float current_a = 0.0F;
    float temperature_c = 0.0F;
    std::uint32_t rejected_commands = 0;   // 被限值拒绝的次数
    std::uint32_t bus_errors = 0;
    bool climb_limit_hit = false;
};

// 总线绑定。**逻辑名到实际节点的映射由配置与驱动层完成**，本层只认逻辑总线。
// 任何一处出现 /dev/ttyS3、can0、GPIO 号都算越界（见 hal/device.hpp 的设计意图）。
struct ControlBuses {
    // 注释里写明**具体端口**，不写"CAN-2.0B"那种形式：
    // 那既可能被读成"CAN-2 口"，也是协议名 CAN 2.0B 的误拼，
    // 而本板 CAN-2 是 can1 —— 下标差一位，驱动就会对错总线。
    ICanDevice* esc_bus = nullptr;       // 主板 can0（端口 CAN-1，CAN 2.0B）：推进风扇电调 ×2
    ICanDevice* canopen_bus = nullptr;   // 主板 can1（端口 CAN-2，CAN 2.0B）：抬降电机 A / 毛刷 / 刮条
    ICanDevice* fd_bus = nullptr;        // USB 转 CAN 模块（接口名 can2，**不是** CAN-2）：抬降电机 B，CANfd
    IRs485Device* servo_bus = nullptr;   // RS485-1：爬绳器伺服 + 吊点电机
    IRs485Device* relay_bus = nullptr;   // USB-RS485-4：继电器（开关量，**无反馈**）
};

// 软限值。这是**动作前**的准入检查，用于避免把明显越界的值发到驱动器。
// 它不替代硬件限位与驱动器自身的保护：软件限位失效时硬件仍须兜住。
struct ControlLimits {
    float climb_speed_max_mps = 0.5F;
    float thruster_percent_max = 100.0F;
    std::int32_t position_min_steps = -100000;
    std::int32_t position_max_steps = 100000;
    float current_max_a = 20.0F;
    float temperature_max_c = 80.0F;

    // 爬绳速度寄存器标度：写入值 = speed_mps / scale。
    // **待与雷赛 L7P 手册核对**：手册未归档进本仓库，此处标度是占位约定，
    // 在核对前不得用于真机动作（见文末"未验证项"）。
    float climb_speed_reg_scale = 0.001F;
    std::uint8_t climb_slave_addr = 1;
    std::uint16_t climb_speed_reg = 0x2000;
    std::uint16_t suspension_target_reg = 0x2100;
    std::uint8_t suspension_slave_addr = 2;

    // 以下标识符一律来自配置：写死在代码里会让换驱动器/换站号变成改代码。
    std::uint32_t esc_base_id = 0x100;      // 推进电调基础 CAN ID（+ThrusterId）
    float esc_percent_scale = 10.0F;        // 百分比 -> 寄存器整数（0.1% 单位）
    std::uint32_t fd_base_id = 0x180;       // USB-CAN 位置电机基础 ID（+node_id）
    std::uint8_t relay_slave_addr = 1;      // 继电器模块站号
    std::uint16_t relay_brush_reg = 0x0010; // 毛刷旋转所在继电器寄存器
};

// 抬降电机的目标位置命令。
struct LiftCommand {
    std::uint8_t node_id = 1;
    std::int32_t target_steps = 0;
};

// 控制律。
//
// 总线为空指针时，对应能力返回 EInvalidConfig —— 这是**刻意的**：
// 配置缺一路总线时，静默地"什么都不发"会让操作员以为命令生效了。
class MotionController {
public:
    MotionController(ControlBuses buses, ControlLimits limits) noexcept;

    MotionController(const MotionController&) = delete;
    MotionController& operator=(const MotionController&) = delete;

    // ---- 爬绳器（RS485-1，半双工一次事务）----
    Status ClimbRope(float speed_mps, Direction dir) noexcept;
    Status StopClimbing() noexcept;

    // ---- 吊点位置（RS485-1）----
    Status MoveSuspensionPoint(std::int32_t position_steps) noexcept;

    // ---- 推进风扇电调（CAN 2.0B）----
    Status SetThrusterSpeed(ThrusterId id, float speed_percent) noexcept;

    // ---- 抬降电机 A（主板 CAN-2，CANopen SDO）----
    Status SetLiftMotorA(const LiftCommand& cmd) noexcept;
    // ---- 抬降电机 B（USB-CAN，CANfd）----
    Status SetLiftMotorB(const LiftCommand& cmd) noexcept;

    // ---- 毛刷旋转（继电器，**只能开/关，无调速无反馈**）----
    Status SetBrushSpin(bool on) noexcept;

    // ---- 紧急停止 ----
    // 语义：**不受限值拒绝影响，尽量把停止意图送到每一条总线**。
    // 单条总线失败不回滚其它总线的停止动作，也不返回失败 ——
    // 若因一条总线故障就整体返回错误，调用方可能误判"停止未生效"而重试或放弃。
    // 实际失败只计入 bus_errors 供上报。
    Status EmergencyStop() noexcept;

    // ---- 反馈更新（由采集线程调用，非 RT 阻塞路径）----
    void UpdateFeedback(float position_mm, float current_a, float temperature_c,
                        bool climb_limit_hit, std::uint64_t now_us) noexcept;

    // 检查反馈是否越限。返回越限原因，Ok 表示在限内。
    // **本方法只判定，不动作** —— 触发停止是 SafetyMonitor 的职责，
    // 分开是为了让"判定逻辑"能被独立测试，而不是埋在副作用里。
    ErrorCode EvaluateLimits() const noexcept;

    const MotionState& State() const noexcept { return state_; }
    const ControlLimits& Limits() const noexcept { return limits_; }
    const ControlBuses& Buses() const noexcept { return buses_; }

    // 把模式置为 SafeMode（由 SafetyMonitor 在触发安全动作后调用）。
    void EnterSafeMode() noexcept;
    // 由操作员确认后清除安全状态。**只清除模式，不自动恢复动作**。
    void ClearSafeMode() noexcept;

private:
    Status SendServoRegister(std::uint8_t slave_addr, std::uint16_t reg,
                             std::uint16_t value) noexcept;
    Status SendEscPercent(ThrusterId id, float percent) noexcept;
    Status SendCanopenSdo(std::uint8_t node_id, std::uint16_t index,
                          std::uint8_t subindex, std::int32_t value) noexcept;
    Status SendFdPosition(std::uint8_t node_id, std::int32_t target) noexcept;
    Status SendRelayCoil(std::uint16_t coil, bool on) noexcept;

    ControlBuses buses_;
    ControlLimits limits_;
    MotionState state_;

    // 预留 scratch：构造期定容，运行期不分配。
    // HAL 的 ModbusWrite 签名收 const std::vector<uint16_t>&，
    // 若每次调用现构造 vector，就在 RT 线程里分配了。
    std::vector<std::uint16_t> scratch_u16_;
    std::vector<std::uint8_t> scratch_bytes_;
};

}  // namespace km3::control
