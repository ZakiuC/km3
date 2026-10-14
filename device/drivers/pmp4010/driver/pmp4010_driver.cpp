// PMP4010 驱动实现。骨架阶段。
//
// 本文件当前**不发任何帧**，且这是设计而非欠缺：对象字典全部条目
// status=unverified（driver/od.json），索引未知。见 CONTRACT.md 第一、三节。
//
// 组织方式：所有对外方法都先过 GuardTransmit()，一处集中判断
// 「OD 是否已验证 / 设备是否已打开 / 节点号是否合法」。
// 早期同类驱动的写法是在每个方法里各写一遍前置检查，结果新增一个
// 发帧方法时就漏掉一次——这里把入口收成一个函数，漏掉的机会降到 0。

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
    : device_(device), config_(std::move(config)) {
}

Pmp4010Driver::~Pmp4010Driver() = default;

bool Pmp4010Driver::NodeIdValid(std::uint8_t node_id) const noexcept {
    if (node_id == 0) {
        return false;  // 0 是 NMT 广播地址，不是节点地址
    }
    const auto& nodes = config_.nodes;
    return std::any_of(nodes.begin(), nodes.end(),
                       [node_id](const NodeConfig& n) { return n.node_id == node_id; });
}

Status Pmp4010Driver::Open() noexcept {
    if (open_) {
        return Status::Ok();
    }
    if (!device_.IsOpen()) {
        // HAL 不做内部加锁，也**不替调用方打开设备**：由持有者决定何时打开。
        return Status(ErrorCode::EDeviceOffline);
    }

    const Status backend = CheckBackend(device_);
    if (!backend.ok()) {
        return backend;
    }

    if (config_.logical_bus.empty()) {
        return Status(ErrorCode::EInvalidConfig);
    }

    // 4 台节点号必须非零且互不相同。
    // 分配方式未确认（手册条目 D9），所以这里只能校验"调用方给的值自洽"，
    // 无法校验"与硬件拨码一致"——后者要等手册。
    for (std::size_t i = 0; i < config_.nodes.size(); ++i) {
        const std::uint8_t id = config_.nodes[i].node_id;
        if (id == 0) {
            return Status(ErrorCode::EInvalidConfig);
        }
        for (std::size_t j = i + 1; j < config_.nodes.size(); ++j) {
            if (config_.nodes[j].node_id == id) {
                return Status(ErrorCode::EInvalidConfig);
            }
        }
    }

    // 标度未确认（条目 A3）。允许其为零值 —— 上层可能只做扫描不运动；
    // 但真要用到标度时（物理量入口）必须已经填实，那一步在手册之后。
    open_ = true;
    return Status::Ok();
}

Status Pmp4010Driver::Close() noexcept {
    // 不在这里发 NMT Stop / 关使能：停机报文本身需要已确认的控制字索引，
    // 而这一层还不可用（CONTRACT.md 第三节第 3 条）。
    // 上电/断电时的安全由硬件与部署负责。
    open_ = false;
    return Status::Ok();
}

Status Pmp4010Driver::GuardTransmit(std::uint8_t node_id) noexcept {
    if (!kOdVerified) {
        // 唯一正确的行为：拒发，并计数。零字节出总线。
        ++stats_.rejected_by_od_guard;
        return Status(kOdGuardError);
    }
    if (!open_) {
        ++stats_.rejected_by_validation;
        return Status(ErrorCode::EDeviceOffline);
    }
    if (!NodeIdValid(node_id)) {
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
    // 手册到达后：SDO 读设备类型 / 固件版本 / 状态字。
    // 注意这是**边界线程**上的三次请求-应答事务，不得放进任何周期路径。
    NodeState st;
    st.node_id = node_id;
    st.online = false;
    return Result<NodeState>::Ok(st);
}

Status Pmp4010Driver::SendNmt(NmtCommand command, std::uint8_t node_id) {
    // node_id == 0 表示广播，是合法入参，因此不能直接过 GuardTransmit。
    if (!kOdVerified) {
        ++stats_.rejected_by_od_guard;
        return Status(kOdGuardError);
    }
    if (!open_) {
        ++stats_.rejected_by_validation;
        return Status(ErrorCode::EDeviceOffline);
    }
    if (node_id != 0 && !NodeIdValid(node_id)) {
        ++stats_.rejected_by_validation;
        return Status(ErrorCode::EInvalidRequest);
    }

    CanFrame frame;
    // NMT 的 COB-ID 固定为 0x000 —— 这是 CANopen 的预定义连接集，
    // 不属于待确认的厂商条目，因此这里可以写死；载荷第二字节是节点号。
    frame.id = 0x000;
    frame.extended = false;
    frame.dlc = 2;
    frame.data[0] = static_cast<std::uint8_t>(command);
    frame.data[1] = node_id;

    const Status sent = device_.Send(frame);
    if (sent.ok()) {
        ++stats_.tx_frames;
    }
    return sent;
}

// ---- 运动 ----

Status Pmp4010Driver::Enable(std::uint8_t node_id) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    // 手册到达后：置控制字的使能位序列，并**轮询状态字确认**。
    // 只发不确认 = "以为使能了"，是抬降机构最危险的一类静默失败。
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
    // target_counts 是**原始计数**，不是物理量：标度未确认（条目 A3）之前，
    // 任何物理量入口都是假精确。换算责任明确留给上层，驱动不猜。
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
    // 回零方法与速度未确认（od.json 的 homing_method）。没有它，零位无意义。
    return Status(ErrorCode::EInternal);
}

// ---- SDO ----

Status Pmp4010Driver::SdoRead(std::uint8_t node_id, std::uint16_t index, std::uint8_t sub,
                              std::vector<std::uint8_t>& out) {
    const Status guard = GuardTransmit(node_id);
    if (!guard.ok()) {
        return guard;
    }
    // out 在守卫失败时**原样保留**，不改动。真实实现落地时要明确这一语义
    // （清空 vs 不改动）并在单测里钉死；现在不改动，因为改了也是猜。
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
