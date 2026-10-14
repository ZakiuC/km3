#include "km3/rt_control/motion_controller.hpp"

#include <cmath>
#include <cstring>

#include "km3/clock.hpp"

namespace km3::control {
namespace {

// CANopen SDO 下行（expedited，4 字节数据）。
// 本板抬降电机走主板 CAN-2（CAN 2.0B）上的 CANopen，不是 CANfd。
void BuildSdoDownload(CanFrame& f, std::uint32_t cob_id, std::uint16_t index,
                      std::uint8_t subindex, std::int32_t value) noexcept {
    f = CanFrame{};
    f.id = cob_id;
    f.dlc = 8;
    f.data[0] = 0x23;   // 命令字：expedited download，4 字节
    f.data[1] = static_cast<std::uint8_t>(index & 0xFF);
    f.data[2] = static_cast<std::uint8_t>((index >> 8) & 0xFF);
    f.data[3] = subindex;
    const std::uint32_t v = static_cast<std::uint32_t>(value);
    f.data[4] = static_cast<std::uint8_t>(v & 0xFF);
    f.data[5] = static_cast<std::uint8_t>((v >> 8) & 0xFF);
    f.data[6] = static_cast<std::uint8_t>((v >> 16) & 0xFF);
    f.data[7] = static_cast<std::uint8_t>((v >> 24) & 0xFF);
}

std::uint16_t FloatToReg(float value) noexcept {
    const float r = std::nearbyintf(value);
    if (r <= 0.0F) return 0;
    if (r >= 65535.0F) return 65535;
    return static_cast<std::uint16_t>(r);
}

}  // namespace

const char* ToString(Direction d) noexcept {
    switch (d) {
        case Direction::Up: return "up";
        case Direction::Down: return "down";
    }
    return "unknown";
}

const char* ToString(ThrusterId t) noexcept {
    switch (t) {
        case ThrusterId::Left: return "left";
        case ThrusterId::Right: return "right";
    }
    return "unknown";
}

const char* ToString(MotionMode m) noexcept {
    switch (m) {
        case MotionMode::Idle: return "idle";
        case MotionMode::Climbing: return "climbing";
        case MotionMode::Positioning: return "positioning";
        case MotionMode::EmergencyStop: return "emergency_stop";
        case MotionMode::SafeMode: return "safe_mode";
    }
    return "unknown";
}

MotionController::MotionController(ControlBuses buses, ControlLimits limits) noexcept
    : buses_(buses), limits_(limits) {
    // 预留 scratch，运行期不再分配。见头文件的实时纪律说明。
    scratch_u16_.reserve(4);
    scratch_bytes_.reserve(8);
    state_.ts_us = MonotonicUs();
}

Status MotionController::SendServoRegister(std::uint8_t slave_addr, std::uint16_t reg,
                                           std::uint16_t value) noexcept {
    if (buses_.servo_bus == nullptr) {
        // 配置缺这一路时的显式失败。静默返回 Ok 会让操作员以为命令生效。
        return Status(ErrorCode::EInvalidConfig);
    }
    scratch_u16_.assign(1, value);
    const Status st = buses_.servo_bus->ModbusWrite(
        slave_addr, ModbusFunction::WriteSingleRegister, reg, scratch_u16_);
    if (!st.ok()) {
        ++state_.bus_errors;
    }
    return st;
}

Status MotionController::ClimbRope(float speed_mps, Direction dir) noexcept {
    const float mag = std::fabs(speed_mps);
    if (!(mag <= limits_.climb_speed_max_mps)) {
        // 先准入再动作。越限不发送任何帧 —— 把越界值交给驱动器"帮我们兜"，
        // 是把软件缺陷变成机械事故。
        ++state_.rejected_commands;
        return Status(ErrorCode::ECommandRejected);
    }
    const std::uint16_t reg = FloatToReg(mag / limits_.climb_speed_reg_scale);
    const Status st = SendServoRegister(limits_.climb_slave_addr,
                                        limits_.climb_speed_reg, reg);
    if (st.ok()) {
        state_.climb_speed_mps = (dir == Direction::Up) ? mag : -mag;
        state_.mode = MotionMode::Climbing;
    }
    return st;
}

Status MotionController::StopClimbing() noexcept {
    const Status st = SendServoRegister(limits_.climb_slave_addr,
                                        limits_.climb_speed_reg, 0);
    if (st.ok()) {
        state_.climb_speed_mps = 0.0F;
        if (state_.mode == MotionMode::Climbing) {
            state_.mode = MotionMode::Idle;
        }
    }
    return st;
}

Status MotionController::MoveSuspensionPoint(std::int32_t position_steps) noexcept {
    if (position_steps < limits_.position_min_steps ||
        position_steps > limits_.position_max_steps) {
        ++state_.rejected_commands;
        return Status(ErrorCode::ECommandRejected);
    }
    // 目标位置是带符号量，用 16 位寄存器表达时会有截断风险，
    // 因此这里把步数按低位原样下发，量纲与符号约定**待与 IM42ET-P 手册核对**。
    const std::uint16_t reg =
        static_cast<std::uint16_t>(position_steps & 0xFFFF);
    const Status st = SendServoRegister(limits_.suspension_slave_addr,
                                        limits_.suspension_target_reg, reg);
    if (st.ok()) {
        state_.mode = MotionMode::Positioning;
    }
    return st;
}

Status MotionController::SendEscPercent(ThrusterId id, float percent) noexcept {
    if (buses_.esc_bus == nullptr) {
        return Status(ErrorCode::EInvalidConfig);
    }
    CanFrame f{};
    f.id = limits_.esc_base_id + static_cast<std::uint32_t>(id);
    f.dlc = 2;
    const std::uint16_t scaled = FloatToReg(percent * limits_.esc_percent_scale);
    f.data[0] = static_cast<std::uint8_t>(scaled & 0xFF);
    f.data[1] = static_cast<std::uint8_t>((scaled >> 8) & 0xFF);
    const Status st = buses_.esc_bus->Send(f);
    if (!st.ok()) {
        ++state_.bus_errors;
    }
    return st;
}

Status MotionController::SetThrusterSpeed(ThrusterId id, float speed_percent) noexcept {
    if (!(std::fabs(speed_percent) <= limits_.thruster_percent_max)) {
        ++state_.rejected_commands;
        return Status(ErrorCode::ECommandRejected);
    }
    return SendEscPercent(id, speed_percent);
}

Status MotionController::SendCanopenSdo(std::uint8_t node_id, std::uint16_t index,
                                        std::uint8_t subindex,
                                        std::int32_t value) noexcept {
    if (buses_.canopen_bus == nullptr) {
        return Status(ErrorCode::EInvalidConfig);
    }
    CanFrame f{};
    BuildSdoDownload(f, 0x600U + node_id, index, subindex, value);
    const Status st = buses_.canopen_bus->Send(f);
    if (!st.ok()) {
        ++state_.bus_errors;
    }
    return st;
}

Status MotionController::SetLiftMotorA(const LiftCommand& cmd) noexcept {
    if (cmd.target_steps < limits_.position_min_steps ||
        cmd.target_steps > limits_.position_max_steps) {
        ++state_.rejected_commands;
        return Status(ErrorCode::ECommandRejected);
    }
    return SendCanopenSdo(cmd.node_id, 0x607A, 0x00, cmd.target_steps);
}

Status MotionController::SendFdPosition(std::uint8_t node_id,
                                        std::int32_t target) noexcept {
    if (buses_.fd_bus == nullptr) {
        return Status(ErrorCode::EInvalidConfig);
    }
    CanFrame f{};
    f.id = limits_.fd_base_id + node_id;
    f.fd = true;          // 仅 USB-CAN 后端可用
    f.dlc = 8;
    f.data[0] = 0x01;     // 私有协议命令字，**待与 P65MAX-S 手册核对**
    const std::uint32_t v = static_cast<std::uint32_t>(target);
    f.data[1] = static_cast<std::uint8_t>(v & 0xFF);
    f.data[2] = static_cast<std::uint8_t>((v >> 8) & 0xFF);
    f.data[3] = static_cast<std::uint8_t>((v >> 16) & 0xFF);
    f.data[4] = static_cast<std::uint8_t>((v >> 24) & 0xFF);
    const Status st = buses_.fd_bus->Send(f);
    if (!st.ok()) {
        ++state_.bus_errors;
    }
    return st;
}

Status MotionController::SetLiftMotorB(const LiftCommand& cmd) noexcept {
    if (buses_.fd_bus == nullptr) {
        return Status(ErrorCode::EInvalidConfig);
    }
    // 能力校验：标称 CANfd 的设备接到主板 CAN（仅 2.0B）是**接线错误**，
    // 软件不能"兼容"它。这里在动作前拒绝，而不是让帧悄无声息地发不出去。
    if (!buses_.fd_bus->Capabilities().supports_fd) {
        ++state_.rejected_commands;
        return Status(ErrorCode::EInvalidRequest);
    }
    if (cmd.target_steps < limits_.position_min_steps ||
        cmd.target_steps > limits_.position_max_steps) {
        ++state_.rejected_commands;
        return Status(ErrorCode::ECommandRejected);
    }
    return SendFdPosition(cmd.node_id, cmd.target_steps);
}

Status MotionController::SendRelayCoil(std::uint16_t reg, bool on) noexcept {
    if (buses_.relay_bus == nullptr) {
        return Status(ErrorCode::EInvalidConfig);
    }
    scratch_u16_.assign(1, on ? 1 : 0);
    const Status st = buses_.relay_bus->ModbusWrite(
        limits_.relay_slave_addr, ModbusFunction::WriteSingleRegister, reg,
        scratch_u16_);
    if (!st.ok()) {
        ++state_.bus_errors;
    }
    return st;
}

Status MotionController::SetBrushSpin(bool on) noexcept {
    // 毛刷旋转经继电器通断：**只能开/关，无调速、无反馈**。
    // 因此这里不维护任何"毛刷当前状态"的权威副本 —— 无反馈的开关量，
    // 软件记录的"状态"只是我们发过什么，不是机构实际在做什么。
    return SendRelayCoil(limits_.relay_brush_reg, on);
}

Status MotionController::EmergencyStop() noexcept {
    state_.mode = MotionMode::EmergencyStop;
    state_.climb_speed_mps = 0.0F;

    // 尽力而为：一条总线失败不阻断其它总线的停止动作，也不改返回值。
    // 若因一条总线故障就整体返回失败，调用方可能误判"停止未生效"而重试或放弃，
    // 反而让真正生效的那几条总线的停止被忽略。失败只计数，供上报。
    if (buses_.servo_bus != nullptr) {
        scratch_u16_.assign(1, 0);
        if (!buses_.servo_bus->ModbusWrite(limits_.climb_slave_addr,
                                           ModbusFunction::WriteSingleRegister,
                                           limits_.climb_speed_reg,
                                           scratch_u16_).ok()) {
            ++state_.bus_errors;
        }
    }
    if (buses_.esc_bus != nullptr) {
        CanFrame f{};
        f.id = limits_.esc_base_id;
        f.dlc = 2;
        f.data[0] = 0;
        f.data[1] = 0;
        // 广播停机：两台电调都要停，发送失败不阻断后续。
        if (!buses_.esc_bus->Send(f).ok()) ++state_.bus_errors;
    }
    if (buses_.relay_bus != nullptr) {
        scratch_u16_.assign(1, 0);
        if (!buses_.relay_bus->ModbusWrite(limits_.relay_slave_addr,
                                           ModbusFunction::WriteSingleRegister,
                                           limits_.relay_brush_reg,
                                           scratch_u16_).ok()) {
            ++state_.bus_errors;
        }
    }
    return Status::Ok();
}

void MotionController::UpdateFeedback(float position_mm, float current_a,
                                      float temperature_c, bool climb_limit_hit,
                                      std::uint64_t now_us) noexcept {
    state_.ts_us = now_us;
    state_.position_mm = position_mm;
    state_.current_a = current_a;
    state_.temperature_c = temperature_c;
    state_.climb_limit_hit = climb_limit_hit;
}

ErrorCode MotionController::EvaluateLimits() const noexcept {
    // 只判定，不动作。触发停止由 SafetyMonitor 裁决 —— 分开才能单独测试判定逻辑。
    if (state_.position_mm < 0.0F ||
        state_.position_mm > static_cast<float>(limits_.position_max_steps)) {
        return ErrorCode::ECommandRejected;
    }
    if (state_.current_a > limits_.current_max_a) {
        return ErrorCode::EDeviceError;
    }
    if (state_.temperature_c > limits_.temperature_max_c) {
        return ErrorCode::EDeviceError;
    }
    if (state_.climb_limit_hit) {
        return ErrorCode::ECommandRejected;
    }
    return ErrorCode::Ok;
}

void MotionController::EnterSafeMode() noexcept { state_.mode = MotionMode::SafeMode; }

void MotionController::ClearSafeMode() noexcept {
    // 只清模式，不自动恢复动作：操作员确认后必须重新下发命令。
    if (state_.mode == MotionMode::SafeMode ||
        state_.mode == MotionMode::EmergencyStop) {
        state_.mode = MotionMode::Idle;
    }
}

}  // namespace km3::control
