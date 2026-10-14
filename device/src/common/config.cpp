#include "km3/config.hpp"

#include <algorithm>
#include <fstream>
#include <sstream>

#include <nlohmann/json.hpp>

#include "km3/error_code.hpp"

namespace km3 {

namespace {

using json = nlohmann::json;

// 取值辅助：缺失或类型不符时返回默认值，不抛异常。
// 配置文件的错误应由 ValidateConfig 统一报告，而不是在解析途中崩掉。
template <typename T>
T Get(const json& obj, const char* key, const T& fallback) {
    if (!obj.is_object()) {
        return fallback;
    }
    const auto it = obj.find(key);
    if (it == obj.end() || it->is_null()) {
        return fallback;
    }
    try {
        return it->get<T>();
    } catch (const json::exception&) {
        return fallback;
    }
}

// 取一个对象段。段存在但不是对象（null/数组/字符串/数字）时返回空对象，
// 而不是让内层 .value() 抛 type_error 把进程 SIGABRT —— config.cpp 顶部的注释
// 明确承诺"配置文件的错误应由 ValidateConfig 统一报告，而不是在解析途中崩掉"。
// 返回空对象意味着该段退化为全默认值，与"段缺失"同义，这是可报告的弱化而非崩溃。
json SectionOf(const json& root, const char* key) {
    const auto it = root.find(key);
    if (it == root.end() || !it->is_object()) {
        return json::object();
    }
    return *it;
}

// 取一个数组字段。存在但不是数组时返回空数组（同上：可报告的弱化，不崩）。
json ArrayOf(const json& obj, const char* key) {
    const auto it = obj.find(key);
    if (it == obj.end() || !it->is_array()) {
        return json::array();
    }
    return *it;
}

// 段或字段存在但类型不符时，必须**报错**——既不能崩，也不能静默退化。
// 崩：内层 .value() 抛 nlohmann type_error 直接 SIGABRT，与文件顶部
//     "配置文件的错误应由 ValidateConfig 统一报告，而不是在解析途中崩掉" 相矛盾。
// 静默退化：类型写错的 "bus" 段会变成"零条总线"，而没有总线的配置是能通过
//     校验的，错误会一路滑到运行时，以"电机不动"这种最难反查的形式暴露。
bool WrongShape(const json& obj, const char* key, bool want_object) {
    const auto it = obj.find(key);
    if (it == obj.end()) {
        return false;
    }
    return want_object ? !it->is_object() : !it->is_array();
}

// CAN 端点用 "iface"，串口端点用 "device"。两者统一落到 BusEndpoint::device。
std::vector<BusEndpoint> ParseEndpoints(const json& arr) {
    std::vector<BusEndpoint> out;
    if (!arr.is_array()) {
        return out;
    }
    for (const auto& e : arr) {
        if (!e.is_object()) {
            continue;
        }
        BusEndpoint ep;
        ep.logical = Get<std::string>(e, "logical", "");
        ep.device = Get<std::string>(e, "device", Get<std::string>(e, "iface", ""));
        ep.baudrate = Get<std::uint32_t>(e, "baudrate", Get<std::uint32_t>(e, "bitrate", 115200));
        ep.protocol = Get<std::string>(e, "protocol", "");
        out.push_back(std::move(ep));
    }
    return out;
}

}  // namespace

Result<DeviceConfig> LoadConfigText(const std::string& json_text) {
    json root;
    try {
        root = json::parse(json_text);
    } catch (const json::parse_error&) {
        return Result<DeviceConfig>::Err(ErrorCode::EInvalidConfig);
    }
    if (!root.is_object()) {
        return Result<DeviceConfig>::Err(ErrorCode::EInvalidConfig);
    }

    // 段形状预检：类型不符直接报错，不进入解析（理由见 WrongShape）。
    // "motion"/"sensors" 在出厂模板里不存在，缺失不算错。
    for (const char* s : {"device", "network", "storage", "bus", "video",
                          "motion", "sensors", "time"}) {
        if (WrongShape(root, s, true)) {
            return Result<DeviceConfig>::Err(ErrorCode::EInvalidConfig);
        }
    }

    DeviceConfig cfg;

    const json dev = SectionOf(root, "device");
    cfg.device_id   = Get<std::string>(dev, "id", "");
    cfg.device_name = Get<std::string>(dev, "name", "");
    cfg.model       = Get<std::string>(dev, "model", "");

    const json net = SectionOf(root, "network");
    cfg.server_url   = Get<std::string>(net, "server_url", "");
    cfg.g30_ip       = Get<std::string>(net, "g30_ip", "");
    const std::uint32_t g30_port_raw = Get<std::uint32_t>(net, "g30_port", 8888);
    // 先按源类型校验再收窄：65536 静默变成 0 是实测过的缺陷（config.cpp:78）。
    if (g30_port_raw == 0 || g30_port_raw > 65535) {
        return Result<DeviceConfig>::Err(ErrorCode::EInvalidConfig);
    }
    cfg.g30_port     = static_cast<std::uint16_t>(g30_port_raw);
    cfg.iface_uplink = Get<std::string>(net, "iface_uplink", "");
    cfg.iface_g30    = Get<std::string>(net, "iface_g30", "");

    cfg.rt.control_period_us    = Get<std::uint64_t>(net, "control_period_us", 1000);
    cfg.rt.watchdog_direct_us   = Get<std::uint64_t>(net, "watchdog_direct_us", 500000);
    cfg.rt.watchdog_relay_us    = Get<std::uint64_t>(net, "watchdog_relay_us", 3000000);

    // 运动与采样参数（可选段）
    const json motion = SectionOf(root, "motion");
    cfg.rt.climb_speed_max_mps   = Get<float>(motion, "climb_speed_max_mps", 1.0F);
    cfg.rt.lift_position_min_mm  = Get<float>(motion, "lift_position_min_mm", 0.0F);
    cfg.rt.lift_position_max_mm  = Get<float>(motion, "lift_position_max_mm", 1000.0F);

    const json sensors = SectionOf(root, "sensors");
    cfg.rt.imu_sample_hz        = Get<std::uint32_t>(sensors, "imu_sample_hz", 100);
    cfg.rt.ultrasonic_sample_hz = Get<std::uint32_t>(sensors, "ultrasonic_sample_hz", 20);
    cfg.rt.pressure_sample_hz   = Get<std::uint32_t>(sensors, "pressure_sample_hz", 10);

    const json st = SectionOf(root, "storage");
    cfg.storage.data_root              = Get<std::string>(st, "data_root", "/data");
    cfg.storage.log_retention_days     = Get<std::uint32_t>(st, "log_retention_days", 30);
    cfg.storage.sensor_retention_days  = Get<std::uint32_t>(st, "sensor_data_retention_days",
                                       Get<std::uint32_t>(st, "sensor_retention_days", 30));
    cfg.storage.recording_enabled      = Get<bool>(st, "recording_enabled", false);
    cfg.storage.recording_root         = Get<std::string>(st, "recording_root", "");
    if (cfg.storage.recording_root.empty()) {
        cfg.storage.recording_root = cfg.storage.data_root + "/recordings";
    }

    const json bus = SectionOf(root, "bus");
    cfg.rs485     = ParseEndpoints(ArrayOf(bus, "rs485"));
    cfg.rs485_usb = ParseEndpoints(ArrayOf(bus, "rs485_usb"));
    cfg.can       = ParseEndpoints(ArrayOf(bus, "can"));
    cfg.usb_can   = ParseEndpoints(ArrayOf(bus, "usb_can"));

    for (const char* s : {"rs485", "rs485_usb", "can", "usb_can"}) {
        if (WrongShape(bus, s, false)) {
            return Result<DeviceConfig>::Err(ErrorCode::EInvalidConfig);
        }
    }

    const json video = SectionOf(root, "video");
    if (WrongShape(video, "cameras", false)) {
        return Result<DeviceConfig>::Err(ErrorCode::EInvalidConfig);
    }

    for (const auto& c : ArrayOf(video, "cameras")) {
        if (!c.is_object()) {
            continue;
        }
        CameraConfig cam;
        cam.id           = Get<std::string>(c, "id", "");
        cam.device_node  = Get<std::string>(c, "device", Get<std::string>(c, "device_node", ""));
        cam.fps          = Get<std::uint32_t>(c, "fps", 0);
        cam.bitrate_kbps = Get<std::uint32_t>(c, "bitrate_kbps", 0);
        cam.pixel_format = Get<std::string>(c, "pixel_format", "");
        // resolution 形如 "1920x1080"
        const std::string res = Get<std::string>(c, "resolution", "");
        const auto x = res.find('x');
        if (x != std::string::npos) {
            try {
                cam.width  = static_cast<std::uint32_t>(std::stoul(res.substr(0, x)));
                cam.height = static_cast<std::uint32_t>(std::stoul(res.substr(x + 1)));
            } catch (const std::exception&) {
                cam.width = cam.height = 0;
            }
        }
        cfg.cameras.push_back(std::move(cam));
    }

    const json time_cfg = SectionOf(root, "time");
    cfg.require_time_sync_before_signed_ops =
        Get<bool>(time_cfg, "require_sync_before_signed_ops", true);

    return Result<DeviceConfig>::Ok(std::move(cfg));
}

Result<DeviceConfig> LoadConfigFile(const std::string& path) {
    std::ifstream in(path);
    if (!in) {
        return Result<DeviceConfig>::Err(ErrorCode::ENotFound);
    }
    std::stringstream buf;
    buf << in.rdbuf();
    return LoadConfigText(buf.str());
}

Status ValidateConfig(const DeviceConfig& cfg) {
    // 这些规则对应文档里已经写明的约束。放在这里检查而不是只写在文档里，
    // 是因为"配置错了但没人拦"在现场表现为难以定位的行为异常。

    // 1) 设备编号：部署时必须替换占位符
    if (cfg.device_id.empty() || cfg.device_id.find("REPLACE") != std::string::npos) {
        return Status(ErrorCode::EInvalidConfig);
    }

    // 2) 服务器地址必须带版本前缀。路由是 /api/v1/device/ws，
    //    早期配置漏掉 /api/v1 导致连接 404，属于真实发生过的缺陷。
    if (cfg.server_url.empty() ||
        cfg.server_url.find("/api/v1") == std::string::npos) {
        return Status(ErrorCode::EInvalidConfig);
    }

    // 3) 存储不得落在根分区。实测根分区仅剩 4.2 GiB（见 实测基线.md §三），
    //    日志/缓存/录像写进去会很快耗尽。
    if (cfg.storage.data_root.empty() || cfg.storage.data_root[0] != '/' ||
        cfg.storage.data_root == "/" ||
        cfg.storage.data_root.rfind("/opt/km3", 0) == 0) {
        return Status(ErrorCode::EInvalidConfig);
    }
    // 按**路径分量**比较，不是裸字符串前缀：data_root="/data" 时
    // "/database_evil" 与 "/dataX" 都必须被拒（裸 rfind 会放行）。
    const std::string data_root = cfg.storage.data_root;
    const std::string rec_root  = cfg.storage.recording_root;
    const bool rec_inside_data = rec_root.size() > data_root.size() &&
                                 rec_root.compare(0, data_root.size(), data_root) == 0 &&
                                 rec_root[data_root.size()] == '/';
    if (!rec_inside_data && rec_root != data_root) {
        // 录像目录必须位于数据盘之内，否则会悄悄写到别处
        return Status(ErrorCode::EInvalidConfig);
    }

    // 4) 看门狗层级：直连阈值必须小于中转阈值，否则中转通道会被误判超时
    if (cfg.rt.watchdog_direct_us == 0 || cfg.rt.watchdog_relay_us == 0 ||
        cfg.rt.watchdog_direct_us >= cfg.rt.watchdog_relay_us) {
        return Status(ErrorCode::EInvalidConfig);
    }

    // 5) 控制周期必须为正，且不应低于 100us（低于此值在 RK3588 上没有意义）
    if (cfg.rt.control_period_us < 100) {
        return Status(ErrorCode::EInvalidConfig);
    }

    // 6) 抬降行程上下限必须有序
    if (cfg.rt.lift_position_min_mm >= cfg.rt.lift_position_max_mm) {
        return Status(ErrorCode::EInvalidConfig);
    }

    // 7) 总线逻辑名不得重复、不得为空 —— 逻辑名是业务代码的唯一身份
    std::vector<std::string> all;
    for (const auto* v : {&cfg.rs485, &cfg.rs485_usb, &cfg.can, &cfg.usb_can}) {
        for (const auto& ep : *v) {
            if (ep.logical.empty()) {
                return Status(ErrorCode::EInvalidConfig);
            }
            if (std::find(all.begin(), all.end(), ep.logical) != all.end()) {
                return Status(ErrorCode::EInvalidConfig);
            }
            all.push_back(ep.logical);
        }
    }

    return Status::Ok();
}

}  // namespace km3
