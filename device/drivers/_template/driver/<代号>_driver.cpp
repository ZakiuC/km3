// _model_ 驱动骨架实现。
//
// 本文件的实现**刻意不猜测任何报文布局**：PDO 映射、控制字位定义、标度因子
// 都还压在 CONTRACT.md「阻塞项」里。宁可留下明确的拒绝路径，
// 也不要写一个"看起来能跑"的猜测实现——后者在现场的表现是电机按错误的方向动。

#include "_model__driver.hpp"

#include <cstring>
#include <utility>

namespace km3::_model_ {

namespace {

// 单一拒绝文案来源。工具与测试都读这里，不各写一份。
constexpr const char* kReasonUnverified =
    "对象字典未核对（见 driver/od.json 与 CONTRACT.md）";
constexpr const char* kReasonScale =
    "标度因子未配置（meters_per_count 为 0）";
constexpr const char* kReasonClosed = "驱动未打开";

}  // namespace

std::string_view ToString(OperationMode mode) {
    switch (mode) {
        case OperationMode::NoMode:   return "NoMode";
        case OperationMode::Position: return "Position";
        case OperationMode::Velocity: return "Velocity";
        case OperationMode::Homing:   return "Homing";
    }
    return "Unknown";
}

// ============================ _Model_Driver ============================

_Model_Driver::_Model_Driver(ICanDevice& bus, DriverConfig config)
    : bus_(bus), config_(std::move(config)) {}

Status _Model_Driver::Open() noexcept {
    // 打开只做本地准备：不上电、不发 NMT、不使能。
    // 上电默认状态是手册问题（CONTRACT.md 阻塞项 3），驱动不得自行决定。
    open_ = true;
    return Status::Ok();
}

Status _Model_Driver::Close() noexcept {
    open_ = false;
    state_ = NodeState{};
    return Status::Ok();
}

bool _Model_Driver::ConvertToCounts(const MotionRequest& request,
                                    std::int32_t* out_counts) const noexcept {
    if (out_counts == nullptr) {
        return false;
    }
    if (!ScaleConfigured()) {
        return false;
    }
    // 标度已配置时才做换算。未配置时**不得**用 1.0 兜底。
    if (request.mode == OperationMode::Position) {
        *out_counts = static_cast<std::int32_t>(request.position_m / config_.meters_per_count);
        return true;
    }
    if (request.mode == OperationMode::Velocity ||
        request.mode == OperationMode::Homing) {
        *out_counts = static_cast<std::int32_t>(request.velocity_mps / config_.meters_per_count);
        return true;
    }
    return false;
}

CanFrame _Model_Driver::BuildWriteFrame(std::uint16_t index, std::uint8_t subindex,
                                        std::int32_t value) const noexcept {
    // 骨架：**不猜测 SDO/PDO 布局**。此处只填出与手册无关的部分（节点号承载在
    // CAN ID 上这一点同样待确认，见 CONTRACT.md 阻塞项 1），载荷留给手册到位后实现。
    CanFrame frame;
    frame.id = config_.node_id;   // 占位：真实 CAN ID 分配方式未确认
    frame.extended = false;
    frame.dlc = CanFrame::kClassicMaxBytes;
    // 未核对的字段不清零当成"合理默认"：整帧保持 0，由调用方在 OdVerified() 为假时
    // 提前拒绝，根本走不到这里。
    std::memset(frame.data.data(), 0, frame.data.size());
    static_cast<void>(index);
    static_cast<void>(subindex);
    static_cast<void>(value);
    return frame;
}

void _Model_Driver::SetReject(const char* reason) {
    // 注意：std::string 赋值可能分配内存，**这会破坏 RT 可达性**。
    // 因此本函数只在非实时路径调用，且 CONTRACT.md「RT 可达性」当前把所有
    // 函数都标为"不可进 RT"。将来要把驱动放进 RT 线程，必须先解决这一处。
    last_reject_reason_ = reason;
}

Status _Model_Driver::SendMotion(const MotionRequest& request) noexcept {
    // 检查顺序是刻意的：从"最不该发生的错误"到"最具体的错误"，
    // 任何一条不通过都**在发帧之前**返回，绝不出现"报错但帧已出去"。
    if (!open_) {
        SetReject(kReasonClosed);
        return Status(ErrorCode::EDeviceOffline);
    }
    if (!od::kOdVerified) {
        // 库里全部未经手册核对时，连一帧都不发。
        // 只返回错误码而仍发帧，是本仓库已经踩过的坑（测试要断言总线上零帧，
        // 不能只断言返回码）。
        SetReject(kReasonUnverified);
        return Status(ErrorCode::ECommandRejected);
    }
    if (!ScaleConfigured()) {
        SetReject(kReasonScale);
        return Status(ErrorCode::EInvalidConfig);
    }

    std::int32_t counts = 0;
    if (!ConvertToCounts(request, &counts)) {
        SetReject("运动请求无法换算（模式或参数不合法）");
        return Status(ErrorCode::EInvalidRequest);
    }

    // 到这一步才真的发包。索引取自生成的头文件，因此必然带 cite 可追溯。
    const CanFrame frame = BuildWriteFrame(od::kEntries[0].index, 0u, counts);
    const Status sent = bus_.Send(frame);
    if (!sent.ok()) {
        return sent;
    }
    state_.last_ok_us = 0;   // 时间源接入点：使用 MonotonicUs()，不用 WallClockUs()
    return Status::Ok();
}

bool _Model_Driver::OnFrame(const CanFrame& frame) noexcept {
    // 骨架：节点过滤按 CAN ID 判定，而 ID 分配方式**未确认**（阻塞项 1），
    // 因此这里只做最保守的处理——不消费任何帧。
    // 保守的错法是"收不到反馈"，激进的错法是"把别人的帧当成自己的"。
    static_cast<void>(frame);
    return false;
}

// =========================== _Model_Manager ===========================

_Model_Manager::_Model_Manager(ICanDevice& bus) : bus_(bus) {
    bus_.Stats();   // 触碰一次，确认后端已就绪（骨架占位，无副作用）
}

bool _Model_Manager::AddNode(const DriverConfig& config) {
    if (Find(config.node_id) != nullptr) {
        return false;   // 节点号重复是配置错误，不静默覆盖
    }
    // 禁止裸 new 在实时路径上——这里是初始化路径（进程启动时调用一次），
    // 不在 RT 线程内，因此 make_unique 是允许的。见 CONTRACT.md「RT 可达性」。
    nodes_.push_back(std::make_unique<_Model_Driver>(bus_, config));
    return true;
}

Status _Model_Manager::OpenAll() noexcept {
    for (auto& node : nodes_) {
        const Status s = node->Open();
        if (!s.ok()) {
            return s;
        }
    }
    return Status::Ok();
}

Status _Model_Manager::CloseAll() noexcept {
    for (auto& node : nodes_) {
        const Status s = node->Close();
        if (!s.ok()) {
            return s;
        }
    }
    return Status::Ok();
}

_Model_Driver* _Model_Manager::Find(std::uint8_t node_id) noexcept {
    for (auto& node : nodes_) {
        if (node->NodeId() == node_id) {
            return node.get();
        }
    }
    return nullptr;
}

std::size_t _Model_Manager::Dispatch(const CanFrame& frame) noexcept {
    std::size_t consumed = 0;
    for (auto& node : nodes_) {
        if (node->OnFrame(frame)) {
            ++consumed;
        }
    }
    return consumed;
}

std::size_t _Model_Manager::PollOnce(int timeout_ms) {
    std::size_t received = 0;
    for (;;) {
        const Result<CanFrame> r = bus_.Receive(timeout_ms);
        if (!r.ok()) {
            break;   // ECommandTimeout 是"总线空闲"，不是错误
        }
        Dispatch(r.value());
        ++received;
        if (timeout_ms == 0) {
            continue;   // 非阻塞模式：把队列读空
        }
        break;
    }
    return received;
}

std::vector<std::uint8_t> _Model_Manager::TimedOutNodes(std::uint64_t now_us,
                                                        std::uint64_t timeout_us) const {
    std::vector<std::uint8_t> out;
    for (const auto& node : nodes_) {
        const NodeState& st = node->State();
        if (!st.valid) {
            continue;
        }
        if (now_us > st.last_ok_us && (now_us - st.last_ok_us) > timeout_us) {
            out.push_back(node->NodeId());
        }
    }
    return out;
}

}  // namespace km3::_model_
