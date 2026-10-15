// PMP4010 非实时驱动边界。所有 CANopen 帧与 SDO 事务由标准层处理。

#include "pmp4010_driver.hpp"

#include <algorithm>
#include <utility>
#include <cstring>
#include <limits>
#include "od_access_generated.hpp"

namespace km3::pmp4010 {

namespace {

// 后端必须是主板 SocketCAN。
//
// 硬件事实（docs/架构设计/设备端程序架构.md，实测）：主板两路 CAN 由 SPI 外挂
// SIT2515T 实现，仅 CAN 2.0B，不支持 CAN FD。CANopen 设备走主板 CAN，
// CANfd 设备（P65MAX-S）才走 USB 转 CAN。
// 反过来把 CANopen 设备接到 USB-CAN 是选型错误，不是软件能"兼容"的：
// 同一个 SocketCAN 抽象下的两个后端能力不同（hal/can_device.hpp 开篇即说明）。
Status CheckBackend(const ICanDevice& device) {
    if (device.Backend() != CanBackend::SocketCan) {
        return Status(ErrorCode::EInvalidRequest);
    }
    if (device.Capabilities().supports_fd) {
        // SocketCan 后端却声称支持 FD：说明配置接线与驱动实现不一致，
        // 此时"能用"的表象最危险。拒绝，而不是将就用。
        return Status(ErrorCode::EInvalidConfig);
    }
    return Status::Ok();
}

}  // namespace

Pmp4010Driver::Pmp4010Driver(ICanDevice& device, DriverConfig config)
    : device_(device), config_(std::move(config)), sdo_(device) {}

Pmp4010Driver::~Pmp4010Driver() = default;

bool Pmp4010Driver::NodeIdValid(std::uint8_t node_id) const noexcept {
    if (node_id == 0 || node_id > 127) {
        return false;  // 0 是 NMT 广播地址，不是节点地址
    }
    const auto& nodes = config_.nodes;
    return std::any_of(nodes.begin(), nodes.end(),
                       [node_id](const NodeConfig& n) { return n.node_id == node_id; });
}

Status Pmp4010Driver::Open() noexcept {
    // 重新核对 HAL 状态，避免后端已被持有者关闭时仍报告打开成功。
    open_ = false;
    if (!device_.IsOpen()) {
        // HAL 不做内部加锁，也**不替调用方打开设备**：由持有者决定何时打开。
        return Status(ErrorCode::EDeviceOffline);
    }

    const Status backend = CheckBackend(device_);
    if (!backend.ok()) {
        return backend;
    }

    if (config_.logical_bus.empty() || config_.logical_bus != device_.LogicalName()) {
        return Status(ErrorCode::EInvalidConfig);
    }

    // 只校验配置中的地址是否合法且唯一；硬件实际地址由只读探测另行核对。
    for (std::size_t i = 0; i < config_.nodes.size(); ++i) {
        const std::uint8_t id = config_.nodes[i].node_id;
        if (id == 0 || id > 127) {
            return Status(ErrorCode::EInvalidConfig);
        }
        for (std::size_t j = i + 1; j < config_.nodes.size(); ++j) {
            if (config_.nodes[j].node_id == id) {
                return Status(ErrorCode::EInvalidConfig);
            }
        }
    }

    // 没有物理量换算入口，不在这里发明标度或运行参数约束。
    open_ = true;
    return Status::Ok();
}

Status Pmp4010Driver::Close() noexcept {
    // 生命周期不隐式发停机报文，也不关闭由调用方持有的 HAL。
    open_ = false;
    return Status::Ok();
}

Status Pmp4010Driver::GuardTransmit(std::uint8_t node_id, bool allow_broadcast) noexcept {
    preserved_abort_code_ = 0;
    if (!kOdVerified) {
        // 唯一正确的行为：拒发，并计数。零字节出总线。
        ++stats_.rejected_by_od_guard;
        return Status(kOdGuardError);
    }
    if (!IsOpen()) {
        ++stats_.rejected_by_validation;
        return Status(ErrorCode::EDeviceOffline);
    }
    if (!(allow_broadcast && node_id == 0) && !NodeIdValid(node_id)) {
        ++stats_.rejected_by_validation;
        return Status(ErrorCode::EInvalidRequest);
    }
    return Status::Ok();
}

namespace {
namespace wire = canopen::wire;
const od::Access* Find(const char* key) noexcept {
    for (const auto& item : od::kAccess) if (std::strcmp(item.key, key) == 0) return &item;
    return nullptr;
}
const od::Access* Find(std::uint16_t index, std::uint8_t sub) noexcept {
    for (const auto& item : od::kAccess) {
        const auto& entry = od::kEntries[item.entry];
        if (entry.verified && entry.index != od::kIndexUnset && entry.index == index && entry.sub == sub) return &item;
    }
    return nullptr;
}
std::int32_t Signed32(std::uint32_t value) noexcept {
    std::int32_t result;
    std::memcpy(&result, &value, sizeof(result));
    return result;
}
bool ValidMode(std::int8_t mode) noexcept {
    return mode == 1 || mode == 2 || mode == 3 || mode == 4 || mode == 6 ||
           mode == 7 || mode == 8 || mode == 9 || mode == 10;
}
void PollPause(std::uint64_t deadline) noexcept {
    SleepUntilMonotonic(std::min(deadline, MonotonicUs() + 1000u));
}
}  // namespace

DriverStats Pmp4010Driver::Stats() const noexcept {
    auto result = stats_;
    const auto transfer = sdo_.Stats();
    result.tx_frames = transfer.tx_frames;
    result.rx_frames = transfer.rx_frames;
    result.sdo_timeouts = transfer.timeouts;
    return result;
}
std::uint64_t Pmp4010Driver::Deadline() const noexcept {
    return MonotonicUs() + std::uint64_t{config_.sdo_timeout_ms} * 1000u;
}
Result<Pmp4010Driver::SdoBuffer> Pmp4010Driver::ReadRaw(std::uint8_t node, std::uint16_t index,
        std::uint8_t sub, std::uint64_t deadline) {
    if (config_.sdo_timeout_ms == 0) return Result<SdoBuffer>::Err(ErrorCode::EInvalidConfig);
    const auto* item = Find(index, sub);
    if (!item) return Result<SdoBuffer>::Err(ErrorCode::EInvalidRequest);
    auto result = sdo_.Upload(node, index, sub, deadline);
    if (result && (item->variable_length ? result.value().size > item->width :
                                         result.value().size != item->width))
        return Result<SdoBuffer>::Err(ErrorCode::EDeviceError);
    return result;
}
Result<std::uint32_t> Pmp4010Driver::ReadValue(std::uint8_t node, const char* key, std::uint64_t deadline) {
    const auto* item = Find(key);
    if (!item || item->variable_length) return Result<std::uint32_t>::Err(ErrorCode::EInvalidConfig);
    const auto& entry = od::kEntries[item->entry];
    auto bytes = ReadRaw(node, entry.index, entry.sub, deadline);
    if (!bytes) return Result<std::uint32_t>::Err(bytes.code());
    return Result<std::uint32_t>::Ok(canopen::Decode32(bytes.value().bytes.data()));
}
Status Pmp4010Driver::WriteValue(std::uint8_t node, const char* key, std::uint32_t value,
        std::uint64_t deadline) {
    const auto* item = Find(key);
    if (!item || !item->writable || config_.sdo_timeout_ms == 0) return Status(ErrorCode::EInvalidConfig);
    // All numeric write entry points converge here, including generic SDO writes.
    if (std::strcmp(key, "controlword") == 0) {
        const auto cia402 = RequireCia402(node, deadline);
        if (!cia402) return cia402;
    } else if (std::strcmp(key, "target_position") == 0) {
        const auto limits = RequirePositionInRange(node, Signed32(value), deadline);
        if (!limits) return limits;
    } else if (std::strcmp(key, "target_velocity") == 0) {
        // E06 §5.2: user units/s -> rpm needs 608Fh and 6091h. The
        // driver has no verified conversion chain, so 6080h cannot be compared.
        return Status(ErrorCode::EInvalidConfig);
    }
    const auto& entry = od::kEntries[item->entry];
    std::array<std::uint8_t, 4> bytes{};
    canopen::Encode32(bytes.data(), value);
    return sdo_.Download(node, entry.index, entry.sub, bytes.data(), item->width, deadline);
}
Status Pmp4010Driver::Control(std::uint8_t node, std::uint16_t value, std::uint64_t deadline) {
    return WriteValue(node, "controlword", value, deadline);
}

Result<Pmp4010Driver::SdoBuffer> Pmp4010Driver::ReadBytes(std::uint8_t node, std::uint16_t index, std::uint8_t sub) {
    const auto guard = GuardTransmit(node);
    if (!guard) return Result<SdoBuffer>::Err(guard.code());
    return ReadRaw(node, index, sub, Deadline());
}
Result<Pmp4010Driver::SdoBuffer> Pmp4010Driver::ReadString(std::uint8_t node, std::uint16_t index, std::uint8_t sub) {
    const auto guard = GuardTransmit(node);
    if (!guard) return Result<SdoBuffer>::Err(guard.code());
    const auto* item = Find(index, sub);
    if (!item || !item->variable_length) return Result<SdoBuffer>::Err(ErrorCode::EInvalidRequest);
    return ReadRaw(node, index, sub, Deadline());
}
Result<std::uint32_t> Pmp4010Driver::ReadObject(std::uint8_t node, std::uint16_t index, std::uint8_t sub) {
    auto result = ReadBytes(node, index, sub);
    if (!result) return Result<std::uint32_t>::Err(result.code());
    if (result.value().size == 0 || result.value().size > 4) return Result<std::uint32_t>::Err(ErrorCode::EDeviceError);
    return Result<std::uint32_t>::Ok(canopen::Decode32(result.value().bytes.data()));
}
Status Pmp4010Driver::WriteObject(std::uint8_t node, std::uint16_t index, std::uint8_t sub, std::uint32_t value) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto* item = Find(index, sub);
    if (!item || !item->writable) return Status(ErrorCode::EInvalidRequest);
    // Generic values must fit the on-wire width; signed callers use typed methods.
    if (item->width < 4 && value >= (std::uint32_t{1} << (item->width * 8u))) return Status(ErrorCode::EInvalidRequest);
    return WriteValue(node, item->key, value, Deadline());
}
Status Pmp4010Driver::SdoRead(std::uint8_t node, std::uint16_t index, std::uint8_t sub,
                             std::vector<std::uint8_t>& out) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    if (out.capacity() < SdoBuffer::kCapacity) return Status(ErrorCode::EInvalidRequest);
    auto result = ReadRaw(node, index, sub, Deadline());
    if (!result) return Status(result.code());
    out.resize(result.value().size); // Never allocates: capacity checked before any I/O.
    std::copy_n(result.value().bytes.begin(), result.value().size, out.begin());
    return Status::Ok();
}
Status Pmp4010Driver::SdoWrite(std::uint8_t node, std::uint16_t index, std::uint8_t sub,
                              const std::vector<std::uint8_t>& data) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto* item = Find(index, sub);
    if (!item || !item->writable || data.size() != item->width) return Status(ErrorCode::EInvalidRequest);
    if (config_.sdo_timeout_ms == 0) return Status(ErrorCode::EInvalidConfig);
    std::array<std::uint8_t, 4> bytes{};
    std::copy(data.begin(), data.end(), bytes.begin());
    return WriteValue(node, item->key, canopen::Decode32(bytes.data()), Deadline());
}
Status Pmp4010Driver::SendNmt(NmtCommand command, std::uint8_t node) {
    const auto guard = GuardTransmit(node, true);
    return guard ? sdo_.Nmt(static_cast<std::uint8_t>(command), node) : guard;
}

Status Pmp4010Driver::WaitState(std::uint8_t node, std::uint16_t expected, std::uint64_t deadline) {
    while (MonotonicUs() < deadline) {
        auto sw = ReadValue(node, "statusword", deadline);
        if (!sw) return Status(sw.code());
        const auto value = static_cast<std::uint16_t>(sw.value());
        if (canopen::StateIs(value, expected) || (expected == wire::CIA402_DISABLED && canopen::Disabled(value)))
            return Status::Ok();
        if ((value & wire::CIA402_FAULT) != 0) return Status(ErrorCode::EDeviceError);
        PollPause(deadline);
    }
    return Status(ErrorCode::ECommandTimeout);
}
Status Pmp4010Driver::Reset(std::uint8_t node, std::uint64_t deadline) {
    auto st = Control(node, wire::CIA402_DISABLE, deadline); // de-energize and clear bit7 first
    if (!st) return st;
    st = Control(node, wire::CIA402_FAULT_RESET, deadline);
    if (!st) return st;
    while (MonotonicUs() < deadline) {
        const auto sw = ReadValue(node, "statusword", deadline);
        if (!sw) return Status(sw.code());
        if ((sw.value() & wire::CIA402_FAULT) == 0) return Control(node, wire::CIA402_DISABLE, deadline);
        PollPause(deadline);
    }
    return Status(ErrorCode::ECommandTimeout);
}
Status Pmp4010Driver::FaultReset(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    return guard ? Reset(node, Deadline()) : guard;
}
Status Pmp4010Driver::Shutdown(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto deadline = Deadline();
    const auto st = Control(node, wire::CIA402_SHUTDOWN, deadline);
    return st ? WaitState(node, wire::CIA402_READY, deadline) : st;
}
Status Pmp4010Driver::SwitchOn(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto deadline = Deadline();
    const auto st = Control(node, wire::CIA402_SWITCH_ON, deadline);
    return st ? WaitState(node, wire::CIA402_SWITCHED_ON, deadline) : st;
}
Status Pmp4010Driver::RequireCia402(std::uint8_t node, std::uint64_t deadline) {
    const auto selected = ReadValue(node, "control_mode_select", deadline);
    if (!selected) return Status(selected.code());
    return selected.value() == 0 ? Status::Ok() : Status(ErrorCode::EInvalidConfig);
}
Status Pmp4010Driver::RequirePositionInRange(std::uint8_t node, std::int32_t value,
        std::uint64_t deadline) {
    const auto minimum = ReadValue(node, "software_position_limit", deadline);
    if (!minimum) return Status(minimum.code());
    const auto maximum = ReadValue(node, "software_position_limit_max", deadline);
    if (!maximum) return Status(maximum.code());
    const auto low = Signed32(minimum.value()), high = Signed32(maximum.value());
    if (low > high) return Status(ErrorCode::EInvalidConfig);
    return value >= low && value <= high ? Status::Ok() : Status(ErrorCode::EInvalidRequest);
}
Status Pmp4010Driver::RequireEnabled(std::uint8_t node, std::uint64_t deadline) {
    const auto sw = ReadValue(node, "statusword", deadline);
    if (!sw) return Status(sw.code());
    return canopen::StateIs(static_cast<std::uint16_t>(sw.value()), wire::CIA402_ENABLED) ?
           Status::Ok() : Status(ErrorCode::ECommandRejected);
}
Status Pmp4010Driver::EnableOperation(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto deadline = Deadline();
    const auto mode = ReadValue(node, "modes_of_operation_display", deadline);
    if (!mode) return Status(mode.code());
    const auto mode_value = static_cast<std::int8_t>(mode.value());
    if (!ValidMode(mode_value) || mode_value == 2 || mode_value == 7) return Status(ErrorCode::EInvalidConfig);
    auto st = Control(node, wire::CIA402_ENABLE, deadline);
    if (st) st = WaitState(node, wire::CIA402_ENABLED, deadline);
    if (!st) CleanupStop(node);
    return st;
}
Status Pmp4010Driver::EnableSequence(std::uint8_t node, std::uint64_t deadline) {
    // Read the conflicting vendor selector instead of assuming a CiA402 factory default.
    const auto selected = RequireCia402(node, deadline);
    if (!selected) return selected;
    auto mode = ReadValue(node, "modes_of_operation_display", deadline);
    if (!mode) return Status(mode.code());
    const auto m = static_cast<std::int8_t>(mode.value());
    if (!ValidMode(m) || m == 2 || m == 7) return Status(ErrorCode::EInvalidConfig);
    auto sw = ReadValue(node, "statusword", deadline);
    if (!sw) return Status(sw.code());
    while (canopen::FaultReaction(static_cast<std::uint16_t>(sw.value())) || (sw.value() & wire::CIA402_DISABLED_MASK) == 0) {
        if (MonotonicUs() >= deadline) return Status(ErrorCode::ECommandTimeout);
        PollPause(deadline);
        sw = ReadValue(node, "statusword", deadline);
        if (!sw) return Status(sw.code());
    }
    if (canopen::StateIs(static_cast<std::uint16_t>(sw.value()), wire::CIA402_ENABLED)) return Status::Ok();
    if (canopen::Fault(static_cast<std::uint16_t>(sw.value()))) {
        const auto st = Reset(node, deadline);
        if (!st) return st;
    }
    for (const auto command : {wire::CIA402_SHUTDOWN, wire::CIA402_SWITCH_ON,
                              wire::CIA402_ENABLE}) {
        auto st = Control(node, static_cast<std::uint16_t>(command), deadline);
        if (!st) { if (command == wire::CIA402_ENABLE) CleanupStop(node); return st; }
        st = WaitState(node, static_cast<std::uint16_t>(command == wire::CIA402_SHUTDOWN ? wire::CIA402_READY :
                      command == wire::CIA402_SWITCH_ON ? wire::CIA402_SWITCHED_ON : wire::CIA402_ENABLED), deadline);
        if (!st) { if (command == wire::CIA402_ENABLE) CleanupStop(node); return st; }
    }
    return Status::Ok();
}
Status Pmp4010Driver::Enable(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    return guard ? EnableSequence(node, Deadline()) : guard;
}
Status Pmp4010Driver::Disable(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto deadline = Deadline();
    const auto st = Control(node, wire::CIA402_DISABLE, deadline);
    return st ? WaitState(node, wire::CIA402_DISABLED, deadline) : st;
}
Status Pmp4010Driver::QuickStop(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto deadline = Deadline();
    const auto st = Control(node, wire::CIA402_QUICK_STOP_COMMAND, deadline);
    if (!st) return st;
    while (MonotonicUs() < deadline) {
        const auto sw = ReadValue(node, "statusword", deadline);
        if (!sw) return Status(sw.code());
        const auto value = static_cast<std::uint16_t>(sw.value());
        if (canopen::StateIs(value, wire::CIA402_QUICK_STOP) || canopen::Disabled(value)) return Status::Ok();
        if ((value & wire::CIA402_FAULT) != 0) return Status(ErrorCode::EDeviceError);
        PollPause(deadline);
    }
    return Status(ErrorCode::ECommandTimeout);
}
Status Pmp4010Driver::Stop(std::uint8_t node) { return QuickStop(node); }
void Pmp4010Driver::CleanupStop(std::uint8_t node) {
    const auto original_abort = LastSdoAbortCode();
    if (!QuickStop(node)) ++stats_.cleanup_failures;
    if (original_abort != 0) preserved_abort_code_ = original_abort;
}

Result<std::uint16_t> Pmp4010Driver::ReadStatusword(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return Result<std::uint16_t>::Err(guard.code());
    const auto result = ReadValue(node, "statusword", Deadline());
    return result ? Result<std::uint16_t>::Ok(static_cast<std::uint16_t>(result.value())) : Result<std::uint16_t>::Err(result.code());
}
Result<std::int8_t> Pmp4010Driver::GetOperationMode(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return Result<std::int8_t>::Err(guard.code());
    const auto result = ReadValue(node, "modes_of_operation_display", Deadline());
    return result ? Result<std::int8_t>::Ok(static_cast<std::int8_t>(result.value())) : Result<std::int8_t>::Err(result.code());
}
Status Pmp4010Driver::SetOperationMode(std::uint8_t node, std::int8_t mode) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    if (!ValidMode(mode)) return Status(ErrorCode::EInvalidRequest);
    const auto deadline = Deadline();
    auto sw = ReadValue(node, "statusword", deadline);
    if (!sw) return Status(sw.code());
    // Use the manual's conservative disabled-only policy, including quick-stop/fault states.
    const auto value = static_cast<std::uint16_t>(sw.value());
    if (!canopen::Disabled(value) && !canopen::StateIs(value, wire::CIA402_READY) &&
        !canopen::StateIs(value, wire::CIA402_SWITCHED_ON)) return Status(ErrorCode::ECommandRejected);
    auto st = WriteValue(node, "modes_of_operation", static_cast<std::uint8_t>(mode), deadline);
    if (!st) return st;
    while (MonotonicUs() < deadline) {
        const auto readback = ReadValue(node, "modes_of_operation_display", deadline);
        if (!readback) return Status(readback.code());
        if (readback.value() == static_cast<std::uint8_t>(mode)) return Status::Ok();
        PollPause(deadline);
    }
    return Status(ErrorCode::ECommandTimeout);
}
Status Pmp4010Driver::SetTargetPosition(std::uint8_t node, std::int32_t value) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto deadline = Deadline();
    const auto enabled = RequireEnabled(node, deadline);
    return enabled ? WriteValue(node, "target_position", static_cast<std::uint32_t>(value), deadline) : enabled;
}
Status Pmp4010Driver::SetTargetVelocity(std::uint8_t node, std::int32_t) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    // E06 §5.2 requires a live 608Fh/6091h conversion before comparing to
    // max_motor_speed (6080h, rpm). counts_per_unit is not that conversion.
    return Status(ErrorCode::EInvalidConfig);
}
Result<std::int32_t> Pmp4010Driver::GetActualPosition(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return Result<std::int32_t>::Err(guard.code());
    const auto result = ReadValue(node, "actual_position", Deadline());
    return result ? Result<std::int32_t>::Ok(Signed32(result.value())) : Result<std::int32_t>::Err(result.code());
}
Result<std::int32_t> Pmp4010Driver::GetActualVelocity(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return Result<std::int32_t>::Err(guard.code());
    const auto result = ReadValue(node, "actual_velocity", Deadline());
    return result ? Result<std::int32_t>::Ok(Signed32(result.value())) : Result<std::int32_t>::Err(result.code());
}
Result<NodeState> Pmp4010Driver::ProbeNode(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return Result<NodeState>::Err(guard.code());
    const auto deadline = Deadline();
    const auto sw = ReadValue(node, "statusword", deadline);
    if (!sw) return Result<NodeState>::Err(sw.code());
    const auto position = ReadValue(node, "actual_position", deadline);
    if (!position) return Result<NodeState>::Err(position.code());
    const auto velocity = ReadValue(node, "actual_velocity", deadline);
    if (!velocity) return Result<NodeState>::Err(velocity.code());
    NodeState state;
    state.node_id = node; state.online = true; state.fault = (sw.value() & wire::CIA402_FAULT) != 0;
    state.enabled = canopen::StateIs(static_cast<std::uint16_t>(sw.value()), wire::CIA402_ENABLED);
    state.actual_position_counts = Signed32(position.value());
    state.actual_velocity_counts = Signed32(velocity.value());
    return Result<NodeState>::Ok(state);
}
Status Pmp4010Driver::SetVelocity(std::uint8_t node, std::int32_t value, Direction direction) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    if (value < 0 || (direction != Direction::Raise && direction != Direction::Lower)) return Status(ErrorCode::EInvalidRequest);
    // No configured/verified user units/s -> rpm conversion; deny before I/O.
    return Status(ErrorCode::EInvalidConfig);
}
Status Pmp4010Driver::MoveTo(std::uint8_t node, std::int32_t value) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto deadline = Deadline();
    // Read both live software limits before clearing a set-point/control edge.
    const auto limits = RequirePositionInRange(node, value, deadline);
    if (!limits) return limits;
    const auto enabled = RequireEnabled(node, deadline);
    if (!enabled) return enabled;
    const auto mode = ReadValue(node, "modes_of_operation_display", deadline);
    if (!mode) return Status(mode.code());
    if (mode.value() != 1) return Status(ErrorCode::ECommandRejected);
    auto st = Control(node, wire::CIA402_ENABLE, deadline); // clear set-point edge
    if (!st) return st;
    const auto wait_ack = [&](bool expected) {
        while (MonotonicUs() < deadline) {
            const auto sw = ReadValue(node, "statusword", deadline);
            if (!sw) return Status(sw.code());
            if (!canopen::StateIs(static_cast<std::uint16_t>(sw.value()), wire::CIA402_ENABLED) ||
                (sw.value() & wire::CIA402_PP_FOLLOWING_ERROR) != 0) return Status(ErrorCode::EDeviceError);
            if (((sw.value() & wire::CIA402_PP_SETPOINT_ACK) != 0) == expected) return Status::Ok();
            PollPause(deadline);
        }
        return Status(ErrorCode::ECommandTimeout);
    };
    st = wait_ack(false);
    if (!st) return st;
    st = WriteValue(node, "target_position", static_cast<std::uint32_t>(value), deadline);
    if (!st) return st;
    st = Control(node, static_cast<std::uint16_t>(wire::CIA402_ENABLE | wire::CIA402_NEW_SETPOINT), deadline);
    if (st) st = wait_ack(true);
    // Always lower the trigger, even if acknowledgement failed (fresh bounded cleanup).
    const auto original_abort = LastSdoAbortCode();
    const auto cleanup = Control(node, wire::CIA402_ENABLE, st ? deadline : Deadline());
    if (!st && original_abort != 0) preserved_abort_code_ = original_abort;
    if (!cleanup) ++stats_.cleanup_failures;
    if (!st) { CleanupStop(node); return st; }
    st = cleanup ? wait_ack(false) : cleanup;
    if (!st) CleanupStop(node);
    return st;
}
Status Pmp4010Driver::Home(std::uint8_t node) {
    const auto guard = GuardTransmit(node);
    if (!guard) return guard;
    const auto method = config_.homing_method;
    if (!((method >= 17 && method <= 30) || method == 35) || config_.homing_timeout_ms == 0)
        return Status(ErrorCode::EInvalidConfig);
    const auto selected = RequireCia402(node, Deadline());
    if (!selected) return selected;
    auto st = Disable(node);
    if (!st) return st;
    st = SetOperationMode(node, 6);
    if (!st) return st;
    st = WriteValue(node, "homing_method", static_cast<std::uint8_t>(method), Deadline());
    if (!st) return st;
    const auto method_readback = ReadValue(node, "homing_method", Deadline());
    if (!method_readback) return Status(method_readback.code());
    if (method_readback.value() != static_cast<std::uint8_t>(method)) return Status(ErrorCode::EDeviceError);
    st = Enable(node);
    if (!st) { CleanupStop(node); return st; }
    const auto deadline = MonotonicUs() + std::uint64_t{config_.homing_timeout_ms} * 1000u;
    st = Control(node, wire::CIA402_ENABLE, std::min(deadline, Deadline()));
    const auto baseline = st ? ReadValue(node, "statusword", std::min(deadline, Deadline())) : Result<std::uint32_t>::Err(st.code());
    if (!baseline) { CleanupStop(node); return Status(baseline.code()); }
    if (!canopen::StateIs(static_cast<std::uint16_t>(baseline.value()), wire::CIA402_ENABLED) || (baseline.value() & wire::CIA402_HOMING_ERROR) != 0) {
        CleanupStop(node); return Status(ErrorCode::EDeviceError);
    }
    // E06 table 6-19 defines target reached (bit10) and homing attained
    // (bit12) separately. Host policy requires both, so successful homing also
    // confirms the profile reached its target; this is stricter than either bit.
    constexpr auto complete = wire::CIA402_HOMING_TARGET_REACHED | wire::CIA402_HOMING_ATTAINED;
    bool seen_incomplete = (baseline.value() & complete) != complete;
    if (st) st = Control(node, static_cast<std::uint16_t>(wire::CIA402_START_HOMING), std::min(deadline, Deadline()));
    if (!st) { CleanupStop(node); return st; }
    while (MonotonicUs() < deadline) {
        const auto sw = ReadValue(node, "statusword", std::min(deadline, Deadline()));
        if (!sw) { CleanupStop(node); return Status(sw.code()); }
        if (!canopen::StateIs(static_cast<std::uint16_t>(sw.value()), wire::CIA402_ENABLED) || (sw.value() & wire::CIA402_HOMING_ERROR) != 0) {
            CleanupStop(node); return Status(ErrorCode::EDeviceError);
        }
        if ((sw.value() & complete) != complete) seen_incomplete = true;
        if (seen_incomplete && (sw.value() & complete) == complete) {
            const auto finish = Control(node, wire::CIA402_ENABLE, std::min(deadline, Deadline()));
            if (!finish) CleanupStop(node);
            return finish;
        }
        PollPause(deadline);
    }
    CleanupStop(node);
    return Status(ErrorCode::ECommandTimeout);
}
}  // namespace km3::pmp4010
