// 设备端配置：加载、校验、以及"文档约束变成可执行规则"的验证。
//
// 这些断言的分量比普通单测重：校验规则里有 7 条直接对应现场事故——
// 把日志写进只剩 4.2 GiB 的根分区、漏掉 /api/v1 导致 404、
// 看门狗阈值层级颠倒导致中转通道被误判超时，等等。
// 规则一旦被放松，这里必须失败。
#include <string>
#include <type_traits>

#include "km3/config.hpp"
#include "km3/config_snapshot.hpp"
#include "km3/error_code.hpp"
#include "test_harness.hpp"

using km3::DeviceConfig;
using km3::ErrorCode;
using km3::LoadConfigFile;
using km3::LoadConfigText;
using km3::RtParams;
using km3::ValidateConfig;

namespace {

// 一份最小可用配置。各用例在此基础上改一个字段，隔离被测规则。
std::string BaseJson() {
    return R"({
      "device":  { "id": "wcr-prod-001", "name": "robot", "model": "wcr" },
      "network": {
        "server_url": "wss://api.example.com/api/v1/device/ws",
        "g30_ip": "192.168.1.100", "g30_port": 8888,
        "iface_uplink": "eth0", "iface_g30": "eth1",
        "watchdog_direct_us": 500000, "watchdog_relay_us": 3000000
      },
      "storage": {
        "data_root": "/data",
        "log_retention_days": 30,
        "sensor_data_retention_days": 30,
        "recording_enabled": false
      },
      "bus": {
        "rs485":     [ { "logical": "rs485-1", "device": "/dev/ttyS3", "baudrate": 115200 } ],
        "usb_can":   [ { "logical": "usb-can-1", "iface": "can2", "bitrate": 500000, "protocol": "canfd" } ]
      },
      "motion": { "climb_speed_max_mps": 0.8 },
      "time":   { "require_sync_before_signed_ops": true }
    })";
}

// 按需要替换 BaseJson 中的一段
std::string WithField(const std::string& find, const std::string& replace_with) {
    std::string j = BaseJson();
    const auto pos = j.find(find);
    if (pos == std::string::npos) {
        return j;
    }
    return j.replace(pos, find.size(), replace_with);
}

bool Valid(const std::string& json) {
    auto r = LoadConfigText(json);
    return r.ok() && ValidateConfig(r.value()).ok();
}

}  // namespace

// ===================== 加载 =====================

KM3_TEST(loads_a_valid_config) {
    auto r = LoadConfigText(BaseJson());
    KM3_CHECK(r.ok());
    const DeviceConfig& c = r.value();

    KM3_CHECK_EQ(c.device_id, std::string("wcr-prod-001"));
    KM3_CHECK_EQ(c.model, std::string("wcr"));
    KM3_CHECK_EQ(c.g30_port, std::uint16_t{8888});
    KM3_CHECK_EQ(c.rt.control_period_us, std::uint64_t{1000});   // 默认 1 kHz
    KM3_CHECK_EQ(c.rt.watchdog_direct_us, std::uint64_t{500000});
    KM3_CHECK_EQ(c.rt.watchdog_relay_us, std::uint64_t{3000000});
    KM3_CHECK(ValidateConfig(c).ok());
}

KM3_TEST(rejects_malformed_json_with_a_specific_code) {
    auto r = LoadConfigText("{ this is not json ");
    KM3_CHECK(!r.ok());
    KM3_CHECK(r.code() == ErrorCode::EInvalidConfig);

    // 顶层不是对象同样拒绝，而不是静默当成空配置
    auto r2 = LoadConfigText("[1, 2, 3]");
    KM3_CHECK(!r2.ok());
    KM3_CHECK(r2.code() == ErrorCode::EInvalidConfig);
}

KM3_TEST(missing_file_is_not_found_not_invalid) {
    auto r = LoadConfigFile("/nonexistent/km3/device.json");
    KM3_CHECK(!r.ok());
    // 区分"文件不存在"与"文件内容非法"：前者是部署问题，后者是配置问题
    KM3_CHECK(r.code() == ErrorCode::ENotFound);
}

KM3_TEST(accepts_can_iface_alias_and_serial_device) {
    // CAN 端点写 "iface"，串口端点写 "device"，两者统一落到 BusEndpoint::device
    auto r = LoadConfigText(BaseJson());
    KM3_CHECK(r.ok());
    KM3_CHECK_EQ(r.value().rs485.size(), std::size_t{1});
    KM3_CHECK_EQ(r.value().rs485[0].device, std::string("/dev/ttyS3"));
    KM3_CHECK_EQ(r.value().usb_can.size(), std::size_t{1});
    KM3_CHECK_EQ(r.value().usb_can[0].device, std::string("can2"));
    KM3_CHECK_EQ(r.value().usb_can[0].protocol, std::string("canfd"));
}

// ===================== 校验规则 =====================

KM3_TEST(rejects_unreplaced_placeholder_device_id) {
    // 出厂模板用 wcr-REPLACE-001 占位，必须替换后才能通过。
    // 这条规则是"故意让模板校验失败"，防止带着占位 ID 上现场。
    auto r = LoadConfigText(WithField("\"id\": \"wcr-prod-001\"", "\"id\": \"wcr-REPLACE-001\""));
    KM3_CHECK(r.ok());
    KM3_CHECK(!ValidateConfig(r.value()).ok());
    KM3_CHECK(ValidateConfig(r.value()).code() == ErrorCode::EInvalidConfig);
}

KM3_TEST(shipped_template_must_not_validate_as_is) {
    // 直接读仓库里的模板文件：它**应当**校验失败（含 REPLACE 占位）。
    // 若这条断言开始失败，说明有人放松了规则或替换了模板，需要人工确认。
    //
    // 路径由 CMake 在编译期以绝对路径注入（KM3_REPO_ROOT），
    // 这样无论从哪个目录运行测试，结果都一致 —— 之前用相对路径，
    // 从仓库根跑通过、从构建目录跑失败，属于测试自身依赖环境 CWD 的缺陷。
#ifdef KM3_REPO_ROOT
    const std::string path = std::string(KM3_REPO_ROOT) + "/device/config/device.json";
#else
    const std::string path = "device/config/device.json";   // 未注入时的退化路径
#endif

    auto r = LoadConfigFile(path);
    KM3_CHECK(r.ok());                                   // 语法合法
    KM3_CHECK(!ValidateConfig(r.value()).ok());          // 但语义不达标
}

KM3_TEST(rejects_server_url_without_api_version_prefix) {
    // 真实发生过的缺陷：配置漏掉 /api/v1，连接返回 404。
    auto r = LoadConfigText(
        WithField("wss://api.example.com/api/v1/device/ws",
                  "wss://api.example.com/device/ws"));
    KM3_CHECK(r.ok());
    KM3_CHECK(!ValidateConfig(r.value()).ok());
}

KM3_TEST(rejects_data_root_on_the_root_partition) {
    // 根分区实测仅剩 4.2 GiB。数据目录不允许是 "/" 或挂在 /opt/km3 之下。
    KM3_CHECK(!Valid(WithField("\"data_root\": \"/data\"", "\"data_root\": \"/\"")));
    KM3_CHECK(!Valid(WithField("\"data_root\": \"/data\"", "\"data_root\": \"/opt/km3/data\"")));
    // 相对路径同样拒绝
    KM3_CHECK(!Valid(WithField("\"data_root\": \"/data\"", "\"data_root\": \"data\"")));
    // 正常值仍通过
    KM3_CHECK(Valid(WithField("\"data_root\": \"/data\"", "\"data_root\": \"/srv/km3\"")));
}

KM3_TEST(rejects_recording_root_outside_data_root) {
    // 录像目录必须落在数据盘内，否则会悄悄写到别处（例如根分区）
    const std::string j = WithField(
        "\"recording_enabled\": false",
        "\"recording_enabled\": true, \"recording_root\": \"/var/recordings\"");
    KM3_CHECK(!Valid(j));
}

KM3_TEST(derives_recording_root_when_omitted) {
    auto r = LoadConfigText(BaseJson());
    KM3_CHECK(r.ok());
    // 未配置时应推导为 data_root + "/recordings"，而不是留空
    KM3_CHECK_EQ(r.value().storage.recording_root, std::string("/data/recordings"));
}

KM3_TEST(rejects_inverted_watchdog_thresholds) {
    // 直连阈值必须小于中转阈值。反了会让中转通道永远超时。
    const std::string bad = WithField("\"watchdog_direct_us\": 500000",
                                      "\"watchdog_direct_us\": 5000000");
    KM3_CHECK(!Valid(bad));

    // 相等同样拒绝：两个通道语义不同，不该共用同一阈值
    const std::string same = WithField("\"watchdog_direct_us\": 500000",
                                       "\"watchdog_direct_us\": 3000000");
    KM3_CHECK(!Valid(same));

    KM3_CHECK(Valid(BaseJson()));
}

KM3_TEST(rejects_absurd_control_period) {
    // 100us 以下的周期在 RK3588 上没有意义，属于配置错误而非"更实时"
    const std::string bad = WithField("\"g30_port\": 8888",
                                      "\"g30_port\": 8888, \"control_period_us\": 10");
    KM3_CHECK(!Valid(bad));

    const std::string ok = WithField("\"g30_port\": 8888",
                                     "\"g30_port\": 8888, \"control_period_us\": 2000");
    KM3_CHECK(Valid(ok));
}

KM3_TEST(rejects_inverted_lift_limits) {
    // 行程上下限颠倒会让安全监控的边界判断失去意义
    const std::string bad = WithField(
        "\"motion\": { \"climb_speed_max_mps\": 0.8 }",
        "\"motion\": { \"climb_speed_max_mps\": 0.8, \"lift_position_min_mm\": 900, \"lift_position_max_mm\": 100 }");
    KM3_CHECK(!Valid(bad));
}

KM3_TEST(rejects_duplicate_or_empty_logical_names) {
    // 逻辑名是业务代码识别总线的唯一身份，重复会导致指令发到错误的总线
    const std::string dup = WithField(
        "[ { \"logical\": \"rs485-1\", \"device\": \"/dev/ttyS3\", \"baudrate\": 115200 } ]",
        "[ { \"logical\": \"rs485-1\", \"device\": \"/dev/ttyS3\", \"baudrate\": 115200 },"
        "  { \"logical\": \"rs485-1\", \"device\": \"/dev/ttyS5\", \"baudrate\": 115200 } ]");
    KM3_CHECK(!Valid(dup));

    const std::string empty = WithField(
        "[ { \"logical\": \"rs485-1\", \"device\": \"/dev/ttyS3\", \"baudrate\": 115200 } ]",
        "[ { \"logical\": \"\", \"device\": \"/dev/ttyS3\", \"baudrate\": 115200 } ]");
    KM3_CHECK(!Valid(empty));

    // 同一逻辑名不得跨总线类型重复（rs485 与 usb_can 之间也不允许）
    const std::string cross = WithField(
        "[ { \"logical\": \"usb-can-1\", \"iface\": \"can2\", \"bitrate\": 500000, \"protocol\": \"canfd\" } ]",
        "[ { \"logical\": \"rs485-1\", \"iface\": \"can2\", \"bitrate\": 500000 } ]");
    KM3_CHECK(!Valid(cross));
}

// ===================== 实时快照桥接 =====================

KM3_TEST(rt_params_are_pod_suitable_for_the_snapshot) {
    // RtParams 会被放进 ConfigSnapshot，由实时线程无锁读取。
    // 因此它必须是平凡可拷贝且不含任何会分配内存的成员。
    static_assert(std::is_trivially_copyable<RtParams>::value,
                  "RtParams must be trivially copyable: it goes into the RT snapshot");
    static_assert(std::is_default_constructible<RtParams>::value,
                  "RtParams must be default constructible");

    // 体积可控：实时路径上不该出现大对象拷贝
    KM3_CHECK(sizeof(RtParams) <= 128);

    auto r = LoadConfigText(BaseJson());
    KM3_CHECK(r.ok());
    km3::ConfigSnapshot<RtParams> snap(r.value().rt);
    KM3_CHECK_EQ(snap.Get().control_period_us, std::uint64_t{1000});
    KM3_CHECK_EQ(snap.PublishedCount(), std::uint64_t{0});
}

KM3_TEST(cameras_are_absent_by_default_and_params_not_invented) {
    // 实测中摄像头未接入、参数未定案。默认配置不应凭空造出 6 路 1080p。
    auto r = LoadConfigText(BaseJson());
    KM3_CHECK(r.ok());
    KM3_CHECK_EQ(r.value().cameras.size(), std::size_t{0});
}

KM3_TEST_MAIN()
