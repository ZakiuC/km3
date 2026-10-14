#pragma once
// CAN 设备抽象。见 docs/架构设计/设备端程序架构.md §3.2 与 通信协议规范相关约定。
//
// **为什么必须区分后端**（这是本板最容易被写错的硬件事实）：
//
//   主板两路 CAN 由 SPI0 外挂 SIT2515T + NSI1050 实现，**仅支持 CAN 2.0B，最高 1Mbps，
//   不支持 CAN FD**。标称 CANfd 的设备（P65MAX-S）必须走 USB 转 CAN 模块。
//   因此 CAN 抽象下有两类后端，能力不同：
//
//     SocketCanBackend —— 主板 can0/can1，经典帧，走 SocketCAN
//     UsbCanBackend    —— 六叶树 USB-CAN，支持 CAN FD 帧
//
//   把 CANopen 设备接到 USB-CAN、或把 CAN FD 帧发到主板 CAN，都是接线/选型错误，
//   不是软件可以"兼容"的。接口因此显式暴露 Backend() 与 SupportsFd()。

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "km3/hal/device.hpp"

namespace km3 {

// 帧后端类型。
enum class CanBackend : std::uint8_t {
    SocketCan = 0,   // 主板 can0/can1：CAN 2.0B
    UsbCan = 1,      // USB 转 CAN 模块：可支持 CAN FD
};

std::string_view ToString(CanBackend backend);

// 后端能力。用于启动时校验"配置声明的能力"与"实际后端"是否匹配。
struct CanCapabilities {
    bool supports_fd = false;          // 是否支持 CAN FD
    std::uint32_t max_bitrate = 1000000;
    bool supports_extended_id = true;
    bool supports_remote = true;
};

// 一帧 CAN/CAN FD 报文。
// 与 Linux struct can_frame / canfd_frame 保持语义一致，但不直接复用其布局，
// 避免用户态结构体对齐差异（资料明确提示不能对含锁/指针的跨平台结构体做 packed 假设）。
struct CanFrame {
    std::uint32_t id = 0;              // 标准 11 位或扩展 29 位
    bool extended = false;             // 是否使用 29 位扩展帧
    bool remote = false;               // 远程请求帧
    bool fd = false;                   // 是否 CAN FD 帧（仅 USB-CAN 后端可用）
    bool bitrate_switch = false;       // FD 的数据段变速
    // 载荷**字节数**（不是 CAN 的"数据长度码"编码值）。
    // 经典帧 0..8；FD 帧 0..64。越界值不被静默钳制 —— 见 IsDlcValid()。
    std::uint8_t dlc = 0;
    std::array<std::uint8_t, 64> data{};  // 按最大 FD 载荷分配，经典帧只用前 8 字节

    static constexpr std::uint8_t kClassicMaxBytes = 8;
    static constexpr std::uint8_t kFdMaxBytes = 64;

    // 本帧允许的最大载荷字节数，取决于是否为 FD 帧。
    std::uint8_t MaxPayloadBytes() const noexcept {
        return fd ? kFdMaxBytes : kClassicMaxBytes;
    }

    // DLC 是否合法。
    //
    // 早期实现让 PayloadLength() 直接返回钳制后的值，而发送校验又拿它去比较上限，
    // 结果**校验永远不会触发**：dlc=9 的经典帧被钳成 8 后一路通过，
    // 现场表现为"上层发了超长帧但没有任何报错"。
    // 因此这里把"合法判定"与"取值"分开，校验一律基于原始 dlc。
    bool IsDlcValid() const noexcept { return dlc <= MaxPayloadBytes(); }

    // 有效载荷长度。**仅在 IsDlcValid() 为真时有意义**；
    // 非法值是调用方的错误，不应被此处掩盖。
    std::uint8_t PayloadLength() const noexcept { return dlc; }
};

// 总线统计。资料要求"用 SocketCAN 处理报文和总线状态，保留错误计数"，
// 且 bus-off 恢复是部署参数，需要可观测。
struct CanBusStats {
    std::uint64_t tx_frames = 0;
    std::uint64_t rx_frames = 0;
    std::uint64_t tx_errors = 0;
    std::uint64_t rx_errors = 0;
    std::uint64_t bus_off_count = 0;   // 进入 bus-off 的次数
    std::uint64_t last_error_us = 0;
};

// 接收过滤规则。can_id + mask，符合 SocketCAN 语义。
struct CanFilter {
    std::uint32_t id = 0;
    std::uint32_t mask = 0;
};

class ICanDevice : public IDevice {
public:
    DeviceKind Kind() const noexcept override { return DeviceKind::Can; }

    virtual CanBackend Backend() const noexcept = 0;
    virtual CanCapabilities Capabilities() const = 0;

    virtual Status Send(const CanFrame& frame) noexcept = 0;

    // timeout_ms: 0 = 非阻塞轮询；>0 = 等待至多该毫秒数。
    // 超时返回 ECommandTimeout，而不是错误 —— 总线空闲是正常状态。
    virtual Result<CanFrame> Receive(int timeout_ms) = 0;

    virtual Status SetFilters(const std::vector<CanFilter>& filters) = 0;
    virtual CanBusStats Stats() const = 0;

    // bus-off 后恢复。资料把"bus-off 自动恢复"列为部署策略，需显式可控。
    virtual Status RecoverFromBusOff() = 0;

protected:
    ICanDevice() = default;
};

}  // namespace km3
