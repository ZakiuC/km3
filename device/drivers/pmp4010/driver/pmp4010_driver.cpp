// PMP4010 非实时驱动边界。OD 尚未全部核对，且 km3_canopen 尚未实现。
// 所有协议入口都先经过同一个 OD / 生命周期 / 节点守卫；本文件不组装 CANopen 帧。

#include "pmp4010_driver.hpp"

#include <algorithm>
#include <utility>

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
    : device_(device), config_(std::move(config)) {}

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

// ---- 诊断 ----

Result<NodeState> Pmp4010Driver::ProbeNode(std::uint8_t node_id) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return Result<NodeState>::Err(guard.code());
    }
    // 未接入标准层就没有探测结果。不能把“未发送请求”报告为探测成功。
    return Result<NodeState>::Err(ErrorCode::EInternal);
}

Status Pmp4010Driver::SendNmt(NmtCommand command, std::uint8_t node_id) {
    const Status guard = GuardTransmit(node_id, true);
    if (!guard.ok()) {
        return guard;
    }
    // NMT 帧组装归 km3_canopen 所有（硬件驱动开发路径第 7 步）。
    // 标准层缺失时显式失败，不能在本型号驱动中另写一套实现。
    (void)command;
    return Status(ErrorCode::EInternal);
}

// ---- 运动 ----

Status Pmp4010Driver::Enable(std::uint8_t node_id) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    // 标准层接入后才可执行使能事务，并通过状态字确认结果。
    return Status(ErrorCode::EInternal);
}

Status Pmp4010Driver::Disable(std::uint8_t node_id) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    return Status(ErrorCode::EInternal);
}

Status Pmp4010Driver::MoveTo(std::uint8_t node_id, std::int32_t target_counts) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    // target_counts 是原始计数；不在此处猜测物理量换算。
    (void)target_counts;
    return Status(ErrorCode::EInternal);
}

Status Pmp4010Driver::SetVelocity(std::uint8_t node_id, std::int32_t velocity_counts,
                                  Direction direction) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    (void)velocity_counts;
    (void)direction;
    return Status(ErrorCode::EInternal);
}

Status Pmp4010Driver::Stop(std::uint8_t node_id) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    return Status(ErrorCode::EInternal);
}

Status Pmp4010Driver::Home(std::uint8_t node_id) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    // 回零依赖标准层事务与明确的调用方运行参数，当前不执行。
    return Status(ErrorCode::EInternal);
}

// ---- SDO ----

Status Pmp4010Driver::SdoRead(std::uint8_t node_id, std::uint16_t index, std::uint8_t sub,
                              std::vector<std::uint8_t>& out) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    // 所有失败路径保持 out 原样。
    (void)index;
    (void)sub;
    (void)out;
    return Status(ErrorCode::EInternal);
}

Status Pmp4010Driver::SdoWrite(std::uint8_t node_id, std::uint16_t index, std::uint8_t sub,
                               const std::vector<std::uint8_t>& data) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    (void)index;
    (void)sub;
    (void)data;
    return Status(ErrorCode::EInternal);
}

}  // namespace km3::pmp4010
