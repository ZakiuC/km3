#include "ui.hpp"
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {
double Number(const std::string& text) {
    std::size_t used = 0;
    double value = std::stod(text, &used);
    if (used != text.size() || !std::isfinite(value)) throw std::invalid_argument("参数必须为有限数值");
    return value;
}
unsigned Integer(const std::string& text, unsigned max) {
    const double value = Number(text);
    if (value < 1 || value > max || std::floor(value) != value) throw std::invalid_argument("整数参数超出范围");
    return static_cast<unsigned>(value);
}
void Help() {
    std::cout << "PMP4010 独立 C++ 手动直线测试（PP 模式）\n"
        "用法: pmp4010_manual_test [--mock] [选项]\n"
        "  --interface 接口名      真机必填；使用已有 SocketCAN，不配置链路\n"
        "  --bitrate 1000000      验证已有接口的位速率，不修改配置\n"
        "  --exclusive-can-ack    明确确认RT及其他CAN控制程序已停止\n"
        "  --node 1               节点 1..127\n"
        "  --mm-per-rev 4         每电机轴转对应的直线距离 mm\n"
        "  --rpm 1                PP 轨迹速度 rpm，不是连续速度控制\n"
        "  --step-mm 0.1          单步距离 mm\n"
        "  --log 路径             操作日志，默认 /tmp/pmp4010-manual-时间-pid.log\n"
        "  --sdo-timeout-ms 500   单次 SDO 超时 1..500 ms\n"
        "  --move-timeout-ms 15000 动作超时\n"
        "  --mock-partial-alarm-timeout 仅mock:后续报警读取超时\n"
        "  --mock-warning-2312     仅 mock: 初始已失能Warning 2312\n"
        "  --mock-trace 路径       仅 mock: 保存原始发送帧\n"
        "  --mock-delay-ms 数值    仅 mock: 延迟应答（离线验证）\n"
        "  --mock-timeout-after N  仅 mock: 第 N 次发送起无应答\n"
        "按键: z软件零点 r/j相对mm p绝对mm s速度rpm a步长mm c换算\n"
        "      f/b单步 e保持使能 d立即失能 Space/x停止 t报警复位 q退出\n"
        "输入后 Enter审阅，再 Enter执行；Esc取消。启动仅读，不自动使能/复位。\n"
        "真机未提供--exclusive-can-ack时按Enter确认独占CAN及独立停机准备。\n"
        "本手动测试不要求填写OD理由；隔离节点不重试。失能不等于机械停稳。\n";
}
}
int main(int argc, char** argv) {
    pmp4010_cpp_test::UiOptions options;
    bool explicit_interface = false;
    try {
        for (int i = 1; i < argc; ++i) {
            const std::string key(argv[i]);
            if (key == "--help" || key == "-h") { Help(); return 0; }
            if (key == "--version") {
                std::cout << "PMP4010 独立测试 1\nservo_sha256=" << PMP4010_SERVO_SHA256
                    << "\nsocketcan_sha256=" << PMP4010_SOCKETCAN_SHA256 << "\nod_sha256=" << PMP4010_OD_SHA256 << '\n';
                return 0;
            }
            if (key == "--exclusive-can-ack") { options.exclusive_can_ack = true; continue; }
            if (key == "--mock-partial-alarm-timeout") { options.mock_partial_alarm_timeout = true; continue; }
            if (key == "--mock-warning-2312") { options.mock_warning_2312 = true; continue; }
            if (key == "--mock") { options.mock = true; continue; }
            if (i + 1 >= argc) throw std::invalid_argument("选项缺少参数: " + key);
            const std::string value(argv[++i]);
            if (key == "--interface") { options.interface = value; explicit_interface = !value.empty(); }
            else if (key == "--bitrate") options.bitrate = Integer(value, 10000000);
            else if (key == "--node") options.settings.node = Integer(value, 127);
            else if (key == "--mm-per-rev" || key == "--mm-per-motor-turn") options.settings.mm_per_motor_turn = Number(value);
            else if (key == "--rpm") options.settings.rpm = Number(value);
            else if (key == "--step-mm") options.settings.step_mm = Number(value);
            else if (key == "--log") options.log_path = value;
            else if (key == "--sdo-timeout-ms") options.settings.sdo_timeout_ms = Integer(value, 500);
            else if (key == "--move-timeout-ms") options.settings.move_timeout_ms = Integer(value, 3600000);
            else if (key == "--mock-trace") options.mock_trace_path = value;
            else if (key == "--mock-delay-ms") options.mock_delay_ms = Integer(value, 60000);
            else if (key == "--mock-timeout-after") options.mock_timeout_after = Integer(value, 100000000);
            else throw std::invalid_argument("未知选项: " + key);
        }
        if (!(options.settings.mm_per_motor_turn > 0 && options.settings.rpm > 0 && options.settings.step_mm > 0))
            throw std::invalid_argument("换算、速度与步长必须大于零");
        if (!options.mock && (!options.mock_trace_path.empty() || options.mock_delay_ms || options.mock_timeout_after || options.mock_warning_2312 || options.mock_partial_alarm_timeout))
            throw std::invalid_argument("mock选项需要 --mock");
        if (!options.mock && !explicit_interface) throw std::invalid_argument("真机必须显式提供 --interface 接口名");
        return pmp4010_cpp_test::RunUi(options);
    } catch (const std::exception& error) {
        std::cerr << "错误: " << error.what() << '\n';
        return 1;
    }
}
