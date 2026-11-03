#include "ui.hpp"
#include "tests/mock_bus.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <csignal>
#include <deque>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <system_error>
#include <pthread.h>
#include <thread>
#include <vector>
#include <cerrno>
#include <poll.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

namespace pmp4010_cpp_test {
namespace {
using Clock = std::chrono::steady_clock;
volatile sig_atomic_t interrupted = 0;
void Signal(int signal) { interrupted = signal; }
std::string Number(double value) { std::ostringstream s; s << std::setprecision(8) << value; return s.str(); }
std::string Hex(unsigned value, unsigned width = 4) {
    std::ostringstream s; s << "0x" << std::uppercase << std::hex << std::setw(width) << std::setfill('0') << value; return s.str();
}
// Sanitize and clip UTF-8 on complete glyph boundaries. Chinese glyphs occupy two cells.
std::string Fit(const std::string& text, int columns) {
    std::string result;
    for (std::size_t i = 0; i < text.size();) {
        const unsigned char first = text[i];
        unsigned size = first < 0x80 ? 1 : first < 0xe0 ? 2 : first < 0xf0 ? 3 : 4;
        if (i + size > text.size()) break;
        unsigned code = first & (size == 1 ? 0x7f : size == 2 ? 0x1f : size == 3 ? 0xf : 7);
        for (unsigned j = 1; j < size; ++j) code = (code << 6) | (static_cast<unsigned char>(text[i+j]) & 0x3f);
        const int width = code >= 0x1100 && (code <= 0x115f || (code >= 0x2e80 && code <= 0xa4cf) ||
            (code >= 0xac00 && code <= 0xd7a3) || (code >= 0xf900 && code <= 0xfaff) ||
            (code >= 0xfe10 && code <= 0xfe6f) || (code >= 0xff01 && code <= 0xff60) || code >= 0x1f300) ? 2 : 1;
        if (columns < width) break;
        if (code < 32 || (code >= 0x7f && code < 0xa0)) result += '?';
        else result.append(text, i, size);
        columns -= width; i += size;
    }
    return result;
}
std::string Timestamp() {
    const auto current = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm utc{}; gmtime_r(&current, &utc); char buffer[32]{};
    std::strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &utc); return buffer;
}
std::string Diagnostic(const Snapshot& view) {
    return std::string(view.valid ? "完整反馈: " : "不完整反馈: ") + "phase=" + view.phase +
        " 603F=" + (view.error_code_known ? Hex(view.error_code) : "未知") +
        " SW=" + (view.statusword_known ? Hex(view.statusword) : "未知") +
        " CW=" + (view.controlword_known ? Hex(view.controlword) : "未知") +
        " raw=" + (view.position_known ? std::to_string(view.position_raw) : "未知") +
        " velocity_raw=" + (view.velocity_known ? std::to_string(view.velocity_raw) : "未知") +
        " attempted_CW=" + (view.controlword_attempted ? Hex(view.attempted_controlword) : "无") +
        " download_ACK=" + (view.controlword_acknowledged ? "是" : "否") +
        (view.error_code_known ? "；" + FaultDescription(view.error_code) : "");
}
void Backspace(std::string& text) {
    if (text.empty()) return;
    std::size_t i = text.size() - 1;
    while (i && (static_cast<unsigned char>(text[i]) & 0xc0) == 0x80) --i;
    text.erase(i);
}
// File IO never runs on the UI or CAN worker thread. Emergency input only
// contends with a short queue push, independently of filesystem latency.
class PersistentLog {
    std::ofstream stream_;
    std::mutex mutex_;
    std::condition_variable condition_;
    std::deque<std::string> pending_;
    std::thread thread_;
    bool closing_ = false;
    std::atomic_bool failed_{false};
public:
    explicit PersistentLog(const std::string& path) : stream_(path) {
        if (!stream_) throw std::runtime_error("无法打开日志路径");
        thread_ = std::thread([this] {
            for (;;) {
                std::string line;
                {
                    std::unique_lock<std::mutex> lock(mutex_);
                    condition_.wait(lock, [this] { return closing_ || !pending_.empty(); });
                    if (pending_.empty() && closing_) break;
                    line = std::move(pending_.front()); pending_.pop_front();
                }
                stream_ << line << '\n' << std::flush;
                if (!stream_) failed_.store(true);
            }
        });
    }
    ~PersistentLog() { Finish(); }
    void Write(std::string line) {
        std::lock_guard<std::mutex> lock(mutex_); pending_.push_back(std::move(line)); condition_.notify_one();
    }
    bool Failed() const { return failed_.load(); }
    void Finish() {
        if (!thread_.joinable()) return;
        { std::lock_guard<std::mutex> lock(mutex_); closing_ = true; condition_.notify_one(); }
        thread_.join();
    }
};
class Terminal {
    termios saved_{};
    bool active_ = false;
    struct sigaction old_int_{}, old_term_{};
public:
    Terminal() {
        if (!isatty(STDIN_FILENO) || !isatty(STDOUT_FILENO)) throw std::runtime_error("交互界面需要终端；帮助请用 --help");
        if (tcgetattr(STDIN_FILENO, &saved_) != 0) throw std::runtime_error("读取终端设置失败");
        termios raw = saved_;
        raw.c_lflag &= ~(ICANON | ECHO | IEXTEN); // Keep ISIG: Ctrl-C invokes controlled cleanup.
        raw.c_iflag &= ~(IXON | ICRNL);
        raw.c_cc[VMIN] = 0; raw.c_cc[VTIME] = 0;
        if (tcsetattr(STDIN_FILENO, TCSANOW, &raw) != 0) throw std::runtime_error("设置终端失败");
        active_ = true;
        struct sigaction action{}; action.sa_handler = Signal; sigemptyset(&action.sa_mask);
        sigaction(SIGINT, &action, &old_int_); sigaction(SIGTERM, &action, &old_term_);
        std::cout << "\033[?1049h\033[?25l\033[2J" << std::flush;
    }
    ~Terminal() {
        if (active_) {
            tcsetattr(STDIN_FILENO, TCSANOW, &saved_);
            std::cout << "\033[?25h\033[?1049l" << std::flush;
            sigaction(SIGINT, &old_int_, nullptr); sigaction(SIGTERM, &old_term_, nullptr);
        }
    }
};

// A monotonic deadline and ordinary pthread timed-wait semantics in every
// build. GCC 11's shipped TSan does not intercept pthread_cond_clockwait,
// which libstdc++ uses for steady_clock wait_until; it does intercept this
// CLOCK_MONOTONIC condition's pthread_cond_timedwait unlock/relock correctly.
class MonotonicWakeup {
    pthread_cond_t condition_{};
    static void Check(int result) {
        if (result) throw std::system_error(result, std::generic_category(), "初始化单调时钟条件变量");
    }
public:
    MonotonicWakeup() {
        pthread_condattr_t attributes;
        Check(pthread_condattr_init(&attributes));
        try {
            Check(pthread_condattr_setclock(&attributes, CLOCK_MONOTONIC));
            Check(pthread_cond_init(&condition_, &attributes));
        } catch (...) { pthread_condattr_destroy(&attributes); throw; }
        pthread_condattr_destroy(&attributes);
    }
    ~MonotonicWakeup() { pthread_cond_destroy(&condition_); }
    MonotonicWakeup(const MonotonicWakeup&) = delete;
    MonotonicWakeup& operator=(const MonotonicWakeup&) = delete;
    void NotifyOne() noexcept { pthread_cond_signal(&condition_); }
    template<class Predicate>
    void WaitUntil(std::unique_lock<std::mutex>& lock, Clock::time_point deadline, Predicate ready) {
        while (!ready()) {
            const auto remaining = deadline - Clock::now();
            if (remaining <= Clock::duration::zero()) return;
            timespec absolute{};
            if (clock_gettime(CLOCK_MONOTONIC, &absolute) != 0)
                throw std::system_error(errno, std::generic_category(), "读取单调时钟");
            const auto seconds = std::chrono::duration_cast<std::chrono::seconds>(remaining);
            const auto nanos = std::chrono::duration_cast<std::chrono::nanoseconds>(remaining - seconds);
            absolute.tv_sec += seconds.count(); absolute.tv_nsec += nanos.count();
            if (absolute.tv_nsec >= 1000000000L) { ++absolute.tv_sec; absolute.tv_nsec -= 1000000000L; }
            const int result = pthread_cond_timedwait(&condition_, lock.mutex()->native_handle(), &absolute);
            if (result && result != ETIMEDOUT)
                throw std::system_error(result, std::generic_category(), "等待后台命令");
            // Spurious wakes and timeouts recheck the predicate and steady deadline.
        }
    }
};
enum class CommandKind { Review, Execute, Discard, Stop, Disable, Quit };
struct Command { CommandKind kind; Action action = Action::Hold; double value = 0; std::uint64_t token = 0; unsigned epoch = 0; };
struct Event {
    enum class Kind { Snapshot, Review, Result, Diagnostic, Error, Done } kind;
    Snapshot snapshot;
    ReviewPlan review;
    std::string message;
    unsigned epoch = 0;
    bool fatal = false;
    bool success = false, final_cleanup = false;
    Event(Kind value) : kind(value) {}
};
class Worker {
    UiOptions options_;
    std::thread thread_;
    std::mutex mutex_;
    MonotonicWakeup condition_;
    std::deque<Command> commands_;
    std::deque<Event> events_;
    bool quit_ = false;
    void Publish(Event event) { std::lock_guard<std::mutex> lock(mutex_); events_.push_back(std::move(event)); }
    void Result(const Outcome& outcome, unsigned epoch, bool final_cleanup = false) {
        Event event{Event::Kind::Result}; event.snapshot = outcome.snapshot; event.epoch = epoch; event.success = outcome.success; event.final_cleanup = final_cleanup;
        event.message = outcome.message + "；电气失能确认=" + (outcome.disabled_confirmed ? "是" : "否") + "；机械停稳未确认";
        Publish(std::move(event));
    }
    void Run();
public:
    std::atomic_bool cancel{false};
    explicit Worker(UiOptions options) : options_(std::move(options)) { thread_ = std::thread([this] { Run(); }); }
    ~Worker() { if (thread_.joinable()) { Submit({CommandKind::Quit}); thread_.join(); } }
    void Submit(Command command) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (command.kind == CommandKind::Stop || command.kind == CommandKind::Disable || command.kind == CommandKind::Quit) {
            cancel.store(true);
            commands_.clear(); commands_.push_front(command);
        } else {
            if (command.kind == CommandKind::Review || command.kind == CommandKind::Execute) cancel.store(false);
            commands_.push_back(command);
        }
        condition_.NotifyOne();
    }
    std::vector<Event> Drain() {
        std::lock_guard<std::mutex> lock(mutex_);
        std::vector<Event> events;
        while (!events_.empty()) { events.push_back(std::move(events_.front())); events_.pop_front(); }
        return events;
    }
    void Join() { if (thread_.joinable()) thread_.join(); }
};
// This wrapper changes only simulated transport timing. Every request still uses
// the real Controller, SdoClient and MockCanBus byte-level protocol implementation.
class UiMockBus final : public nimotion::CanBus {
public:
    MockCanBus bus;
    explicit UiMockBus(unsigned node) : bus(node) {}
    int delay_ms = 0;
    unsigned timeout_after = 0;
    unsigned sends = 0;
    Clock::time_point ready{};
    void send(const nimotion::Frame& frame) override {
        ++sends; bus.send(frame); ready = Clock::now() + std::chrono::milliseconds(delay_ms);
    }
    bool receive(nimotion::Frame& frame, int timeout_ms) override {
        if (timeout_after && sends >= timeout_after) {
            if (timeout_ms > 0) std::this_thread::sleep_for(std::chrono::milliseconds(std::min(timeout_ms, 5)));
            return false;
        }
        if (Clock::now() < ready) {
            if (timeout_ms <= 0) return false;
            std::this_thread::sleep_until(std::min(ready, Clock::now() + std::chrono::milliseconds(timeout_ms)));
            if (Clock::now() < ready) return false;
        }
        return bus.receive(frame, timeout_ms);
    }
};
void Worker::Run() {
    std::unique_ptr<nimotion::CanBus> bus;
    UiMockBus* mock = nullptr;
    try {
        if (options_.mock) {
            auto created = std::make_unique<UiMockBus>(options_.settings.node); mock = created.get();
            if (options_.mock_warning_2312) {
                mock->bus.SetObject(0x6041, 0, 0xc0, 2);
                mock->bus.SetObject(0x603f, 0, 0x2312, 2);
            }
            if (options_.mock_partial_alarm_timeout) {
                mock->bus.on_send = [status_reads = 0u](const nimotion::Frame& frame, MockCanBus& simulated) mutable {
                    const auto index = nimotion::decode(frame.data.data() + 1, 2);
                    if (frame.data[0] == 0x40 && index == 0x6041 && ++status_reads == 3) {
                        simulated.SetObject(0x603f, 0, 0x2312, 2);
                        simulated.SetObject(0x6041, 0, 0xc0, 2);
                    }
                    if (frame.data[0] == 0x40 && index == 0x607d && frame.data[3] == 1 && status_reads >= 3)
                        simulated.SetTimeoutAfter(simulated.Frames().size() - 1);
                };
            }
            mock->delay_ms = options_.mock_delay_ms; mock->timeout_after = options_.mock_timeout_after;
            bus = std::move(created);
        } else bus = MakeSocketCan(options_.interface, options_.bitrate);
        Controller controller(*bus, options_.settings);
        controller.SetCancellationFlag(&cancel);
        controller.SetDiagnostics([this](const std::string& text) {
            Event event{Event::Kind::Diagnostic}; event.message = text; Publish(std::move(event));
        });
        auto next_poll = Clock::now();
        while (!quit_) {
            Command command{CommandKind::Discard};
            bool have_command = false;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                condition_.WaitUntil(lock, next_poll, [this] { return !commands_.empty(); });
                if (!commands_.empty()) { command = commands_.front(); commands_.pop_front(); have_command = true; }
            }
            try {
                if (have_command) {
                    switch (command.kind) {
                    case CommandKind::Review: {
                        Event event{Event::Kind::Review}; event.review = controller.Review(command.action, command.value); event.snapshot = controller.LastSnapshot(); event.epoch = command.epoch;
                        Publish(std::move(event)); break;
                    }
                    case CommandKind::Execute: Result(controller.Execute(command.token, cancel), command.epoch); break;
                    case CommandKind::Discard: controller.DiscardReview(); break;
                    case CommandKind::Stop: controller.DiscardReview(); Result(controller.SafetyStop(false), command.epoch); break;
                    case CommandKind::Disable: controller.DiscardReview(); Result(controller.SafetyStop(true), command.epoch); break;
                    case CommandKind::Quit: quit_ = true; break;
                    }
                } else if (!controller.Isolated()) {
                    Event event{Event::Kind::Snapshot}; event.snapshot = controller.Poll(); Publish(std::move(event));
                }
            } catch (const std::exception& error) {
                Event event{Event::Kind::Error}; event.epoch = command.epoch; event.message = error.what();
                event.snapshot = controller.LastSnapshot(); event.snapshot.isolated = controller.Isolated(); Publish(std::move(event));
            }
            // Reset a completed operator cancellation only if no newer safety
            // command is queued. UI and worker serialize this with Submit().
            if (have_command && command.kind != CommandKind::Quit) {
                std::lock_guard<std::mutex> lock(mutex_);
                if (commands_.empty()) cancel.store(false);
            }
            // Never poll a quarantined node again; queued local quit remains usable.
            next_poll = Clock::now() + std::chrono::milliseconds(controller.Isolated() ? 60000 : 400);
        }
        Result(controller.Shutdown(), 0, true);
    } catch (const std::exception& error) {
        Event event{Event::Kind::Error}; event.fatal = true; event.message = error.what(); Publish(std::move(event));
    }
    if (mock && !options_.mock_trace_path.empty()) {
        std::ofstream trace(options_.mock_trace_path);
        for (const auto& frame : mock->bus.Frames()) {
            trace << Hex(frame.id, 3) << ' ' << unsigned(frame.size);
            for (unsigned i = 0; i < frame.size; ++i) trace << ' ' << Hex(frame.data[i], 2);
            trace << '\n';
        }
        trace << "downloads=" << mock->bus.downloads() << " sends=" << mock->sends << '\n';
        if (!trace) { Event event{Event::Kind::Error}; event.message = "mock帧日志写入失败"; Publish(std::move(event)); }
    }
    Publish({Event::Kind::Done});
}

std::string Label(Action action) {
    switch (action) {
    case Action::Absolute: return "绝对位置 mm";
    case Action::Relative: return "相对位移 mm";
    case Action::Forward: return "正向单步";
    case Action::Backward: return "反向单步";
    case Action::Hold: return "保持使能";
    case Action::SetZero: return "设置软件零点";
    case Action::SetRpm: return "轨迹速度 rpm";
    case Action::SetStep: return "单步距离 mm";
    case Action::SetMmPerTurn: return "每电机转距离 mm";
    case Action::ResetAlarm: return "手动报警复位";
    }
    return "未知动作";
}
}

int RunUi(const UiOptions& supplied) {
    UiOptions options = supplied;
    if (options.log_path.empty()) options.log_path = "/tmp/pmp4010-manual-" +
        std::to_string(std::chrono::system_clock::now().time_since_epoch().count()) + "-" + std::to_string(getpid()) + ".log";
    PersistentLog log(options.log_path);
    std::vector<std::string> history;
    auto record = [&](const std::string& text, bool display = true) {
        if (text.empty()) return;
        if (display) {
            history.push_back(text);
            if (history.size() > 2000) history.erase(history.begin());
        }
        log.Write(Timestamp() + " " + text);
    };
    interrupted = 0;
    int exit_code = 0;
    {
        Terminal terminal;
        std::unique_ptr<Worker> worker;
        Snapshot snapshot;
        snapshot.settings = options.settings;
        enum class Mode { SessionConfirm, Idle, Edit, Draft, WaitingReview, Confirm, Executing, Exiting };
        Mode mode = !options.mock && !options.exclusive_can_ack ? Mode::SessionConfirm : Mode::Idle;
        std::string input;
        Action action = Action::Hold;
        ReviewPlan review;
        Snapshot review_snapshot;
        unsigned epoch = 0;
        bool done = false;
        bool escaped = false;
        auto start = [&] {
            record("会话开始: " + Timestamp() + " interface=" + options.interface + " node=" + std::to_string(options.settings.node) +
                " bitrate=" + std::to_string(options.bitrate) + " mock=" + (options.mock ? "是" : "否"), false);
            record("初始设置: mm_per_motor_turn=" + Number(options.settings.mm_per_motor_turn) + " rpm=" + Number(options.settings.rpm) + " step_mm=" + Number(options.settings.step_mm), false);
            record("servo_sha256=" PMP4010_SERVO_SHA256, false);
            record("socketcan_sha256=" PMP4010_SOCKETCAN_SHA256, false);
            record("od_sha256=" PMP4010_OD_SHA256, false);
            record("日志路径: " + options.log_path, false);
            record("本手动测试不要求填写OD理由，OD未核对状态未改变", false);
            record("启动仅读；不自动使能、复位或写目标。速度为PP轨迹参数。");
            record("操作者须确认RT及其他CAN控制程序已停止，独立停机措施已就绪。");
            worker = std::make_unique<Worker>(options);
        };
        if (mode != Mode::SessionConfirm) start();
        else record("Enter确认RT及其他CAN控制程序已停、独立停机已就绪；q退出");
        auto safety = [&](CommandKind kind, const std::string& message) {
            ++epoch; input.clear(); review = {}; mode = kind == CommandKind::Quit ? Mode::Exiting : Mode::Executing;
            if (worker) worker->cancel.store(true);
            if (worker) worker->Submit({kind, Action::Hold, 0, 0, epoch});
            else if (kind == CommandKind::Quit) done = true;
            else mode = Mode::SessionConfirm;
            record(message);
        };
        auto events = [&] {
            if (!worker) return;
            for (auto& event : worker->Drain()) {
                if (event.kind == Event::Kind::Snapshot) {
                    if (!snapshot.valid || snapshot.error_code != event.snapshot.error_code ||
                        snapshot.statusword != event.snapshot.statusword || snapshot.position_raw != event.snapshot.position_raw) {
                        record(Diagnostic(event.snapshot), false);
                    }
                    snapshot = event.snapshot;
                }
                else if (event.kind == Event::Kind::Review && event.epoch == epoch && mode == Mode::WaitingReview) {
                    review = std::move(event.review); review_snapshot = event.snapshot; mode = Mode::Confirm;
                    record("审阅就绪: " + Label(action) + "；等待第二次Enter，尚未执行");
                    for (const auto& line : review.lines) record("审阅: " + line, false);
                } else if (event.kind == Event::Kind::Result) {
                    snapshot = event.snapshot;
                    record("结果: " + event.message);
                    record(Diagnostic(snapshot), false);
                    if (event.final_cleanup && !event.success) exit_code = exit_code ? exit_code : 1;
                    if (mode == Mode::Executing && event.epoch == epoch) mode = Mode::Idle;
                } else if (event.kind == Event::Kind::Diagnostic) {
                    record("诊断: " + event.message, false);
                } else if (event.kind == Event::Kind::Error) {
                    snapshot = event.snapshot;
                    record("错误: " + event.message);
                    record(Diagnostic(snapshot), false);
                    if (event.fatal) exit_code = 1;
                    if (event.snapshot.isolated) { snapshot.isolated = true; record("节点已隔离；停止后台轮询，不能在本会话重试。"); }
                    if (mode != Mode::Exiting && event.epoch == epoch) mode = Mode::Idle;
                } else if (event.kind == Event::Kind::Done) done = true;
            }
        };
        while (!done) {
            events();
            if (done) break;
            if (log.Failed() && mode != Mode::Exiting) {
                exit_code = exit_code ? exit_code : 1;
                safety(CommandKind::Quit, "操作日志写入失败；已取消未确认动作，受控退出（非通信隔离）");
            }
            if (interrupted && mode != Mode::Exiting) {
                exit_code = 128 + interrupted;
                safety(CommandKind::Quit, "收到信号；退出并受控清理。");
            }
            winsize size{}; ioctl(STDOUT_FILENO, TIOCGWINSZ, &size);
            int columns = size.ws_col ? size.ws_col : 80, rows = size.ws_row ? size.ws_row : 24;
            std::vector<std::string> lines;
            lines.push_back("PMP4010 手动直线测试 | " + std::string(options.mock ? "本机MOCK" : options.interface) + " | 节点 " + std::to_string(options.settings.node));
            lines.push_back("位置 " + (snapshot.valid ? Number(snapshot.position_mm) : "--") + " mm   raw " + (snapshot.position_known ? std::to_string(snapshot.position_raw) : "未知") + "   软件零点 " + std::to_string(snapshot.software_zero_raw));
            lines.push_back("速度 " + (snapshot.valid ? Number(snapshot.motor_rpm) : "--") + " rpm   状态 " + (snapshot.statusword_known ? snapshot.state : "未知") + " " + (snapshot.statusword_known ? Hex(snapshot.statusword) : "--"));
            lines.push_back("603F " + (snapshot.error_code_known ? Hex(snapshot.error_code) + " " + FaultDescription(snapshot.error_code) : "未知") + (snapshot.valid ? "" : " [样本不完整/已失效]"));
            lines.push_back("轨迹 " + Number(snapshot.settings.rpm) + " rpm | 步长 " + Number(snapshot.settings.step_mm) + " mm | " + Number(snapshot.settings.mm_per_motor_turn) + " mm/转");
            lines.push_back(snapshot.isolated ? "【隔离】不再轮询/重试；请退出排查，软件不能证明机械停稳" : "使用前确认RT及其他CAN控制程序已停；独立停机已就绪");
            lines.push_back("z 零点  r/j 相对  p 绝对  s 速度  a 步长  c mm/转");
            lines.push_back("f/b 单步  e 保持使能  d 立即失能  Space/x 停止  t 复位");
            lines.push_back("q 退出 | Esc 取消输入/审阅 | 输入Enter审阅，再Enter执行");
            lines.push_back("日志 " + options.log_path);
            if (mode == Mode::SessionConfirm) {
                lines.push_back("Enter确认RT及其他CAN控制程序已停、独立停机已就绪；q退出");
            } else if (mode == Mode::Edit) {
                lines.push_back(Label(action) + ": 输入有限数值，Enter审阅，Esc取消");
                lines.push_back("> " + input);
            } else if (mode == Mode::Draft) lines.push_back(Label(action) + "：第一次Enter审阅；第二次Enter执行");
            else if (mode == Mode::WaitingReview) lines.push_back("正在读取并审阅；尚未执行。Space/x停止，d失能，q退出");
            else if (mode == Mode::Confirm) {
                lines.push_back("【待执行】" + Label(action) + "；再次Enter执行，Esc取消");
                const bool motion = action == Action::Absolute || action == Action::Relative ||
                    action == Action::Forward || action == Action::Backward || action == Action::Hold;
                if (motion) {
                    const double target_mm = action == Action::Absolute ? review.actual_mm : review_snapshot.position_mm + review.actual_mm;
                    lines.push_back("冻结起点 " + Number(review_snapshot.position_mm) + " mm → 目标 " + Number(target_mm) + " mm");
                    lines.push_back("raw " + std::to_string(review.start_raw) + " → " + std::to_string(review.target_raw));
                    lines.push_back("轨迹速度：请求 " + Number(review.requested_rpm) + " → 实际 " + Number(review.actual_rpm) + " rpm");
                    lines.push_back("换算 " + Number(review_snapshot.settings.mm_per_motor_turn) + " mm/电机转");
                    lines.push_back("目标须位于设备行程内；独立停机措施保持就绪");
                } else if (action == Action::SetRpm) {
                    lines.push_back("轨迹速度：请求 " + Number(review.requested_rpm) + " → 实际 " + Number(review.actual_rpm) + " rpm");
                } else if (action == Action::SetStep) lines.push_back("设置单步距离 " + input + " mm");
                else if (action == Action::SetMmPerTurn) lines.push_back("设置换算 " + input + " mm/电机转");
                else if (action == Action::SetZero) lines.push_back("执行后当前位置显示为 0 mm；仅设置软件坐标");
                else if (action == Action::ResetAlarm) lines.push_back("只复位当前报警；不会使能或重放动作");
            } else if (mode == Mode::Executing) lines.push_back("后台执行中；Space/x停止，d失能，q退出立即响应");
            else if (mode == Mode::Exiting) lines.push_back("退出清理中；等待后台完成并恢复终端");
            else lines.push_back("就绪：选择一个动作。启动及未确认的审阅仅进行读取。");
            const int log_start = std::max<int>(0, static_cast<int>(history.size()) - std::max(1, rows - static_cast<int>(lines.size()) - 2));
            lines.push_back("--- 消息 ---");
            for (std::size_t i = log_start; i < history.size() && static_cast<int>(lines.size()) < rows - 1; ++i) lines.push_back(history[i]);
            std::ostringstream screen; screen << "\033[H";
            for (int row = 0; row < rows; ++row) {
                screen << "\033[2K";
                if (row < static_cast<int>(lines.size())) screen << Fit(lines[row], std::max(1, columns - 1));
                if (row + 1 < rows) screen << "\r\n";
            }
            std::cout << screen.str() << std::flush;
            pollfd descriptor{STDIN_FILENO, POLLIN, 0};
            const int ready = poll(&descriptor, 1, 40);
            if (ready < 0) { if (errno != EINTR) safety(CommandKind::Quit, "终端读取失败，退出清理"); continue; }
            if (ready == 0) continue;
            if (descriptor.revents & (POLLHUP | POLLERR | POLLNVAL)) { safety(CommandKind::Quit, "终端EOF，退出清理"); continue; }
            char buffer[128]; const ssize_t count = read(STDIN_FILENO, buffer, sizeof(buffer));
            if (count == 0) { safety(CommandKind::Quit, "终端EOF，退出清理"); continue; }
            if (count < 0) continue;
            for (ssize_t i = 0; i < count; ++i) {
                const unsigned char key = buffer[i];
                if (mode == Mode::Exiting) continue;
                if (key == 3 || key == 4) { if (key == 3) exit_code = 130; safety(CommandKind::Quit, "收到中断/EOF，退出清理"); continue; }
                if (key == 'q') { safety(CommandKind::Quit, "退出请求：取消当前动作，执行受控清理"); continue; }
                if (key == 'x' || key == ' ') {
                    if (mode == Mode::SessionConfirm) continue;
                    safety(CommandKind::Stop, "停止请求已接收；等待后台停止结果"); continue;
                }
                if (key == 'd' && mode != Mode::SessionConfirm) { safety(CommandKind::Disable, "立即失能请求已接收；等待电气状态确认"); continue; }
                if (key == 27) {
                    if (mode == Mode::Executing) { record("后台执行中；请用Space/x停止或d失能"); continue; }
                    escaped = true; ++epoch; input.clear(); review = {};
                    if (worker) worker->Submit({CommandKind::Discard});
                    if (mode != Mode::SessionConfirm && mode != Mode::Executing) mode = Mode::Idle;
                    record("已取消输入/审阅；未提交执行"); continue;
                }
                if (escaped && key == '[') continue;
                if (escaped && ((key >= 'A' && key <= 'D') || key == '~')) { escaped = false; continue; }
                escaped = false;
                if (key == '\r' || key == '\n') {
                    if (mode == Mode::SessionConfirm) {
                        options.exclusive_can_ack = true; mode = Mode::Idle; start();
                    } else if (mode == Mode::Edit || mode == Mode::Draft) {
                        double value = 0;
                        try {
                            if (mode == Mode::Edit) {
                                std::size_t used = 0; value = std::stod(input, &used);
                                if (used != input.size() || !std::isfinite(value)) throw std::invalid_argument("bad number");
                            }
                        } catch (...) { record("输入无效：请输入有限数值"); continue; }
                        ++epoch; worker->Submit({CommandKind::Review, action, value, 0, epoch}); mode = Mode::WaitingReview;
                    } else if (mode == Mode::Confirm) {
                        worker->Submit({CommandKind::Execute, action, 0, review.token, epoch});
                        record("第二次Enter已确认执行: " + Label(action)); mode = Mode::Executing;
                    }
                    continue;
                }
                if (mode == Mode::Edit) {
                    if (key == 127 || key == 8) Backspace(input);
                    else if (key >= 32 && input.size() < 512) input += static_cast<char>(key);
                    continue;
                }
                if (mode != Mode::Idle || !worker) continue;
                bool numeric = false, known = true;
                switch (key) {
                case 'r': case 'j': action = Action::Relative; numeric = true; break;
                case 'p': action = Action::Absolute; numeric = true; break;
                case 's': action = Action::SetRpm; numeric = true; break;
                case 'a': action = Action::SetStep; numeric = true; break;
                case 'c': action = Action::SetMmPerTurn; numeric = true; break;
                case 'z': action = Action::SetZero; break;
                case 'f': action = Action::Forward; break;
                case 'b': action = Action::Backward; break;
                case 'e': action = Action::Hold; break;
                case 't': action = Action::ResetAlarm; break;
                default: known = false;
                }
                if (known) { input.clear(); mode = numeric ? Mode::Edit : Mode::Draft; review = {}; }
            }
        }
        if (worker) { worker->Join(); events(); }
    }
    log.Finish();
    if (log.Failed()) { exit_code = exit_code ? exit_code : 1; std::cerr << "日志写入失败: " << options.log_path << '\n'; }
    for (const auto& line : history) std::cout << line << '\n';
    std::cout << "终端已恢复；机械停稳未由软件确认。\n" << std::flush;
    return exit_code;
}
}
