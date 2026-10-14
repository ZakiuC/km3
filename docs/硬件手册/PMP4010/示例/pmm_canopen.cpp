// Build on Linux: g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic pmm_canopen.cpp -o pmm_canopen -pthread
#include "socketcan.hpp"
#include <csignal>
#include <iomanip>
#include <iostream>
#include <vector>

static volatile std::sig_atomic_t interrupted = 0;
static void signalHandler(int) { interrupted = 1; }
class Console {
    std::string pending_;
    bool eof_ = false;
public:
    bool line(std::string& out, nimotion::Servo& motor) {
        auto next_check = nimotion::Clock::now();
        while (!interrupted) {
            auto end = pending_.find('\n');
            if (end != std::string::npos) {
                out = pending_.substr(0, end); pending_.erase(0, end + 1); return true;
            }
            if (eof_) {
                if (pending_.empty()) return false;
                out = std::move(pending_); pending_.clear(); return true;
            }
            if (nimotion::Clock::now() >= next_check) {
                motor.pollHealth();
                next_check = nimotion::Clock::now() + std::chrono::milliseconds(200);
            }
            pollfd p{STDIN_FILENO, POLLIN, 0};
            int ready = poll(&p, 1, 100);
            if (ready < 0) {
                if (errno == EINTR) continue;
                throw std::runtime_error("Console poll failed");
            }
            if (p.revents & (POLLIN | POLLHUP)) {
                char data[256]; auto n = ::read(STDIN_FILENO, data, sizeof(data));
                if (n > 0) pending_.append(data, size_t(n));
                else if (n == 0) eof_ = true;
                else if (errno != EINTR) throw std::runtime_error("Console read failed");
                if (pending_.size() > 8192) throw std::runtime_error("Console input line too long");
            }
        }
        return false;
    }
};
static double number(const std::string& text) {
    size_t used = 0; double v = std::stod(text, &used);
    if (used != text.size() || !std::isfinite(v)) throw std::invalid_argument("Invalid number: " + text);
    return v;
}
static void info(nimotion::Servo& m) {
    m.refreshUnits();
    auto& s = m.sdo();
    auto sw = m.statusword();
    std::cout << "status=" << nimotion::hex(sw)
              << " mode=" << int(s.read<int8_t>(0x6061))
              << " fault=" << nimotion::hex(s.read<uint16_t>(0x603f)) << '\n';
    auto pos = m.positionRaw();
    std::cout << std::fixed << std::setprecision(3)
              << "position=" << pos << " UU, output=" << m.units().toDegrees(pos) << " deg\n"
              << "motor velocity=" << m.motorRpm() << " rpm\n"
              << "608F resolution=" << m.units().incrementsPerMotorTurn
              << ", 6091 electronic gear=" << m.units().electronicGear
              << ", mechanical reduction=" << m.units().mechanicalReduction
              << ", UU/output turn=" << m.units().unitsPerOutputTurn() << '\n'
              << "software limits=" << s.read<int32_t>(0x607d, 1) << ".." << s.read<int32_t>(0x607d, 2)
              << " UU, node=" << s.read<uint16_t>(0x200c, 2)
              << ", bitrate code=" << s.read<uint16_t>(0x200c, 3) << '\n';
    // Optional manufacturer diagnostics; do not fail core control if older firmware lacks them.
    try {
        std::cout << "rated speed=" << s.read<uint16_t>(0x2000, 0x0e) << " rpm"
                  << ", motor max parameter=" << s.read<uint16_t>(0x2000, 0x0f) << " rpm"
                  << ", 6080 limit=" << s.read<uint32_t>(0x6080) << " rpm\n"
                  << "directional limits=" << s.read<uint16_t>(0x2006, 0x0a) << "/"
                  << s.read<uint16_t>(0x2006, 0x0b) << " rpm"
                  << ", 607F limit=" << s.read<uint32_t>(0x607f) << " UU/s\n";
        std::cout << "bus voltage=" << s.read<uint16_t>(0x200b, 0x15) * 0.1 << " V"
                  << ", temperature=" << s.read<int16_t>(0x200b, 0x16) << " C"
                  << ", torque=" << s.read<int16_t>(0x6077) * 0.1 << " % rated\n";
    } catch (const nimotion::SdoAbort& e) { std::cout << "Optional diagnostic: " << e.what() << '\n'; }
}
static void help() {
    std::cout << R"(Commands (one command per line):
  status                         read state, position, velocity, faults, units
  pp [accel decel]                configure PP; output deg/s^2 (default 360 360)
  pv [accel decel]                configure PV; output deg/s^2 (default 360 360)
  enable                         enable selected mode (starts with safe target)
  abs DEG SPEED [SECONDS]        absolute OUTPUT angle, OUTPUT speed deg/s
  rel DEG SPEED [SECONDS]        displacement from measured current position
  rpm RPM [SECONDS]              MOTOR shaft rpm; default duration 5 seconds
  halt                           decelerate and hold (drive stays enabled)
  disable                        halt then disable operation (holding torque lost)
  quickstop                      6040=0x0002; recovery: disable, pp/pv, enable
  reset                          explicit fault reset; then pp/pv, enable again
  id NEW_ID                      disabled drive only; SAVE ALL parameters, then exit
                                 power cycle drive to apply new ID (range 1..127)
  help / quit                    quit attempts halt and disable
Start with: status -> pp -> enable -> rel 10 20 -> halt -> disable
No motion is sent when the program starts. Close NiMStudio CAN control first.
)";
}
int main(int argc, char** argv) {
    try {
        if (argc > 4 || (argc > 1 && std::string(argv[1]) == "--help")) {
            std::cout << "Usage: pmm_canopen [can0] [node=1] [mechanical_reduction=1]\n";
            help(); return argc > 4 ? 1 : 0;
        }
        std::string channel = argc > 1 ? argv[1] : "can0";
        double id = argc > 2 ? number(argv[2]) : 1;
        if (id < 1 || id > 127 || std::floor(id) != id) throw std::invalid_argument("Node must be 1..127");
        double reduction = argc > 3 ? number(argv[3]) : 1;
        std::signal(SIGINT, signalHandler); std::signal(SIGTERM, signalHandler);
        nimotion::SocketCan bus(channel);
        nimotion::Servo motor(bus, unsigned(id), reduction, [] { return interrupted != 0; });
        std::cout << "Connected to " << channel << ", node=" << unsigned(id) << '\n';
        help();
        std::string line;
        Console console;
        while (!interrupted && (std::cout << "> " << std::flush) && console.line(line, motor)) {
            std::istringstream in(line);
            std::string command, token;
            in >> command;
            if (command.empty()) continue;
            std::vector<double> args;
            try {
                while (in >> token) args.push_back(number(token));
                auto count = [&](size_t lo, size_t hi) {
                    if (args.size() < lo || args.size() > hi) throw std::invalid_argument("Wrong argument count; use help");
                };
                if (command == "quit" || command == "q") { count(0, 0); break; }
                if (command == "help") { count(0, 0); help(); }
                else if (command == "status") { count(0, 0); info(motor); }
                else if (command == "pp" || command == "pv") {
                    if (!args.empty() && args.size() != 2) throw std::invalid_argument("Use pp/pv or pp/pv ACCEL DECEL");
                    motor.configure(command == "pp" ? 1 : 3, args.empty() ? 360 : args[0], args.empty() ? 360 : args[1]);
                    std::cout << "Configured, disabled. Use enable.\n";
                } else if (command == "enable") { count(0, 0); motor.enable(); }
                else if (command == "abs" || command == "rel") {
                    count(2, 3);
                    double seconds = args.size() == 3 ? args[2] : 30;
                    if (seconds <= 0 || seconds > 86400) throw std::invalid_argument("Timeout must be 0..86400 seconds");
                    int timeout = nimotion::Units::rounded<int>(seconds * 1000);
                    if (command == "abs") motor.moveAbsolute(args[0], args[1], timeout);
                    else motor.moveRelative(args[0], args[1], timeout);
                    std::cout << "Target reached; drive holds position.\n";
                } else if (command == "rpm") {
                    count(1, 2);
                    double seconds = args.size() == 2 ? args[1] : 5;
                    if (seconds <= 0 || seconds > 86400) throw std::invalid_argument("Duration must be 0..86400 seconds");
                    motor.setMotorRpm(args[0]);
                    auto end = nimotion::Clock::now() + std::chrono::milliseconds(nimotion::Units::rounded<int>(seconds * 1000));
                    while (!interrupted && nimotion::Clock::now() < end) {
                        if (motor.statusword() & 8) throw std::runtime_error("Fault during velocity motion");
                        std::cout << "motor=" << motor.motorRpm() << " rpm\n";
                        std::this_thread::sleep_for(std::chrono::milliseconds(100));
                    }
                    if (interrupted) throw std::runtime_error("Interrupted");
                    motor.halt();
                    std::cout << "Velocity run complete; halted.\n";
                } else if (command == "halt") { count(0, 0); motor.halt(); }
                else if (command == "disable") { count(0, 0); motor.disable(); }
                else if (command == "quickstop") { count(0, 0); motor.quickStop(); }
                else if (command == "reset") { count(0, 0); motor.resetFault(); }
                else if (command == "id") {
                    count(1, 1);
                    if (args[0] < 1 || args[0] > 127 || std::floor(args[0]) != args[0])
                        throw std::invalid_argument("Node ID must be an integer in 1..127");
                    motor.changeNodeId(unsigned(args[0]));
                    std::cout << "Parameters saved. Power cycle the drive, then reconnect using node "
                              << unsigned(args[0]) << ". Program exits now.\n";
                    break;
                }
                else throw std::invalid_argument("Unknown command; use help");
            } catch (const std::invalid_argument& e) {
                std::cerr << "Input: " << e.what() << '\n';
            } catch (const std::out_of_range& e) {
                std::cerr << "Range: " << e.what() << '\n';
            } catch (const std::exception& e) {
                std::cerr << "Control: " << e.what() << "\nAttempting quick stop; reconnect program.\n";
                motor.sdo().bestEffortQuickStop();
                return 2;
            }
        }
        motor.shutdownNoThrow(); return interrupted ? 130 : 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
