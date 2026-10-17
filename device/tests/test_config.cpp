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
      "device":  { "id": "km3-prod-001", "name": "robot", "model": "km3" },
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

    KM3_CHECK_EQ(c.device_id, std::string("km3-prod-001"));
    KM3_CHECK_EQ(c.model, std::string("km3"));
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
    // 出厂模板用 km3-REPLACE-001 占位，必须替换后才能通过。
    // 这条规则是"故意让模板校验失败"，防止带着占位 ID 上现场。
    // 注意 REPLACE 不在环境取值域里，所以它同时也被下面的规则 8 拦下。
    auto r = LoadConfigText(WithField("\"id\": \"km3-prod-001\"", "\"id\": \"km3-REPLACE-001\""));
    KM3_CHECK(r.ok());
    KM3_CHECK(!ValidateConfig(r.value()).ok());
    KM3_CHECK(ValidateConfig(r.value()).code() == ErrorCode::EInvalidConfig);
}

KM3_TEST(rejects_device_id_whose_env_segment_is_not_a_known_environment) {
    // 编号规则 <前缀>-<env>-<seq>，其中 env 必须取自 environments.code
    // （server/migrations/0001_init.sql 的种子：test / demo / prod）。
    // 写错 env 的表现是"设备出现在后台列表里但谁也连不上"——要跨服务器、
    // 管理端、前端三端才能反查，因此在加载期就拦下。
    for (const char* env : {"test", "demo", "prod"}) {
        const std::string j = WithField("\"id\": \"km3-prod-001\"",
                                        std::string("\"id\": \"km3-") + env + "-001\"");
        KM3_CHECK(Valid(j));
    }

    // 未知环境：大小写也不放过（environments.code 是小写）
    for (const char* bad : {"staging", "dev", "production", "TEST", "Prod", "replace"}) {
        const std::string j = WithField("\"id\": \"km3-prod-001\"",
                                        std::string("\"id\": \"km3-") + bad + "-001\"");
        KM3_CHECK(!Valid(j));
    }
}

KM3_TEST(rejects_device_id_with_a_malformed_prefix_or_seq) {
    // 少于三段拆不出 env；空前缀 / 空 seq / 字母 seq 都让编号规则失去意义。
    for (const char* bad : {"km3-001", "km3", "km3-test", "-test-001", "km3--001",
                            "km3-test-", "km3-test-foo", "km3-test-1a"}) {
        const std::string j = WithField("\"id\": \"km3-prod-001\"",
                                        std::string("\"id\": \"") + bad + "\"");
        KM3_CHECK(!Valid(j));
    }

    // 多段前缀也接受：<前缀> 取设备型号时可能自带连字符
    KM3_CHECK(Valid(WithField("\"id\": \"km3-prod-001\"",
                              "\"id\": \"km3-wcr-prod-007\"")));
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

// ===================== 结构与取值边界的加固（2026-10-14）=====================
// 以下四组用例对应 config.cpp 的四处修复。它们的共同背景是：配置文件里写错的
// 东西，必须在**加载期**变成一个可报告的错误码，既不能把进程打崩，也不能静默
// 退化成"看起来正常"的配置——后者会让错误滑到现场，以"电机不动"这种最难反查
// 的形式暴露。

KM3_TEST(wrong_typed_section_is_reported_not_crashed) {
  // 段存在但不是对象时，旧实现会让内层 .value() 抛 nlohmann type_error 并
  // SIGABRT（视频段与总线段都实测过）。这与文件顶部"配置文件的错误应由
  // ValidateConfig 统一报告，而不是在解析途中崩掉"的承诺直接矛盾。
  // 现在必须返回一个错误码，而不是终止进程。
  for (const char* bad : {"null", "[]", "\"x\"", "42", "true"}) {
    const std::string j = WithField("\"bus\": {", std::string("\"bus\": ") + bad + ", \"bus_unused\": {");
    auto r = LoadConfigText(j);
    // 只要求"有结果、不崩"，且不允许它被当成合法配置放行
    KM3_CHECK(!(r.ok() && ValidateConfig(r.value()).ok()));
  }

  // video 段在 BaseJson 里不存在，因此注入点选在 time 段之前
  const std::string v = WithField("\"time\":", "\"video\": [], \"time\":");
  auto r = LoadConfigText(v);
  KM3_CHECK(!(r.ok() && ValidateConfig(r.value()).ok()));

  // 正常对象仍通过
  KM3_CHECK(Valid(BaseJson()));
}

KM3_TEST(wrong_typed_bus_subarray_is_reported) {
  // 总线段的子数组类型写错（例如 "can" 写成对象而不是数组），同样必须报错。
  // 静默退化的后果特别隐蔽：变成"零条总线"，而没有总线的配置能通过校验。
  // BaseJson 的 bus 段没有 "can" 子数组，注入点选在 rs485 之前
  const std::string j = WithField(
      "\"rs485\":", "\"can\": { \"unused\": [] }, \"rs485\":");
  auto r = LoadConfigText(j);
  KM3_CHECK(!(r.ok() && ValidateConfig(r.value()).ok()));

  // 子数组类型错写成字符串，同样必须报错
  const std::string j2 = WithField(
      "\"rs485\":", "\"can\": \"oops\", \"rs485\":");
  auto r2 = LoadConfigText(j2);
  KM3_CHECK(!(r2.ok() && ValidateConfig(r2.value()).ok()));
}

KM3_TEST(rejects_recording_root_sharing_a_string_prefix_but_not_a_path) {
  // 规则本意是"录像目录必须位于数据盘之内"，否则会悄悄写到根分区
  // （实测根分区仅剩 4.2 GiB）。旧实现用裸字符串 rfind 做前缀判断，
  // 于是 data_root="/data" 时 "/database_evil" 与 "/dataX" 都被放行——
  // 它们与数据盘只是共享字符串前缀，并不是它的子路径。
  for (const char* evil : {"/database_evil", "/dataX", "/data2", "/data_old"}) {
    const std::string j = WithField(
        "\"recording_enabled\": false",
        std::string("\"recording_enabled\": true, \"recording_root\": \"") + evil + "\"");
    KM3_CHECK(!Valid(j));
  }

  // 真正的子路径仍要通过
  const std::string ok = WithField(
      "\"recording_enabled\": false",
      "\"recording_enabled\": true, \"recording_root\": \"/data/recordings\"");
  KM3_CHECK(Valid(ok));

  // data_root 本身也允许（等价于"整个数据盘"）
  const std::string same = WithField(
      "\"recording_enabled\": false",
      "\"recording_enabled\": true, \"recording_root\": \"/data\"");
  KM3_CHECK(Valid(same));
}

KM3_TEST(rejects_out_of_range_g30_port) {
  // g30_port 读作 uint32 后直接 static_cast<uint16>，65536 会静默变成 0。
  // 端口号是配置里最容易被手抖改错的一项，且没有任何运行时读者兜底。
  KM3_CHECK(!Valid(WithField("\"g30_port\": 8888", "\"g30_port\": 0")));
  KM3_CHECK(!Valid(WithField("\"g30_port\": 8888", "\"g30_port\": 65536")));
  KM3_CHECK(!Valid(WithField("\"g30_port\": 8888", "\"g30_port\": 100000")));

  // 边界内的仍要通过
  KM3_CHECK(Valid(WithField("\"g30_port\": 8888", "\"g30_port\": 1")));
  KM3_CHECK(Valid(WithField("\"g30_port\": 8888", "\"g30_port\": 65535")));
}

KM3_TEST(sync_period_is_defaulted_overridable_and_strictly_validated) {
    const auto defaults = LoadConfigText(BaseJson());
    KM3_REQUIRE(defaults.ok());
    KM3_CHECK_EQ(defaults.value().rt.sync_period_us, 4000u);
    for (const char* good : {"1", "8000", "1000000"}) {
        const auto cfg = LoadConfigText(WithField("\"g30_port\": 8888",
            std::string("\"g30_port\": 8888, \"sync_period_us\": ") + good));
        KM3_REQUIRE(cfg.ok());
        KM3_CHECK(ValidateConfig(cfg.value()).ok());
        KM3_CHECK_EQ(cfg.value().rt.sync_period_us, std::stoull(good));
    }
    for (const char* bad : {"0", "-1", "-18446744073709551616", "1000001", "1.5", "4000.0",
                           "true", "false", "null", "\"4000\"", "[]", "{}", "1e100", "1e1000",
                           "18446744073709551615", "18446744073709551616"}) {
        const auto cfg = LoadConfigText(WithField("\"g30_port\": 8888",
            std::string("\"g30_port\": 8888, \"sync_period_us\": ") + bad));
        KM3_CHECK(!cfg.ok());
        KM3_CHECK(cfg.code() == ErrorCode::EInvalidConfig);
    }
    auto cfg = defaults.value();
    for (auto bad : {std::uint64_t{0}, km3::kMaxSyncPeriodUs + 1, UINT64_MAX}) {
        cfg.rt.sync_period_us = bad;
        KM3_CHECK(ValidateConfig(cfg).code() == ErrorCode::EInvalidConfig);
    }
}

KM3_TEST(socketcan_identity_and_rate_must_not_silently_fall_back) {
    for (const char* bad : {"{\"logical\":\"can-2\",\"iface\":3,\"bitrate\":1000000,\"protocol\":\"canopen\"}",
         "{\"logical\":\"can-2\",\"iface\":\"testcan\",\"bitrate\":-1,\"protocol\":\"canopen\"}",
         "{\"logical\":\"can-2\",\"iface\":\"testcan\",\"bitrate\":1000000.0,\"protocol\":\"canopen\"}",
         "{\"logical\":\"can-2\",\"iface\":\"testcan\",\"device\":\"other\",\"bitrate\":1000000,\"protocol\":\"canopen\"}"}) {
        const auto cfg = LoadConfigText(WithField("\"rs485\":", std::string("\"can\": [") + bad + "], \"rs485\":"));
        KM3_CHECK(!cfg.ok());
    }
}

KM3_TEST(sync_cob_id_is_defaulted_overridable_and_strict) {
    KM3_CHECK_EQ(km3::RtParams{}.sync_cob_id, km3::canopen::wire::COB_SYNC);
    for (const auto* bad : {"0", "-1", "2048", "128.0", "null", "true", "\"128\""}) {
        const auto cfg = km3::LoadConfigText(std::string("{\"network\":{\"sync_cob_id\":") + bad + "}}");
        KM3_CHECK(!cfg.ok());
    }
    const auto cfg = km3::LoadConfigText("{\"network\":{\"sync_cob_id\":129}}");
    KM3_REQUIRE(cfg.ok());
    KM3_CHECK_EQ(cfg.value().rt.sync_cob_id, 129u);
}

KM3_TEST_MAIN()
