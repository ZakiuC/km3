#pragma once
// 可进入环形缓冲的记录类型：全部是定长 POD。
// 禁止在这里放入 std::string / 容器 / 指针（见 spsc_ring.hpp 的约束）。

#include <cstdint>

namespace km3 {

// 定长器件名：避免 std::string。协议里的 sensor_id 最长按此长度截断。
struct DeviceTag {
    static constexpr std::size_t kLen = 24;
    char name[kLen] = {};

    static DeviceTag From(const char* src) noexcept;
};

struct MotionSnapshot {
    std::uint64_t ts_us = 0;          // 单调时钟
    float position_mm = 0.0F;
    float speed_mps = 0.0F;
    float current_a = 0.0F;
    float temperature_c = 0.0F;
    std::uint8_t subsystem = 0;       // 见 Subsystem 枚举
    std::uint8_t flags = 0;           // 位标志：故障/就绪/限位
};

enum class Subsystem : std::uint8_t {
    Climb = 0,
    SuspensionPoint = 1,
    Thruster = 2,
    LiftA = 3,
    LiftB = 4,
    BrushLift = 5,
    SqueegeeLift = 6,
    BrushSpin = 7,     // 继电器通断，无反馈
    Blower = 8,
    AirKnife = 9,
};

// 传感器采样：一个定长记录表示一个通道的一个采样点。
struct SensorSample {
    std::uint64_t ts_us = 0;          // 单调时钟
    DeviceTag sensor_id{};
    float value = 0.0F;
    std::uint8_t quality = 0;         // 0..100
    std::uint8_t kind = 0;            // 见 SensorKind
};

enum class SensorKind : std::uint8_t {
    Imu = 0,
    Ultrasonic = 1,
    Pressure = 2,
    Temperature = 3,
    Humidity = 4,
    Analog = 5,
};

// 安全事件：优先级最高，必须能进入边界而不被丢弃设计掩盖。
struct SafetyEvent {
    std::uint64_t ts_us = 0;
    std::uint16_t code = 0;           // 对应 ErrorCode 数值
    std::uint8_t severity = 0;
    char detail[48] = {};
};

}  // namespace km3
