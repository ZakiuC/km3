#pragma once
// PMP4010（机身抬降电机 A）驱动 —— 接口骨架。
//
// 硬件：4 台 PMP4010，CANopen，挂主板第二路 CAN（逻辑名 `can-2`，CAN 2.0B @500000bps）。
// 逻辑名到物理接口的映射只在 device/config/device.json 里，本文件不出现物理标识。
//
// 本驱动当前**被对象字典挡住**：厂家手册未到，driver/od.json 每条 index 为 null。
// 于是 kOdVerified 恒为 false，所有会发帧的方法直接返回错误、一个字节都不发。
// 这不是 TODO 占位，而是当下唯一正确的行为（见 CONTRACT.md §3.1）：
// 索引未知就发帧 = 拿真实电机盲试。
//
// 线程：**本驱动必须由边界线程独占**（ICanDevice 不做内部加锁）。
// 不得在实时线程内调用任何方法。理由见 CONTRACT.md §2。

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "km3/hal/can_device.hpp"
#include "km3/result.hpp"
// 对象字典常量的**唯一来源**：本文件由 tools/hwdriver/gen-od.py 从 driver/od.json 生成。
//
// 它默认是**有毒的**：od.json 里仍有 status != verified 的条目（当前 25 条全部如此），
// 于是生成物里是一个 #error，本驱动编不过。编不过是设计，不是欠缺 ——
// 索引未知就发帧等于拿真实电机盲试，把这件事挪到编译期是本仓库的硬停规则第 2 条。
//
// 旁路开关 KM3_ALLOW_UNVERIFIED_OD=1（或直接定义 PMP4010_OD_VERIFIED=1）**只**允许
// 用于 CI 与未接电机的回环验证。未定义时，本文件所在的目标根本编译不出来，
// 因此不存在"不小心带着它上电"的路径。
#include "od_generated.hpp"

namespace km3::pmp4010 {

// 对象字典的置信度，转发生成物的取值 —— **事实只有一处**，不在这里再算一遍。
//
// 为什么不写成两个文件各定义一次的常量：那样单测同时 include 两者时会
// 直接 "redefinition" 编不过，而更麻烦的是手工维护的那份可以在 od.json
// 填实后被漏改，让驱动继续以为自己未核对（或者相反）。
// 取值链条：od.json 的条目状态 -> 生成器 -> PMP4010_OD_VERIFIED -> 生成物的
// od::kOdVerified -> 本名（生成物的 od 子命名空间）。
//
// 保留 kOdVerified 这个短名字是因为下面十几处调用点都在用它；它只是一个转发，
// 不是第二个事实。手册到达、od.json 全部 verified 后，本行随之变 true，
// 驱动里所有 GuardTransmit 的门就开了 —— 不需要任何人去改这里的字面量。
//
// 转发用 constexpr 而不是宏：宏会在调用点静默污染预处理空间，
// 而 tests 里的 `!kOdVerified` 一旦变成宏，写错也无人报错。
constexpr bool kOdVerified = od::kOdVerified;

// 单台从站的运行参数。**没有任何默认值** —— 节点号分配方式未确认
// （手册条目 D9：拨码 / SDO 写入 / 出厂固定）。给一个"看起来合理"的默认值，
// 只会让 4 台电机同时在总线上应答同一帧。
struct NodeConfig {
    std::uint8_t node_id = 0;          // 必须由调用方显式给出；0 不是合法 CANopen 节点号
    // 位置与速度标度（计数单位 → 物理量）与零位偏置。
    // 手册条目 A3 未确认，因此这里没有可用的数值，驱动也不会替上层换算。
    double counts_per_unit = 0.0;
    double zero_offset_counts = 0.0;
};

struct DriverConfig {
    // CANopen 会话所在总线的**逻辑名**（"can-2"）。物理接口由 device.json 决定。
    std::string logical_bus;
    // 4 台：机身抬降电机 A。数量与节点号都必须来自配置，不写死。
    std::array<NodeConfig, 4> nodes{};
    // 心跳（条目 A4）与 SDO 超时均未确认。保留字段是为了让
    // "这两个值必须来自配置"这件事有落点，当前不使用。
    std::uint32_t sdo_timeout_ms = 0;
    std::uint32_t heartbeat_period_ms = 0;
};

// 一台从站的状态摘要。字段刻意留少：多一个字段就多一个需要依据才能填的值。
struct NodeState {
    std::uint8_t node_id = 0;
    bool online = false;
    bool fault = false;
    bool enabled = false;
    std::int32_t actual_position_counts = 0;   // 原始计数，未按标度换算
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

// 方向。抬降是双向运动，方向不能靠符号约定隐含在速度里 —— 那样"负速度"到底
// 是下降还是反转，取决于标度的正负，而标度未确认（条目 A3）。
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
    // 逻辑名非空，4 个节点号非零且互不相同。任一不满足即拒绝打开。
    Status Open() noexcept;
    Status Close() noexcept;
    bool IsOpen() const noexcept { return open_; }

    const std::string& LogicalBus() const noexcept { return config_.logical_bus; }
    const DriverConfig& Config() const noexcept { return config_; }

    // ---- 诊断 ----
    // 只读探测。kOdVerified 为 false 时不发任何帧，返回 EDeviceError。
    // 手册到达后：读设备类型 / 固件版本 / 状态字各一次，均为**边界线程**上的 SDO 事务。
    Result<NodeState> ProbeNode(std::uint8_t node_id);

    // NMT 是**广播语义**的单帧命令（COB-ID 0x000，载荷 = 命令 + 可选节点号），
    // 不含请求应答，因此它不受 SDO 阻塞约束；但仍受对象字典守卫约束 ——
    // 在索引与上电状态未知时让 4 台电机同时 "Start" 是危险的。
    Status SendNmt(NmtCommand command, std::uint8_t node_id /* 0 = 全部 */);

    // ---- 运动（当前全部被守卫挡下）----
    // 一次性指令语义，**不是周期指令语义**。
    // 周期路径必须走 PDO，而 PDO 映射未知（CONTRACT.md §1.1），故此处不实现周期接口。
    Status Enable(std::uint8_t node_id);
    Status Disable(std::uint8_t node_id);
    Status MoveTo(std::uint8_t node_id, std::int32_t target_counts);
    Status SetVelocity(std::uint8_t node_id, std::int32_t velocity_counts, Direction direction);
    Status Stop(std::uint8_t node_id);
    Status Home(std::uint8_t node_id);

    // ---- SDO（边界线程专用事务）----
    // 骨架：返回 EDeviceError。真实实现要调用 km3_canopen 的 SDO 客户端，
    // 且在 kOdVerified 为 false 时同样不得发帧 —— 连 index 都不确定，
    // 一个"读"也可能被从站当成别的服务。
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
    Status GuardTransmit(std::uint8_t node_id) noexcept;

    bool NodeIdValid(std::uint8_t node_id) const noexcept;

    ICanDevice& device_;
    DriverConfig config_;
    bool open_ = false;
    DriverStats stats_{};
};

}  // namespace km3::pmp4010
