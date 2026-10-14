#pragma once
// _model_ 驱动骨架。类与函数 PascalCase，变量 snake_case，成员变量后缀下划线。
//
// ---- 分层 ----
//
//   _Model_Driver   一个物理节点的语义封装：使能 / 发送 / 回读。
//                   它**不**自己接收，帧从外部喂进来（见 OnFrame 的注释）。
//   _Model_Manager  一条总线上的同型号节点集合：节点号 → 驱动，帧分发，心跳超时。
//
// 为什么接收不放在 Driver 里：接收路径在多节点场景下必须由**一处分发**，
// 否则每个节点各自 Receive() 会互相偷帧——CAN 没有"谁该收"的概念，
// 每个打开的套接字都拿到同一份帧。这是个具体的、必然发生的错法，不是风格偏好。

#include <array>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "km3/hal/can_device.hpp"
#include "km3/result.hpp"
#include "od_generated.hpp"

namespace km3::_model_ {

// 型号常量。占位值必须在手册到位后替换；见 CONTRACT.md「阻塞项」。
inline constexpr std::uint8_t kDefaultNodeId = 1;   // 出厂值未确认，纯占位
inline constexpr std::uint16_t kDefaultHeartbeatMs = 1000;

// 运行模式。取值未与手册核对，此处按 CiA 402 惯例命名。
enum class OperationMode : std::int8_t {
    NoMode = 0,
    Position = 1,    // 取值待手册确认
    Velocity = 3,    // 取值待手册确认
    Homing = 6,      // 取值待手册确认
};

std::string_view ToString(OperationMode mode);

// 一次运动请求的物理量。**这里只有物理量，没有计数单位**——
// 标度换算在驱动内部，上层不该知道计数器是多少。
struct MotionRequest {
    OperationMode mode = OperationMode::NoMode;
    double position_m = 0.0;     // mode == Position 时有意义
    double velocity_mps = 0.0;   // mode == Velocity 时有意义
};

// 节点状态快照。
//
// **本结构体的存在不等于本硬件有回读能力。** 有没有回读只在
// docs/硬件手册/_model_/README.md 的「反馈能力」小节声明一次。
// 若那里声明的值是 `none`，则各字段的 Valid 恒为 false，
// 且界面必须显式标注"无反馈"，不得把默认值当成实测值显示。
struct NodeState {
    bool valid = false;              // 是否来自真实回读（无反馈时恒 false）
    bool enabled = false;            // 是否已使能
    std::int32_t position_counts = 0;
    std::int32_t velocity_counts = 0;
    std::uint16_t status_word = 0;
    std::uint8_t error_code = 0;
    std::uint64_t last_ok_us = 0;    // 单调时刻，单位微秒
};

// 驱动配置。全部来自配置快照，**不在 RT 线程里读 JSON**（开发规范 §1.4）。
struct DriverConfig {
    std::string logical_bus;         // 逻辑名，如 "can-2"；内核接口名只出现在 device.json
    std::uint8_t node_id = kDefaultNodeId;
    std::uint16_t heartbeat_ms = kDefaultHeartbeatMs;
    // 标度：每个计数单位对应多少米。**未核对**，因此此处为 0，
    // 0 表示"未配置"，任何用它做换算的路径都必须先拒绝（见 ScaleConfigured()）。
    double meters_per_count = 0.0;
};

// 单个节点的驱动。
//
// 线程约定：继承 HAL 的约定——**本层不做内部加锁**，
// 调用方保证同一实例不被并发访问。
class _Model_Driver {
public:
    _Model_Driver(ICanDevice& bus, DriverConfig config);

    _Model_Driver(const _Model_Driver&) = delete;
    _Model_Driver& operator=(const _Model_Driver&) = delete;

    const DriverConfig& Config() const noexcept { return config_; }
    std::uint8_t NodeId() const noexcept { return config_.node_id; }

    // 打开：只做本地准备，**不上电、不使能不动作**。
    // 上电默认状态（自动使能还是保持禁用）见 docs/硬件手册/_model_/操作手册.md；
    // 未确认前驱动不得自行决定使能时机。
    Status Open() noexcept;
    Status Close() noexcept;
    bool IsOpen() const noexcept { return open_; }

    // 标度是否已配置。未配置时任何位置/速度换算都必须拒绝，
    // 而不是用 1.0 之类的默认值凑合——那会让上层以为自己在发米，实际在发计数。
    bool ScaleConfigured() const noexcept { return config_.meters_per_count > 0.0; }

    // 对象字典是否全部核对。运行时的拒绝路径与构建期的 #error 用同一判据。
    static constexpr bool OdVerified() noexcept { return od::kOdVerified; }
    static constexpr std::uint32_t UnverifiedCount() noexcept { return od::kUnverifiedCount; }

    // 发送运动请求。
    //
    // **未核对时必须零字节发出**：连一帧都不能上总线。
    // 早期版本只返回错误码却仍然把帧发了出去，测试只断言返回码因此放过——
    // 结果是"上层看到失败、电机却动了"。tests/test__model__.cpp 里对这一点
    // 断言的是总线上的帧数，不是返回码。
    Status SendMotion(const MotionRequest& request) noexcept;

    // 喂帧。由 Manager 调用，**不自己 Receive**（理由见文件头）。
    // 返回 true 表示该帧属于本节点且已被消费。
    bool OnFrame(const CanFrame& frame) noexcept;

    const NodeState& State() const noexcept { return state_; }

    // 最近一次拒绝的原因，供工具与日志展示。非实时路径使用。
    const std::string& LastRejectReason() const noexcept { return last_reject_reason_; }

private:
    // 把物理量换算成计数。标度未配置时返回 false，调用方必须拒绝发送。
    bool ConvertToCounts(const MotionRequest& request, std::int32_t* out_counts) const noexcept;

    // 组装一帧 SDO/PDO 写请求。**当前只有骨架**：报文格式取决于手册的 PDO 映射，
    // 未确认前不猜测布局。
    CanFrame BuildWriteFrame(std::uint16_t index, std::uint8_t subindex,
                             std::int32_t value) const noexcept;

    void SetReject(const char* reason);

    ICanDevice& bus_;
    DriverConfig config_;
    bool open_ = false;
    NodeState state_{};
    std::string last_reject_reason_;   // 非实时路径使用；RT 可达性见 CONTRACT.md
};

// 一条总线上的同型号节点集合。
class _Model_Manager {
public:
    explicit _Model_Manager(ICanDevice& bus);

    _Model_Manager(const _Model_Manager&) = delete;
    _Model_Manager& operator=(const _Model_Manager&) = delete;

    // 注册一个节点。返回 false 表示节点号已存在（重复注册是配置错误，必须报出来）。
    bool AddNode(const DriverConfig& config);

    Status OpenAll() noexcept;
    Status CloseAll() noexcept;

    std::size_t NodeCount() const noexcept { return nodes_.size(); }
    _Model_Driver* Find(std::uint8_t node_id) noexcept;

    // 把一帧分发给所有节点，返回被消费的节点数。
    // **只有一处 Receive 调用点**，见文件头关于"互相偷帧"的说明。
    std::size_t Dispatch(const CanFrame& frame) noexcept;

    // 轮转收帧一轮，返回收到的帧数。timeout_ms: 0 = 非阻塞。
    std::size_t PollOnce(int timeout_ms);

    // 心跳超时的节点号。未核对前心跳周期本身未确认，因此调用方必须知道
    // 这个结果的可信度取决于 od.json 里 producer_heartbeat_time 的状态。
    std::vector<std::uint8_t> TimedOutNodes(std::uint64_t now_us,
                                            std::uint64_t timeout_us) const;

private:
    ICanDevice& bus_;
    // 必须用 unique_ptr：_Model_Driver 删除了拷贝与移动（HAL 的 IDevice 约定），
    // 而 std::vector<T> 的扩容要求 T 可移动——用值语义存放会在 emplace_back
    // 触发扩容时以一条极难读的 static_assert 失败。
    // 顺带的好处：Find() 返回的裸指针在新增节点后依然有效。
    std::vector<std::unique_ptr<_Model_Driver>> nodes_;
};

}  // namespace km3::_model_
