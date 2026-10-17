#pragma once
// CANopen SYNC is a standard-layer responsibility, independent of node drivers.
#include <atomic>
#include <cstdint>
#include <pthread.h>

#include "km3/canopen_wire_generated.hpp"
#include "km3/clock.hpp"
#include "km3/hal/can_device.hpp"

namespace km3::canopen {

static_assert(std::atomic<std::uint64_t>::is_always_lock_free,
              "SYNC telemetry must not introduce hidden locks");

struct SyncStats {
    std::uint64_t attempts = 0;
    std::uint64_t sent = 0;
    std::uint64_t failures = 0;
    std::uint64_t consecutive_failures = 0;
    std::uint64_t skipped = 0;
    std::uint64_t max_late_us = 0;
    ErrorCode last_error = ErrorCode::Ok;
};

// Allocation-free single-owner scheduler, usable with an injected clock in tests.
// This is deliberately NOT called by the RT control loop (development rules §1.4).
class SyncCycle {
public:
    virtual ~SyncCycle() = default;
    virtual Status ValidateForSync(std::uint32_t cob_id) const noexcept = 0;
    virtual Status BeforeSync(std::uint64_t deadline_us) noexcept = 0;
    virtual void LatchFailure() noexcept = 0;
    virtual void ReceiveAvailable() noexcept = 0;
};

class SyncProducer {
public:
    using ClockFn = std::uint64_t (*)() noexcept;
    SyncProducer(ICanDevice& device, std::uint64_t period_us, ClockFn clock = &MonotonicUs,
                 std::uint32_t cob_id = wire::COB_SYNC, SyncCycle* cycle = nullptr) noexcept;
    static CanFrame MakeFrame(std::uint32_t cob_id = wire::COB_SYNC) noexcept;
    Status Start() noexcept;
    void Stop() noexcept { active_ = false; }
    // At most one send, including after stalls; no retry/catch-up burst.
    void Tick() noexcept;
    std::uint64_t NextDeadlineUs() const noexcept { return next_us_; }
    bool Active() const noexcept { return active_; }
    SyncStats Stats() const noexcept { return stats_; }

private:
    ICanDevice& device_;
    const std::uint64_t period_us_;
    const CanFrame frame_;
    ClockFn clock_;
    SyncCycle* cycle_;
    std::uint64_t next_us_ = 0;
    bool active_ = false;
    SyncStats stats_{};
};

// Route B: dedicated SCHED_OTHER boundary thread. Independent from RT AND the
// logging/dequeue thread, whose output can block. OS jitter is acceptable only
// for this task's frame-production check, NOT a future CSP timing acceptance.
// Start/Stop are single-caller lifecycle operations. While running, this worker
// exclusively owns device IO. The device must outlive Stop() and remain open.
class SyncWorker {
public:
    SyncWorker(ICanDevice& device, std::uint64_t period_us, std::uint32_t cob_id = wire::COB_SYNC,
               SyncCycle* cycle = nullptr) noexcept;
    ~SyncWorker();
    SyncWorker(const SyncWorker&) = delete;
    SyncWorker& operator=(const SyncWorker&) = delete;
    Status Start() noexcept;
    void Stop() noexcept;  // eventfd wakes poll; join guarantees no later send.
    SyncStats Stats() const noexcept;
    ErrorCode WorkerError() const noexcept { return worker_error_.load(); }
    int ActualPolicy() const noexcept { return actual_policy_.load(); }
    int ActualPriority() const noexcept { return actual_priority_.load(); }
    int LastSystemError() const noexcept { return system_error_.load(); }

private:
    static void* Entry(void* self) noexcept;
    void Run() noexcept;
    void Publish() noexcept;
    SyncProducer producer_;
    ICanDevice& device_;
    SyncCycle* cycle_;
    pthread_t thread_{};
    bool joinable_ = false;
    int timer_fd_ = -1;
    int stop_fd_ = -1;
    int ready_fd_ = -1;
    std::atomic<bool> stopping_{false};
    std::atomic<int> startup_{0};
    std::atomic<int> actual_policy_{-1};
    std::atomic<int> actual_priority_{-1};
    std::atomic<int> system_error_{0};
    std::atomic<ErrorCode> worker_error_{ErrorCode::Ok};
    // Approximate independent counters, never a lock/wait for the producer.
    std::atomic<std::uint64_t> attempts_{0}, sent_{0}, failures_{0};
    std::atomic<std::uint64_t> consecutive_failures_{0}, skipped_{0}, max_late_us_{0};
    std::atomic<ErrorCode> last_error_{ErrorCode::Ok};
};

}  // namespace km3::canopen
