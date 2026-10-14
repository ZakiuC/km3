#pragma once
// NiMotion CANopen / CiA402. C++17. One SDO client per node; not thread-safe.
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <functional>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>
#include <utility>

namespace nimotion {
using Clock = std::chrono::steady_clock;
struct Frame {
    uint32_t id = 0;
    uint8_t size = 0;
    std::array<uint8_t, 8> data{};
};
class CanBus {
public:
    virtual ~CanBus() = default;
    virtual void send(const Frame&) = 0;
    virtual bool receive(Frame&, int timeout_ms) = 0;
};
inline std::string hex(uint32_t value) {
    std::ostringstream out;
    out << "0x" << std::hex << value;
    return out.str();
}
inline uint32_t decode(const uint8_t* p, unsigned n) {
    uint32_t v = 0;
    for (unsigned i = 0; i < n; ++i) v |= uint32_t(p[i]) << (8 * i);
    return v;
}
inline void encode(uint8_t* p, uint32_t value, unsigned n) {
    for (unsigned i = 0; i < n; ++i) p[i] = uint8_t(value >> (8 * i));
}
class SdoAbort : public std::runtime_error {
public:
    uint32_t code;
    SdoAbort(uint16_t index, uint8_t sub, uint32_t c)
        : std::runtime_error("SDO abort " + hex(index) + ":" + hex(sub) +
                             " code=" + hex(c)), code(c) {}
};
class SdoClient {
    CanBus& bus_;
    uint8_t node_;
    int timeout_;
    bool poisoned_ = false;
    void observe(const Frame& f) {
        if (f.id == uint32_t(0x80 + node_) && f.size == 8 && decode(f.data.data(), 2)) {
            poisoned_ = true;
            throw std::runtime_error("EMCY error=" + hex(decode(f.data.data(), 2)) +
                " register=" + hex(f.data[2]) + "; read 0x603F / inspect drive alarm");
        }
        if (f.id == uint32_t(0x700 + node_) && f.size == 1 && f.data[0] == 0) {
            poisoned_ = true;
            throw std::runtime_error("Drive rebooted; reconnect and reconfigure");
        }
    }
    Frame transact(Frame request, uint16_t index, uint8_t sub) {
        if (poisoned_) throw std::runtime_error("SDO channel invalid after timeout/reboot; reconnect");
        // Discard queued SDO replies; delayed replies after timeout cannot be safely reused.
        Frame f;
        const auto drain_end = Clock::now() + std::chrono::milliseconds(timeout_);
        while (bus_.receive(f, 0)) {
            observe(f);
            if (Clock::now() >= drain_end) throw std::runtime_error("RX queue cannot be drained");
        }
        bus_.send(request);
        auto end = Clock::now() + std::chrono::milliseconds(timeout_);
        while (Clock::now() < end) {
            int ms = int(std::chrono::duration_cast<std::chrono::milliseconds>(end - Clock::now()).count());
            if (!bus_.receive(f, ms > 0 ? ms : 1)) continue;
            observe(f);
            if (f.id != uint32_t(0x580 + node_) || f.size != 8 ||
                decode(f.data.data() + 1, 2) != index || f.data[3] != sub) continue;
            if (f.data[0] == 0x80) throw SdoAbort(index, sub, decode(f.data.data() + 4, 4));
            return f;
        }
        poisoned_ = true;
        throw std::runtime_error("SDO timeout at " + hex(index) + ":" + hex(sub) +
                                "; verify node, bitrate and wiring; reconnect before retry");
    }
public:
    SdoClient(CanBus& bus, unsigned node = 1, int timeout_ms = 500)
        : bus_(bus), node_(uint8_t(node)), timeout_(timeout_ms) {
        if (node < 1 || node > 127 || timeout_ms <= 0) throw std::invalid_argument("Invalid node/timeout");
    }
    void nmt(uint8_t command) {
        Frame f; f.size = 2; f.data[0] = command; f.data[1] = node_; bus_.send(f);
    }
    // This bypasses SDO reply waiting so a stop can still be attempted after a timeout.
    // Delivery and stopping are NOT guaranteed if CAN communication has failed.
    void bestEffortQuickStop() noexcept {
        try {
            Frame f; f.id = 0x600 + node_; f.size = 8;
            f.data = {0x2b, 0x40, 0x60, 0, 2, 0, 0, 0}; bus_.send(f);
        } catch (...) {}
    }
    template<class T> T read(uint16_t index, uint8_t sub = 0) {
        static_assert(std::is_integral_v<T> && sizeof(T) <= 4 && !std::is_same_v<T, bool>);
        Frame q; q.id = 0x600 + node_; q.size = 8; q.data[0] = 0x40;
        encode(q.data.data() + 1, index, 2); q.data[3] = sub;
        auto r = transact(q, index, sub);
        const auto command = r.data[0];
        if ((command & 0xf3) != 0x43) throw std::runtime_error("Only expedited SDO uploads supported");
        unsigned size = 4 - ((command >> 2) & 3);
        if (size != sizeof(T)) throw std::runtime_error("Object width mismatch at " + hex(index));
        using U = std::make_unsigned_t<T>;
        U u = static_cast<U>(decode(r.data.data() + 4, size));
        T result;
        std::memcpy(&result, &u, sizeof(T));
        return result;
    }
    template<class T> void write(uint16_t index, uint8_t sub, T value) {
        static_assert(std::is_integral_v<T> && sizeof(T) <= 4 && !std::is_same_v<T, bool>);
        Frame q; q.id = 0x600 + node_; q.size = 8;
        q.data[0] = uint8_t(0x23 | ((4 - sizeof(T)) << 2));
        encode(q.data.data() + 1, index, 2); q.data[3] = sub;
        encode(q.data.data() + 4, static_cast<uint32_t>(value), sizeof(T));
        if (transact(q, index, sub).data[0] != 0x60) throw std::runtime_error("Unexpected SDO download response");
    }
};
struct Units {
    double incrementsPerMotorTurn = 0;
    double electronicGear = 0;
    double mechanicalReduction = 1; // motor turns / output turns; separate from 6091.
    double unitsPerOutputTurn() const {
        return incrementsPerMotorTurn * mechanicalReduction / electronicGear;
    }
    double toDegrees(int32_t uu) const { return double(uu) * 360.0 / unitsPerOutputTurn(); }
    int32_t position(double degrees) const {
        return rounded<int32_t>(degrees * unitsPerOutputTurn() / 360.0);
    }
    uint32_t speed(double degrees_per_second) const {
        if (!(degrees_per_second > 0)) throw std::invalid_argument("Speed/acceleration must be positive");
        auto n = rounded<uint32_t>(degrees_per_second * unitsPerOutputTurn() / 360.0);
        if (!n) throw std::invalid_argument("Value rounds to zero user units");
        return n;
    }
    int32_t motorRpm(double rpm) const {
        return rounded<int32_t>(rpm * incrementsPerMotorTurn / (60.0 * electronicGear));
    }
    double feedbackRpm(int32_t raw, uint16_t selector) const {
        if (selector == 0) return raw;
        if (selector == 1) return double(raw) * electronicGear * 60.0 / incrementsPerMotorTurn;
        throw std::runtime_error("Unknown 2006:12 velocity feedback selector");
    }
    template<class T> static T rounded(double v) {
        if (!std::isfinite(v)) throw std::invalid_argument("Non-finite value");
        double r = std::round(v);
        if (r < double(std::numeric_limits<T>::lowest()) || r > double(std::numeric_limits<T>::max()))
            throw std::out_of_range("Value exceeds CANopen object range");
        return static_cast<T>(r);
    }
};
class Servo {
    SdoClient sdo_;
    Units units_;
    int mode_ = 0;
    bool configured_ = false;
    bool touched_ = false;
    bool stopping_ = false;
    uint16_t watchdogMs_ = 0;
    Clock::time_point nextWatchdogFeed_{};
    std::function<bool()> cancelled_;
    void feedWatchdog() {
        if (!watchdogMs_ || Clock::now() < nextWatchdogFeed_) return;
        // Manual 7.10: watchdog requires rewriting 200C:1B; SDO reads are not feeds.
        sdo_.write<uint16_t>(0x200c, 0x1b, watchdogMs_);
        nextWatchdogFeed_ = Clock::now() + std::chrono::milliseconds(watchdogMs_ / 3);
    }
    void readWatchdog() {
        watchdogMs_ = 0;
        try {
            uint16_t ms = sdo_.read<uint16_t>(0x200c, 0x1b);
            if (ms && ms < 1000)
                throw std::runtime_error("This SDO demo requires an enabled watchdog timeout >=1000 ms");
            watchdogMs_ = ms;
        } catch (const SdoAbort& e) {
            if (e.code != 0x06020000 && e.code != 0x06090011) throw;
            // Older firmware without this object.
        }
        nextWatchdogFeed_ = Clock::time_point{};
        feedWatchdog();
    }
    void checkCancel() {
        if (!stopping_ && cancelled_ && cancelled_()) throw std::runtime_error("Interrupted");
    }
    void control(uint16_t value) { touched_ = true; sdo_.write<uint16_t>(0x6040, 0, value); }
    void healthy(uint16_t s) {
        if (s & 8) throw std::runtime_error("Drive fault; use status, then explicit reset");
    }
    template<class Pred> void waitFor(Pred pred, int timeout_ms, const std::string& description) {
        const auto end = Clock::now() + std::chrono::milliseconds(timeout_ms);
        do {
            checkCancel();
            uint16_t s = statusword(); healthy(s);
            if (pred(s)) return;
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        } while (Clock::now() < end);
        throw std::runtime_error("Timeout: " + description);
    }
    void state(uint16_t expected) {
        waitFor([expected](uint16_t s) { return (s & 0x006f) == expected; }, 2000, "CiA402 state " + hex(expected));
    }
    void requireMode(int mode, bool enabled = true) {
        checkCancel();
        if (!configured_ || mode_ != mode || sdo_.read<int8_t>(0x6061) != mode)
            throw std::runtime_error("Configure expected PP/PV mode first");
        auto s = statusword(); healthy(s);
        if (enabled && (s & 0x006f) != 0x0027) throw std::runtime_error("Enable drive first");
    }
    void loadUnits() {
        auto e = sdo_.read<uint32_t>(0x608f, 1), t = sdo_.read<uint32_t>(0x608f, 2);
        auto n = sdo_.read<uint32_t>(0x6091, 1), d = sdo_.read<uint32_t>(0x6091, 2);
        if (!e || !t || !n || !d) throw std::runtime_error("Zero encoder/gear conversion parameter");
        units_.incrementsPerMotorTurn = double(e) / t;
        units_.electronicGear = double(n) / d;
    }
    void validatePosition(int32_t target) {
        int32_t lo = sdo_.read<int32_t>(0x607d, 1), hi = sdo_.read<int32_t>(0x607d, 2);
        if (lo > hi || target < lo || target > hi) throw std::out_of_range("Target exceeds existing 607D software limits");
        // Manual: position after electronic gearing must also fit int32.
        Units::rounded<int32_t>(double(target) * units_.electronicGear);
    }
    void moveRaw(int32_t target, uint32_t speed, int timeout_ms) {
        requireMode(1);
        if (timeout_ms <= 0) throw std::invalid_argument("Movement timeout must be positive");
        validatePosition(target);
        if (speed > sdo_.read<uint32_t>(0x607f)) throw std::out_of_range("Profile speed exceeds 607F");
        control(0x002f); // bit4 low, bit5 immediate, bit6 absolute, bit8 no halt.
        waitFor([](uint16_t s) { return !(s & 0x1000); }, 2000, "set-point ACK low");
        sdo_.write<uint32_t>(0x6081, 0, speed);
        sdo_.write<int32_t>(0x607a, 0, target);
        control(0x003f); // New set point rising edge.
        waitFor([](uint16_t s) { return (s & 0x1000) != 0; }, 2000, "set-point ACK high");
        control(0x002f);
        waitFor([](uint16_t s) { return !(s & 0x1000); }, 2000, "set-point ACK cleared");
        // Bit10 can be stale; require actual position inside drive's arrival window too.
        uint32_t window = sdo_.read<uint32_t>(0x6067);
        waitFor([&](uint16_t s) {
            if (s & 0x2000) throw std::runtime_error("PP following error");
            int64_t delta = int64_t(sdo_.read<int32_t>(0x6064)) - target;
            return (s & 0x0400) && std::abs(delta) <= int64_t(window);
        }, timeout_ms, "position reached");
    }
public:
    Servo(CanBus& bus, unsigned node = 1, double reduction = 1,
          std::function<bool()> cancelled = {})
        : sdo_(bus, node), cancelled_(std::move(cancelled)) {
        if (!std::isfinite(reduction) || reduction <= 0) throw std::invalid_argument("Invalid mechanical reduction");
        units_.mechanicalReduction = reduction;
    }
    Servo(const Servo&) = delete;
    Servo& operator=(const Servo&) = delete;
    ~Servo() { shutdownNoThrow(); }
    uint16_t statusword() { feedWatchdog(); return sdo_.read<uint16_t>(0x6041); }
    int32_t positionRaw() { return sdo_.read<int32_t>(0x6064); }
    const Units& units() const { return units_; }
    SdoClient& sdo() { return sdo_; } // Expert use; no concurrent SDO calls.
    void refreshUnits() { loadUnits(); }
    void pollHealth() {
        checkCancel();
        if (configured_ && touched_) healthy(statusword());
    }
    double motorRpm() {
        loadUnits();
        return units_.feedbackRpm(sdo_.read<int32_t>(0x606c), sdo_.read<uint16_t>(0x2006, 0x12));
    }
    void configure(int mode, double acceleration_deg_s2 = 360, double deceleration_deg_s2 = 360) {
        checkCancel();
        if (mode != 1 && mode != 3) throw std::invalid_argument("Only PP=1 and PV=3 implemented");
        // Wake a stopped NMT node; never broadcast or reset drive configuration.
        sdo_.nmt(0x80);
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        auto s = statusword(); healthy(s);
        if ((s & 0x006f) == 0x0027 || (s & 0x006f) == 0x0007)
            throw std::runtime_error("Drive running/quick-stop active; stop and disable before configuring");
        configured_ = false;
        readWatchdog();
        control(0x0006); state(0x0021);
        control(0x0007); state(0x0023);
        sdo_.write<uint16_t>(0x2002, 1, 0); // Dictionary + software DB: uint16 (manual prose has uint8 typo).
        sdo_.write<int8_t>(0x6060, 0, int8_t(mode));
        loadUnits();
        auto a = units_.speed(acceleration_deg_s2), d = units_.speed(deceleration_deg_s2);
        if (a > sdo_.read<uint32_t>(0x60c5) || d > sdo_.read<uint32_t>(0x60c6))
            throw std::out_of_range("Acceleration/deceleration exceeds 60C5/60C6");
        sdo_.write<uint32_t>(0x6083, 0, a); sdo_.write<uint32_t>(0x6084, 0, d);
        // Deterministic stop behavior from manual, applies to this session; no flash save.
        sdo_.write<int16_t>(0x605d, 0, 1); // Halt: 6084 deceleration and hold.
        sdo_.write<int16_t>(0x605a, 0, 2); // Quick stop: 6085, then free running.
        if (mode == 1) {
            sdo_.write<int32_t>(0x607a, 0, positionRaw());
            sdo_.write<uint32_t>(0x6082, 0, 0); // Stop at destination.
        } else sdo_.write<int32_t>(0x60ff, 0, 0); // Clear stale velocity BEFORE enabling.
        sdo_.nmt(1);
        mode_ = mode;
        configured_ = true;
        requireMode(mode, false);
    }
    void enable() {
        requireMode(mode_, false);
        if ((statusword() & 0x006f) == 0x0027) return;
        // Seed fresh safe target even when returning from halt/disable.
        if (mode_ == 1) sdo_.write<int32_t>(0x607a, 0, positionRaw());
        else sdo_.write<int32_t>(0x60ff, 0, 0);
        control(6); state(0x21); control(7); state(0x23); control(15); state(0x27);
    }
    void moveAbsolute(double output_degrees, double output_deg_s, int timeout_ms = 30000) {
        moveRaw(units_.position(output_degrees), units_.speed(output_deg_s), timeout_ms);
    }
    void moveRelative(double output_degrees, double output_deg_s, int timeout_ms = 30000) {
        // Use actual 6064 + delta and an ABS command, independent of existing 60F2.
        requireMode(1);
        int64_t target = int64_t(positionRaw()) + units_.position(output_degrees);
        if (target < INT32_MIN || target > INT32_MAX) throw std::out_of_range("Relative target overflow");
        moveRaw(int32_t(target), units_.speed(output_deg_s), timeout_ms);
    }
    void setMotorRpm(double rpm) {
        requireMode(3);
        auto raw = units_.motorRpm(rpm);
        if (std::abs(int64_t(raw)) > int64_t(sdo_.read<uint32_t>(0x607f)))
            throw std::out_of_range("Velocity exceeds 607F");
        // Write target first, then clear halt; never resume an old nonzero target.
        sdo_.write<int32_t>(0x60ff, 0, raw); control(0x000f);
    }
    void halt(int timeout_ms = 5000) {
        auto s = statusword(); healthy(s);
        if ((s & 0x006f) != 0x0027) throw std::runtime_error("Halt requires operation enabled");
        control(0x010f);
        const auto end = Clock::now() + std::chrono::milliseconds(timeout_ms);
        do {
            checkCancel(); healthy(statusword());
            if (std::abs(motorRpm()) <= 1.0) {
                if (mode_ == 3) sdo_.write<int32_t>(0x60ff, 0, 0);
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        } while (Clock::now() < end);
        throw std::runtime_error("Timeout: halt/zero speed");
    }
    void disable() {
        auto s = statusword(); healthy(s);
        if ((s & 0x006f) == 0x0027) halt();
        s = statusword();
        if ((s & 0x004f) == 0x0040) { control(6); state(0x0021); }
        control(0x0007); state(0x0023);
    }
    void quickStop() { control(0x0002); }
    void changeNodeId(unsigned new_id) {
        if (new_id < 1 || new_id > 127) throw std::invalid_argument("CANopen node ID must be 1..127");
        checkCancel();
        auto sw = statusword(); healthy(sw);
        auto state_value = sw & 0x006f;
        if (state_value != 0x0040 && state_value != 0x0021 && state_value != 0x0023)
            throw std::runtime_error("Disable motor before changing node ID/saving parameters");
        readWatchdog();
        sdo_.write<uint16_t>(0x200c, 2, uint16_t(new_id));
        // Explicit persistent operation: stores ALL current parameters, not only the ID.
        sdo_.write<uint32_t>(0x1010, 1, 0x65766173);
        const auto end = Clock::now() + std::chrono::milliseconds(3000);
        while (true) {
            checkCancel(); feedWatchdog();
            if (sdo_.read<uint32_t>(0x1010, 1) == 1) break;
            if (Clock::now() >= end) throw std::runtime_error("Parameter save not confirmed");
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        // 200C:02 takes effect after power cycling. Keep this SDO client on the OLD ID.
    }
    void resetFault() {
        auto s = statusword();
        if (!(s & 8)) return;
        control(0); control(0x80);
        // The fault bit may remain high briefly after reset; ordinary waitFor rejects faults.
        const auto end = Clock::now() + std::chrono::milliseconds(2000);
        while (statusword() & 8) {
            checkCancel();
            if (Clock::now() >= end) throw std::runtime_error("Fault reset timed out; fix alarm cause first");
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        control(0); configured_ = false;
    }
    void shutdownNoThrow() noexcept {
        if (!touched_) return; // Read-only session does not control drive on exit.
        stopping_ = true;
        try {
            disable();
            touched_ = false;
        } catch (...) {
            sdo_.bestEffortQuickStop();
            touched_ = false;
        }
    }
};
} // namespace nimotion
