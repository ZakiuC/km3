#include <atomic>
#include <cstdlib>
#include <limits>
#include <new>
#include <sched.h>

#include "km3/canopen_sync.hpp"
#include "km3/config.hpp"
#include "test_harness.hpp"

namespace {
std::atomic<bool> count_allocations{false};
std::atomic<unsigned> allocations{0};
std::uint64_t fake_now = 0;
std::uint64_t FakeClock() noexcept {
    return fake_now;
}

class ProbeCan final : public km3::ICanDevice {
public:
    const std::string& LogicalName() const noexcept override { return name_; }
    km3::Status Open() noexcept override {
        open = true;
        return km3::Status::Ok();
    }
    km3::Status Close() noexcept override {
        open = false;
        return km3::Status::Ok();
    }
    bool IsOpen() const noexcept override { return open; }
    km3::DeviceStatus StatusOf() const override { return {}; }
    km3::CanBackend Backend() const noexcept override { return km3::CanBackend::SocketCan; }
    km3::CanCapabilities Capabilities() const override { return {}; }
    km3::Status Send(const km3::CanFrame& frame) noexcept override {
        last_frame = frame;
        calls.fetch_add(1);
        int value = -1;
        sched_param param{};
        ::pthread_getschedparam(::pthread_self(), &value, &param);
        policy.store(value);
        if (advance != 0)
            fake_now += advance;
        return failing.load() ? km3::Status(km3::ErrorCode::EDeviceBusy) : km3::Status::Ok();
    }
    km3::Result<km3::CanFrame> Receive(int) override {
        return km3::Result<km3::CanFrame>::Err(km3::ErrorCode::ECommandTimeout);
    }
    km3::Status SetFilters(const std::vector<km3::CanFilter>&) override {
        return km3::Status::Ok();
    }
    km3::CanBusStats Stats() const override { return {}; }
    km3::Status RecoverFromBusOff() override {
        return km3::Status(km3::ErrorCode::ECommandRejected);
    }
    bool open = true;
    std::atomic<bool> failing{false};
    std::atomic<unsigned> calls{0};
    std::atomic<int> policy{-1};
    std::uint64_t advance = 0;
    km3::CanFrame last_frame{};

private:
    std::string name_{"test-can"};
};
}  // namespace

// Count actual runtime paths, not just the construction of a trivial frame.
void* operator new(std::size_t size) {
    if (count_allocations.load())
        allocations.fetch_add(1);
    if (void* p = std::malloc(size == 0 ? 1 : size))
        return p;
    throw std::bad_alloc();
}
void* operator new[](std::size_t size) {
    return ::operator new(size);
}
void operator delete(void* p) noexcept {
    std::free(p);
}
void operator delete[](void* p) noexcept {
    std::free(p);
}
void operator delete(void* p, std::size_t) noexcept {
    std::free(p);
}
void operator delete[](void* p, std::size_t) noexcept {
    std::free(p);
}

KM3_TEST(sync_frame_has_only_standard_zero_length_cob_id) {
    const auto frame = km3::canopen::SyncProducer::MakeFrame();
    KM3_CHECK_EQ(frame.id, 0x080u);
    KM3_CHECK_EQ(frame.dlc, 0u);
    KM3_CHECK(!frame.extended && !frame.fd && !frame.remote && !frame.bitrate_switch);
    for (auto value : frame.data) KM3_CHECK_EQ(value, 0u);
}

KM3_TEST(sync_is_inactive_until_started_and_stays_inactive_after_stop) {
    ProbeCan can;
    fake_now = 100;
    km3::canopen::SyncProducer sync(can, 4000, &FakeClock);
    fake_now = 10000;
    sync.Tick();
    KM3_CHECK_EQ(can.calls.load(), 0u);
    KM3_REQUIRE(sync.Start().ok());
    KM3_CHECK_EQ(sync.NextDeadlineUs(), 14000u);
    sync.Tick();
    KM3_CHECK_EQ(can.calls.load(), 0u);
    fake_now = 14000;
    sync.Tick();
    KM3_CHECK_EQ(can.calls.load(), 1u);
    sync.Stop();
    fake_now = 1000000;
    sync.Tick();
    KM3_CHECK_EQ(can.calls.load(), 1u);
}

KM3_TEST(sync_jumps_over_expired_periods_without_drift_or_bursts) {
    ProbeCan can;
    fake_now = 100;
    km3::canopen::SyncProducer sync(can, 4000, &FakeClock);
    KM3_REQUIRE(sync.Start().ok());
    fake_now = 14100;
    sync.Tick();
    KM3_CHECK_EQ(can.calls.load(), 1u);
    KM3_CHECK_EQ(sync.Stats().skipped, 2u);
    KM3_CHECK_EQ(sync.Stats().max_late_us, 10000u);
    KM3_CHECK_EQ(sync.NextDeadlineUs(), 16100u);
    sync.Tick();
    KM3_CHECK_EQ(can.calls.load(), 1u);
    fake_now = 16100;
    can.advance = 10000;  // Includes a scheduling stall DURING send.
    sync.Tick();
    KM3_CHECK_EQ(sync.NextDeadlineUs(), 28100u);
    KM3_CHECK_EQ(sync.Stats().skipped, 4u);
    sync.Tick();
    KM3_CHECK_EQ(can.calls.load(), 2u);
}

KM3_TEST(sync_success_failure_and_overrun_loop_allocates_nothing) {
    ProbeCan can;
    fake_now = 100;
    km3::canopen::SyncProducer sync(can, 4000, &FakeClock);
    KM3_REQUIRE(sync.Start().ok());
    allocations.store(0);
    count_allocations.store(true);
    for (unsigned i = 0; i < 10000; ++i) {
        can.failing.store(i % 3 != 0);
        fake_now = sync.NextDeadlineUs() + (i % 2 == 0 ? 9000 : 0);
        sync.Tick();
    }
    count_allocations.store(false);
    KM3_CHECK_EQ(allocations.load(), 0u);
    KM3_CHECK_EQ(sync.Stats().attempts, 10000u);
    KM3_CHECK_EQ(sync.Stats().sent, 3334u);
    KM3_CHECK_EQ(sync.Stats().failures, 6666u);
    KM3_CHECK_EQ(sync.Stats().skipped, 10000u);
    KM3_CHECK(sync.Stats().last_error == km3::ErrorCode::EDeviceBusy);
    KM3_CHECK_EQ(sync.Stats().consecutive_failures, 0u);
}

KM3_TEST(sync_continuous_failure_is_observable_and_resets_on_success) {
    ProbeCan can;
    can.failing.store(true);
    fake_now = 0;
    km3::canopen::SyncProducer sync(can, 4000, &FakeClock);
    KM3_REQUIRE(sync.Start().ok());
    for (unsigned i = 0; i < 3; ++i) {
        fake_now = sync.NextDeadlineUs();
        sync.Tick();
    }
    KM3_CHECK_EQ(sync.Stats().consecutive_failures, 3u);
    KM3_CHECK_EQ(sync.Stats().failures, 3u);
    can.failing.store(false);
    fake_now = sync.NextDeadlineUs();
    sync.Tick();
    KM3_CHECK_EQ(sync.Stats().consecutive_failures, 0u);
    KM3_CHECK_EQ(sync.Stats().sent, 1u);
    KM3_CHECK(sync.Stats().last_error == km3::ErrorCode::EDeviceBusy);  // Historical, not erased.
}

KM3_TEST(sync_invalid_start_and_arithmetic_limit_are_explicit) {
    ProbeCan can;
    for (const auto bad : {std::uint64_t{0}, km3::kMaxSyncPeriodUs + 1}) {
        km3::canopen::SyncProducer sync(can, bad, &FakeClock);
        KM3_CHECK(sync.Start().code() == km3::ErrorCode::EInvalidConfig);
    }
    can.open = false;
    km3::canopen::SyncProducer offline(can, 4000, &FakeClock);
    KM3_CHECK(offline.Start().code() == km3::ErrorCode::EDeviceOffline);
    can.open = true;
    fake_now = std::numeric_limits<std::uint64_t>::max() - 100;
    km3::canopen::SyncProducer overflow(can, 4000, &FakeClock);
    KM3_CHECK(overflow.Start().code() == km3::ErrorCode::EInvalidRequest);
    fake_now -= 8000;
    KM3_REQUIRE(overflow.Start().ok());
    fake_now = std::numeric_limits<std::uint64_t>::max();
    overflow.Tick();
    KM3_CHECK(!overflow.Active());
    KM3_CHECK(overflow.Stats().last_error == km3::ErrorCode::EInternal);
}

KM3_TEST(worker_is_verified_sched_other_and_running_loop_allocates_nothing) {
    ProbeCan can;
    km3::canopen::SyncWorker sync(can, 1000);
    KM3_REQUIRE(sync.Start().ok());
    KM3_CHECK_EQ(sync.ActualPolicy(), SCHED_OTHER);
    KM3_CHECK_EQ(sync.ActualPriority(), 0);
    KM3_CHECK(sync.Start().code() == km3::ErrorCode::EDeviceBusy);
    allocations.store(0);
    count_allocations.store(true);
    auto deadline = km3::MonotonicUs() + 1000000;
    while (sync.Stats().sent == 0 && km3::MonotonicUs() < deadline)
        km3::SleepUntilMonotonic(km3::MonotonicUs() + 1000);
    can.failing.store(true);
    deadline = km3::MonotonicUs() + 1000000;
    while (sync.Stats().consecutive_failures < 3 && km3::MonotonicUs() < deadline)
        km3::SleepUntilMonotonic(km3::MonotonicUs() + 1000);
    count_allocations.store(false);
    sync.Stop();
    const auto stats = sync.Stats();
    KM3_CHECK_EQ(allocations.load(), 0u);
    KM3_CHECK(stats.sent > 0);
    KM3_CHECK(stats.failures > 0);
    KM3_CHECK(stats.consecutive_failures > 0);
    KM3_CHECK_EQ(can.policy.load(), SCHED_OTHER);
    KM3_CHECK(sync.WorkerError() == km3::ErrorCode::Ok);
    const auto calls = can.calls.load();
    km3::SleepUntilMonotonic(km3::MonotonicUs() + 5000);
    KM3_CHECK_EQ(can.calls.load(), calls);
    KM3_CHECK_EQ(can.last_frame.id, 0x080u);
    KM3_CHECK_EQ(can.last_frame.dlc, 0u);
}

KM3_TEST(worker_stop_before_first_deadline_sends_no_final_frame) {
    ProbeCan can;
    km3::canopen::SyncWorker sync(can, km3::kMaxSyncPeriodUs);
    KM3_REQUIRE(sync.Start().ok());
    const auto before = km3::MonotonicUs();
    sync.Stop();
    KM3_CHECK(km3::MonotonicUs() - before < 500000);
    KM3_CHECK_EQ(can.calls.load(), 0u);
    sync.Stop();
    KM3_REQUIRE(sync.Start().ok());
    sync.Stop();
    KM3_CHECK_EQ(can.calls.load(), 0u);
}

KM3_TEST_MAIN()
