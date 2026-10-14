#pragma once
// 硬件抽象层基础类型。见 docs/架构设计/设备端程序架构.md §3.1。
//
// 设计意图（资料已明确记录，不是推测）：
//   业务代码不得出现 /dev/ttyS3、GPIO 全局号、mmc 分区号、DRM plane ID 之类的物理标识。
//   驱动适配层负责把**逻辑身份**映射到当前设备树的实际节点，并在启动时检查。
//   因此接口参数一律用逻辑名（"rs485-1"、"can-2"），而非设备路径。

#include <cstdint>
#include <string>
#include <string_view>

#include "km3/result.hpp"

namespace km3 {

// 设备类别。用于统一的状态查询与健康上报。
enum class DeviceKind : std::uint8_t {
    Unknown = 0,
    Can,
    Rs485,
    Video,
    Gpio,
};

std::string_view ToString(DeviceKind kind);

// 健康状态。注意区分"设备不存在"与"设备存在但通信失败"：
// 前者是配置/接线问题，后者可能是总线故障，处理方式不同。
enum class DeviceStatusEnum : std::uint8_t {
    Unknown = 0,
    Closed,        // 未打开
    Open,          // 已打开
    BusOff,        // 总线关闭（仅 CAN，需恢复流程）
    Error,
};

std::string_view ToString(DeviceStatusEnum status);

struct DeviceStatus {
    DeviceStatusEnum state = DeviceStatusEnum::Unknown;
    std::uint64_t last_ok_us = 0;     // 最近一次成功通信的单调时刻
    std::uint64_t error_count = 0;    // 累计错误次数
    std::string detail;               // 最近一次错误说明
};

// 所有设备的共同接口。
//
// 线程约定：**本层不做内部加锁**。调用方负责保证同一设备不会被多个线程并发访问。
// 这是有意的：设备句柄数量少、访问模式固定（实时线程独占、或边界线程独占），
// 在 HAL 内部加锁只会把不可预测的等待引入实时路径。
class IDevice {
public:
    virtual ~IDevice() = default;

    IDevice(const IDevice&) = delete;
    IDevice& operator=(const IDevice&) = delete;

    virtual DeviceKind Kind() const noexcept = 0;
    // 逻辑名，如 "can-1"、"rs485-2"。用于日志与配置对应，不用于打开设备。
    virtual const std::string& LogicalName() const noexcept = 0;

    virtual Status Open() noexcept = 0;
    virtual Status Close() noexcept = 0;
    virtual bool IsOpen() const noexcept = 0;

    virtual DeviceStatus StatusOf() const = 0;

protected:
    IDevice() = default;
};

}  // namespace km3
