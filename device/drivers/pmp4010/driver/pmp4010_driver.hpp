#pragma once
// PMP4010 ×4 非实时驱动边界，使用配置中的逻辑总线名 can-2。
// 手册已覆盖部分 OD，整体置信度仍为 false；生产构建由生成物的 #error 拦截。
// 隔离 mock 测试可显式允许编译未完整核对的 OD，但不会打开运行时发帧守卫。
// 协议实现须复用与 Python 同源的 km3_canopen；该 C++ 标准层目前尚未落地。
// 本驱动由边界线程独占，不得在实时线程内调用任何方法。见 CONTRACT.md §2。

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "km3/hal/can_device.hpp"
#include "km3/result.hpp"
// 生成物是 OD 常量与置信度的唯一来源，不手写厂家索引或标度。
#include "od_generated.hpp"

namespace km3::pmp4010 {

constexpr bool kOdVerified = od::kOdVerified;

// 节点号必须来自调用方加载的配置，0 表示尚未配置，不能作为节点地址。
// 标度由调用方显式提供；当前没有物理量换算入口。
struct NodeConfig {
    std::uint8_t node_id = 0;  // 必须由调用方显式给出；0 不是合法 CANopen 节点号
    // 位置与速度标度（计数单位 → 物理量）与零位偏置，不猜测运行参数。
    double counts_per_unit = 0.0;
    double zero_offset_counts = 0.0;
};

struct DriverConfig {
    // CANopen 会话所在总线的**逻辑名**（"can-2"）。物理接口由 device.json 决定。
    std::string logical_bus;
    // 4 台：机身抬降电机 A。数量与节点号都必须来自配置，不写死。
    std::array<NodeConfig, 4> nodes{};
    // 保留调用方配置的心跳与 SDO 超时，标准层接入前不使用。
    std::uint32_t sdo_timeout_ms = 0;
    std::uint32_t heartbeat_period_ms = 0;
};

// 一台从站的状态摘要。字段刻意留少：多一个字段就多一个需要依据才能填的值。
struct NodeState {
    std::uint8_t node_id = 0;
    bool online = false;
    bool fault = false;
    bool enabled = false;
    std::int32_t actual_position_counts = 0;  // 原始计数，未按标度换算
    std::int32_t actual_velocity_counts = 0;
};

struct DriverStats {
    std::uint64_t tx_frames = 0;
    std::uint64_t rx_frames = 0;
    std::uint64_t rejected_by_od_guard = 0;  // 被 kOdVerified 挡下的调用次数
    std::uint64_t rejected_by_validation = 0;
    std::uint64_t sdo_timeouts = 0;
};

enum class NmtCommand : std::uint8_t {
    Start = 0x01,
    Stop = 0x02,
    PreOperational = 0x80,
    ResetNode = 0x81,
    ResetCommunication = 0x82,
};

// 抬降方向由调用方显式给出，当前接口不执行运动或推断方向换算。
enum class Direction : std::uint8_t {
    Raise = 0,
    Lower = 1,
};

class Pmp4010Driver {
public:
    // device 由调用方持有并保证生命周期长于本对象（HAL 的既有约定）。
    Pmp4010Driver(ICanDevice& device, DriverConfig config);
    ~Pmp4010Driver();

    Pmp4010Driver(const Pmp4010Driver&) = delete;
    Pmp4010Driver& operator=(const Pmp4010Driver&) = delete;
    Pmp4010Driver(Pmp4010Driver&&) = delete;
    Pmp4010Driver& operator=(Pmp4010Driver&&) = delete;

    // ---- 生命周期（不发帧）----
    // 校验：后端必须是 SocketCan（主板 CAN 不支持 CAN FD，CANopen 设备不走 USB-CAN），
    // 逻辑名非空且与设备一致，4 个节点号在 1..127 内且互不相同。
    Status Open() noexcept;
    Status Close() noexcept;
    bool IsOpen() const noexcept { return open_ && device_.IsOpen(); }

    const std::string& LogicalBus() const noexcept { return config_.logical_bus; }
    const DriverConfig& Config() const noexcept { return config_; }

    // ---- 诊断 ----
    // 只读探测。kOdVerified 为 false 时不发任何帧，返回 EDeviceError。
    // 标准层尚未接通，守卫放行后仍返回 EInternal，不伪造成功的探测结果。
    Result<NodeState> ProbeNode(std::uint8_t node_id);

    // NMT 允许节点号 0 表示广播，同样经过 OD 守卫。
    // 帧组装归 km3_canopen；标准层缺失时显式返回 EInternal。
    Status SendNmt(NmtCommand command, std::uint8_t node_id /* 0 = 全部 */);

    // ---- 运动（当前全部被守卫挡下）----
    // 一次性指令语义，**不是周期指令语义**。
    // 周期路径须经过标准层和 PDO 配置验证，本接口不承担周期调度。
    Status Enable(std::uint8_t node_id);
    Status Disable(std::uint8_t node_id);
    Status MoveTo(std::uint8_t node_id, std::int32_t target_counts);
    Status SetVelocity(std::uint8_t node_id, std::int32_t velocity_counts, Direction direction);
    Status Stop(std::uint8_t node_id);
    Status Home(std::uint8_t node_id);

    // ---- SDO（边界线程专用事务）----
    // 标准层尚未接通：先校验 OD 与生命周期，放行后返回 EInternal。
    // 失败时 SdoRead 保持 out 原样；不收发帧。
    Status SdoRead(std::uint8_t node_id, std::uint16_t index, std::uint8_t sub,
                   std::vector<std::uint8_t>& out);
    Status SdoWrite(std::uint8_t node_id, std::uint16_t index, std::uint8_t sub,
                    const std::vector<std::uint8_t>& data);

    // 未核对的对象字典条目数。工具与日志打印的「N 项未核对」用这个 N，
    // 与 od.json 同一个来源（生成物的 od::kUnverifiedCount），不各自再算一遍。
    static constexpr std::size_t UnverifiedOdCount() noexcept { return od::kUnverifiedCount; }
    // 条目总数。与 od.json 的 entries 长度同源，用于对账生成物是否脱节。
    static constexpr std::size_t OdEntryCount() noexcept { return od::kEntryCount; }

    DriverStats Stats() const noexcept { return stats_; }
    const ICanDevice& Device() const noexcept { return device_; }

    // 被守卫挡下的调用一律用这个码，便于测试与现场日志用同一判据。
    static constexpr ErrorCode kOdGuardError = ErrorCode::EDeviceError;

private:
    // 所有会发帧的方法的唯一入口。守卫、打开状态、节点号校验集中在这里，
    // 免得每个方法各写一遍（漏一个就是一条能发帧的旁路）。
    Status GuardTransmit(std::uint8_t node_id, bool allow_broadcast = false) noexcept;

    bool NodeIdValid(std::uint8_t node_id) const noexcept;

    ICanDevice& device_;
    DriverConfig config_;
    bool open_ = false;
    DriverStats stats_{};
};

}  // namespace km3::pmp4010
