#pragma once
#include "km3/canopen_wire_generated.hpp"
// 设备端配置：加载、校验、以及对实时线程可见的部分。
// 依据 docs/架构设计/设备端程序架构.md §2.3.5 与 开发规范.md §1.4。
//
// **两类配置必须分开**（这是本文件最重要的设计点）：
//
//   RtParams     —— 纯 POD，无 std::string/容器。放进 ConfigSnapshot，
//                   实时线程无锁读取原子快照指针。周期内绝不解析 JSON。
//   DeviceConfig —— 完整配置，可含字符串与列表。只有非实时进程（km3-core、
//                   km3-video）使用它，或由启动阶段一次性搬运到 RtParams。
//
// 把 URL、路径这类 std::string 塞进实时快照是常见错误：字符串赋值会分配内存。

#include <cstdint>
#include <string>
#include <vector>

#include "km3/result.hpp"

namespace km3 {

// Operational bound: at most one second between SYNC frames. This is a host
// scheduling/configuration bound, NOT certification of any motion period.
inline constexpr std::uint64_t kMaxSyncPeriodUs = 1000000;

// 实时线程可见参数。全部平凡可拷贝（ConfigSnapshot 有静态断言把关）。
struct RtParams {
    std::uint64_t control_period_us   = 1000;     // 1 kHz
    // Shared with future cyclic paths; publishing config does not enable sending.
    // 4000us follows the can-2 bandwidth budget; enable explicitly at process start.
    std::uint64_t sync_period_us      = 4000;
    std::uint32_t sync_cob_id = canopen::wire::COB_SYNC;
    // 看门狗阈值随通道变化：直连比中转快得多。切换通道时必须重置计时，
    // 否则一次切换就会误触发安全模式（见 通信协议规范.md §八）。
    std::uint64_t watchdog_direct_us  = 500000;   // 500 ms
    std::uint64_t watchdog_relay_us   = 3000000;  // 3 s
    float climb_speed_max_mps         = 1.0F;
    float lift_position_min_mm        = 0.0F;
    float lift_position_max_mm        = 1000.0F;
    std::uint32_t imu_sample_hz       = 100;
    std::uint32_t ultrasonic_sample_hz = 20;
    std::uint32_t pressure_sample_hz  = 10;
};

// 一条总线端点。逻辑名（"can-1"、"rs485-2"）是业务代码里的唯一身份，
// 物理路径（/dev/ttyS3、can0）只在这里出现一次，由 HAL 层消费。
struct BusEndpoint {
    std::string logical;          // 逻辑名，业务代码使用
    std::string device;           // 物理节点；CAN 用 iface 字段
    std::uint32_t baudrate = 115200;
    std::string protocol;         // "raw" | "modbus" | "canopen" | "canfd"
};

struct CameraConfig {
    std::string id;
    std::string device_node;
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::uint32_t fps = 0;
    std::string pixel_format;     // 空 = 由摄像头能力协商，不写死 YUYV
    std::uint32_t bitrate_kbps = 0;
};

struct StorageConfig {
    std::string data_root = "/data";
    std::uint32_t log_retention_days = 30;
    std::uint32_t sensor_retention_days = 30;
    bool recording_enabled = false;
    std::string recording_root;   // 省略时取 data_root + "/recordings"
};

struct DeviceConfig {
    // 身份
    std::string device_id;
    std::string device_name;
    std::string model;

    // 网络
    std::string server_url;
    std::string g30_ip;
    std::uint16_t g30_port = 8888;
    std::string iface_uplink;
    std::string iface_g30;

    // 实时参数（会单独拷进 ConfigSnapshot）
    RtParams rt;

    StorageConfig storage;

    std::vector<BusEndpoint> rs485;
    std::vector<BusEndpoint> rs485_usb;
    std::vector<BusEndpoint> can;
    std::vector<BusEndpoint> usb_can;
    std::vector<CameraConfig> cameras;

    bool require_time_sync_before_signed_ops = true;
};

// 从文件加载。文件不存在或 JSON 语法错误返回对应错误码。
Result<DeviceConfig> LoadConfigFile(const std::string& path);

// 从 JSON 文本加载。测试用，避免依赖文件系统。
Result<DeviceConfig> LoadConfigText(const std::string& json_text);

// 校验：把文档里的硬性约束变成可执行规则，而不是只写在文档里。
// 返回第一个违反项，错误码 EINVALID_CONFIG。
Status ValidateConfig(const DeviceConfig& cfg);

}  // namespace km3
