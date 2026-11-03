#pragma once
#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>
#include "../../docs/硬件手册/PMP4010/示例/canopen_servo.hpp"

namespace pmp4010_cpp_test {
// All methods, including construction/destruction, belong to ONE worker thread.
// UI owns only copied values and the atomic cancellation flag; no concurrent CAN access.
struct Settings {
    unsigned node = 1;
    double rpm = 1;
    double step_mm = 0.1;
    double mm_per_motor_turn = 4;
    int sdo_timeout_ms = 500;
    int state_timeout_ms = 2000;
    int move_timeout_ms = 15000;
    int poll_interval_ms = 20;
};
enum class Action {
    Absolute, Relative, Forward, Backward, Hold,
    SetZero, SetRpm, SetStep, SetMmPerTurn, ResetAlarm
};
struct Snapshot {
    bool valid = false;
    bool isolated = false;
    bool action_locked = false;
    bool statusword_known = false, controlword_known = false, error_code_known = false;
    bool position_known = false, velocity_known = false;
    bool controlword_attempted = false, controlword_acknowledged = false;
    std::uint16_t attempted_controlword = 0;
    std::uint16_t statusword = 0, controlword = 0, error_code = 0;
    std::uint8_t error_register = 0;
    std::int32_t position_raw = 0, velocity_raw = 0, target_raw = 0, demand_raw = 0;
    double position_mm = 0, motor_rpm = 0;
    std::int32_t software_zero_raw = 0;
    Settings settings;
    std::string state, alarm, message, phase;
};
struct ReviewPlan {
    std::uint64_t token = 0;
    Action action = Action::Hold;
    std::int32_t start_raw = 0, target_raw = 0;
    double requested_mm = 0, actual_mm = 0;
    double requested_rpm = 0, actual_rpm = 0;
    std::uint32_t profile_velocity = 0;
    std::vector<std::string> lines;
};
struct Outcome {
    bool success = false;
    bool isolated = false;
    bool disabled_confirmed = false;
    bool mechanical_stop_confirmed = false; // software never establishes brake/mechanical proof
    std::string message;
    Snapshot snapshot;
};
std::string FaultDescription(std::uint16_t code);
std::string StateDescription(std::uint16_t statusword);
// Merely creates a transport, never changes interface/link/bitrate/NMT configuration.
std::unique_ptr<nimotion::CanBus> MakeSocketCan(const std::string& interface, unsigned expected_bitrate = 1000000);
class Controller {
public:
    // Transport must outlive Controller. Constructor and destructor send ZERO frames.
    Controller(nimotion::CanBus& bus, Settings settings);
    ~Controller();
    Controller(const Controller&) = delete;
    Controller& operator=(const Controller&) = delete;
    // Startup is read-only. An owned active motion proof that fails protection
    // attempts bounded stop/disable, unless communication has been isolated.
    Snapshot Poll();
    ReviewPlan Review(Action action, double value = 0); // uploads only; frozen one-use token
    Outcome Execute(std::uint64_t token, const std::atomic_bool& cancel);
    void DiscardReview(); // no IO
    // Stable UI-owned flag, read before every new transaction. Never interrupts
    // an in-flight SDO (one bounded transaction may finish before cancellation).
    void SetCancellationFlag(const std::atomic_bool* cancel); // worker thread only
    Outcome SafetyStop(bool disable = false); // bounded stop, optionally followed by disable
    Outcome Shutdown(); // explicit bounded cleanup; never retries an isolated client
    bool Isolated() const noexcept;
    Snapshot LastSnapshot() const; // copied last/partial observations; ZERO IO
    // Worker-thread callback: enqueue/copy only; UI performs durable file IO.
    void SetDiagnostics(std::function<void(const std::string&)> observer);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace pmp4010_cpp_test
