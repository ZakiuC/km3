#pragma once
#include <memory>
#include <string>

namespace nimotion { class CanBus; }
namespace pmp4010_cpp_test {
// Read-only link validation and a lock shared by instances of this tool.
// The lock cannot establish that RT services or other CAN programs are stopped.
std::unique_ptr<nimotion::CanBus> MakeCheckedSocketCan(
    const std::string& interface, unsigned expected_bitrate = 1000000);
}
