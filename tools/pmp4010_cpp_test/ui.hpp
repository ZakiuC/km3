#pragma once
#include "controller.hpp"
#include <string>

namespace pmp4010_cpp_test {
struct UiOptions {
    Settings settings;
    std::string interface = "can0";
    std::string log_path;
    std::string mock_trace_path;
    unsigned bitrate = 1000000;
    bool exclusive_can_ack = false;
    bool mock = false;
    bool mock_warning_2312 = false;
    bool mock_partial_alarm_timeout = false;
    int mock_delay_ms = 0;
    unsigned mock_timeout_after = 0;
};
int RunUi(const UiOptions& options);
}
