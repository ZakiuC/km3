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
        if (replace_send_time)
            fake_now = send_time;
        if (send_delay_us != 0)
            km3::SleepUntilMonotonic(km3::MonotonicUs() + send_delay_us);
        return failing.load() ? km3::Status(km3::ErrorCode::EDeviceBusy) : km3::Status::Ok();
    }
    km3::Status SendConfirmed(const km3::CanFrame& frame,
                              std::uint64_t deadline) noexcept override {
        confirmed_deadline = deadline;
        return Send(frame);
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
    bool replace_send_time = false;
    std::uint64_t send_time = 0;
    std::uint64_t send_delay_us = 0;
    std::uint64_t confirmed_deadline = 0;
    km3::CanFrame last_frame{};

private:
    std::string name_{"test-can"};
};

class ProbeCycle final : public km3::canopen::SyncCycle {
public:
    km3::Status ValidateForSync(std::uint32_t) const noexcept override { return km3::Status::Ok(); }
    km3::Status BeforeSync(std::uint64_t deadline) noexcept override {
        before_deadline = deadline;
        ++before_calls;
        fake_now += advance;
        return km3::Status(error);
    }
    void LatchFailure() noexcept override { ++latches; }
    void ReceiveAvailable() noexcept override {}
    std::uint64_t advance = 0;
    std::uint64_t before_deadline = 0;
    unsigned before_calls = 0;
    unsigned latches = 0;
    km3::ErrorCode error = km3::ErrorCode::Ok;
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
    KM3_CHECK_EQ(sync.Stats().min_send_margin_us, -6000);
    KM3_CHECK_EQ(sync.NextDeadlineUs(), 16100u);
    sync.Tick();
    KM3_CHECK_EQ(can.calls.load(), 1u);
    fake_now = 16100;
    can.advance = 10000;  // Includes a scheduling stall DURING send.
    sync.Tick();
    KM3_CHECK_EQ(sync.NextDeadlineUs(), 28100u);
    KM3_CHECK_EQ(sync.Stats().skipped, 4u);
    KM3_CHECK_EQ(sync.Stats().min_send_margin_us, -6000);
    sync.Tick();
    KM3_CHECK_EQ(can.calls.load(), 2u);
}

KM3_TEST(sync_send_margin_samples_positive_zero_negative_and_keeps_the_minimum) {
    for (const bool confirmed : {false, true}) {
        ProbeCan can;
        ProbeCycle cycle;
        fake_now = 0;
        km3::canopen::SyncProducer sync(can, 4000, &FakeClock, 0x80, confirmed ? &cycle : nullptr);
        KM3_CHECK_EQ(sync.Stats().min_send_margin_us, km3::canopen::kNoSyncSendMarginUs);
        KM3_REQUIRE(sync.Start().ok());
        sync.Tick();  // Not due: no attempt or sample.
        KM3_CHECK_EQ(sync.Stats().min_send_margin_us, km3::canopen::kNoSyncSendMarginUs);
        for (const auto duration : {1000u, 4000u, 4500u, 0u}) {
            fake_now = sync.NextDeadlineUs() + 1;
            can.advance = duration;
            sync.Tick();
            const auto expected = duration == 1000 ? 2999 : duration == 4000 ? -1 : -501;
            KM3_CHECK_EQ(sync.Stats().min_send_margin_us, expected);
            KM3_CHECK_EQ(sync.Stats().max_late_us, 1u);
            if (confirmed)
                KM3_CHECK_EQ(can.confirmed_deadline, cycle.before_deadline);
        }
        KM3_CHECK_EQ(sync.Stats().sent, 4u);  // Telemetry does not rewrite Send's result.
        sync.Stop();
        const auto minimum = sync.Stats().min_send_margin_us;
        fake_now += 100000;
        sync.Tick();
        KM3_CHECK_EQ(sync.Stats().min_send_margin_us, minimum);
        KM3_REQUIRE(sync.Start().ok());
        KM3_CHECK_EQ(sync.Stats().min_send_margin_us, km3::canopen::kNoSyncSendMarginUs);
        KM3_CHECK_EQ(sync.Stats().max_late_us, 0u);
    }
    // An exact Send return at the deadline has zero remaining time.
    for (const bool confirmed : {false, true}) {
        ProbeCan can;
        ProbeCycle cycle;
        fake_now = 0;
        km3::canopen::SyncProducer sync(can, 4000, &FakeClock, 0x80, confirmed ? &cycle : nullptr);
        KM3_REQUIRE(sync.Start().ok());
        fake_now = 4000;
        can.advance = 4000;
        sync.Tick();
        KM3_CHECK_EQ(sync.Stats().min_send_margin_us, 0);
    }
}

KM3_TEST(sync_before_failure_and_pre_send_timeout_have_margin_without_sending) {
    for (const auto elapsed : {1000u, 4000u, 5000u}) {
        for (const bool fail_before : {false, true}) {
            ProbeCan can;
            ProbeCycle cycle;
            fake_now = 0;
            cycle.advance = elapsed;
            if (fail_before)
                cycle.error = km3::ErrorCode::EDeviceBusy;
            km3::canopen::SyncProducer sync(can, 4000, &FakeClock, 0x80, &cycle);
            KM3_REQUIRE(sync.Start().ok());
            fake_now = 4000;
            allocations.store(0);
            count_allocations.store(true);
            sync.Tick();
            count_allocations.store(false);
            KM3_CHECK_EQ(allocations.load(), 0u);
            KM3_CHECK_EQ(sync.Stats().min_send_margin_us, 4000 - static_cast<int>(elapsed));
            KM3_CHECK_EQ(sync.Stats().max_late_us, 0u);
            KM3_CHECK_EQ(can.calls.load(), !fail_before && elapsed < 4000 ? 1u : 0u);
            const bool failed = fail_before || elapsed >= 4000;
            KM3_CHECK_EQ(cycle.latches, failed ? 1u : 0u);
            KM3_CHECK_EQ(sync.Active(), !failed);
            if (failed)
                KM3_CHECK(
                    sync.Stats().last_error ==
                    (fail_before ? km3::ErrorCode::EDeviceBusy : km3::ErrorCode::ECommandTimeout));
        }
    }
}

KM3_TEST(sync_failed_send_records_return_margin_and_cycle_latches) {
    for (const bool confirmed : {false, true}) {
        ProbeCan can;
        ProbeCycle cycle;
        can.failing.store(true);
        can.advance = 4500;
        fake_now = 0;
        km3::canopen::SyncProducer sync(can, 4000, &FakeClock, 0x80, confirmed ? &cycle : nullptr);
        KM3_REQUIRE(sync.Start().ok());
        fake_now = 4001;  // Wake is only 1us late; the failed Send returns 501us overdue.
        sync.Tick();
        KM3_CHECK_EQ(sync.Stats().max_late_us, 1u);
        KM3_CHECK_EQ(sync.Stats().min_send_margin_us, -501);
        KM3_CHECK_EQ(sync.Stats().failures, 1u);
        KM3_CHECK_EQ(sync.Stats().skipped, 1u);
        KM3_CHECK_EQ(cycle.latches, confirmed ? 1u : 0u);
        KM3_CHECK_EQ(sync.Active(), !confirmed);
    }
}

KM3_TEST(sync_send_margin_saturates_without_signed_overflow_or_sentinel_collision) {
    constexpr auto signed_max = std::numeric_limits<std::int64_t>::max();
    constexpr auto unsigned_max = std::numeric_limits<std::uint64_t>::max();
    for (const auto overdue : {static_cast<std::uint64_t>(signed_max),
                               static_cast<std::uint64_t>(signed_max) + 1, unsigned_max - 2}) {
        ProbeCan can;
        fake_now = 0;
        km3::canopen::SyncProducer sync(can, 1, &FakeClock);
        KM3_REQUIRE(sync.Start().ok());
        fake_now = 1;
        can.replace_send_time = true;
        can.send_time = overdue + 2;
        sync.Tick();
        KM3_CHECK_EQ(sync.Stats().min_send_margin_us,
                     overdue == static_cast<std::uint64_t>(signed_max)
                         ? -signed_max
                         : std::numeric_limits<std::int64_t>::min());
    }
    // Synthetic clock regression exercises positive saturation; a real monotonic
    // clock limits positive margin to the period. INT64_MAX stays exclusive to n/a.
    for (const auto remaining :
         {static_cast<std::uint64_t>(signed_max) - 1, static_cast<std::uint64_t>(signed_max),
          static_cast<std::uint64_t>(signed_max) + 1}) {
        ProbeCan can;
        fake_now = remaining - 2;
        km3::canopen::SyncProducer sync(can, 1, &FakeClock);
        KM3_REQUIRE(sync.Start().ok());
        fake_now = sync.NextDeadlineUs();
        can.replace_send_time = true;
        can.send_time = 0;
        sync.Tick();
        KM3_CHECK_EQ(sync.Stats().min_send_margin_us, signed_max - 1);
    }
    // The original schedule can be representable while its send deadline is not.
    for (const bool confirmed : {false, true}) {
        ProbeCan edge_can;
        ProbeCycle edge_cycle;
        fake_now = unsigned_max - 2;
        km3::canopen::SyncProducer edge_sync(edge_can, 1, &FakeClock, 0x80,
                                             confirmed ? &edge_cycle : nullptr);
        KM3_REQUIRE(edge_sync.Start().ok());
        fake_now = unsigned_max - 1;  // Send deadline UINT64_MAX is still representable.
        edge_sync.Tick();
        KM3_CHECK_EQ(edge_sync.Stats().min_send_margin_us, 1);
        fake_now = unsigned_max;
        edge_sync.Tick();
        KM3_CHECK_EQ(edge_sync.Stats().min_send_margin_us, 1);  // No fabricated sample.

        ProbeCan can;
        ProbeCycle cycle;
        fake_now = unsigned_max - 1;
        km3::canopen::SyncProducer sync(can, 1, &FakeClock, 0x80, confirmed ? &cycle : nullptr);
        KM3_REQUIRE(sync.Start().ok());
        fake_now = unsigned_max;
        sync.Tick();
        KM3_CHECK_EQ(sync.Stats().min_send_margin_us, km3::canopen::kNoSyncSendMarginUs);
        KM3_CHECK(!sync.Active());
        KM3_CHECK(sync.Stats().last_error == km3::ErrorCode::EInternal);
        KM3_CHECK_EQ(can.calls.load(), confirmed ? 0u : 1u);
        KM3_CHECK_EQ(cycle.before_calls, 0u);
    }
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
    KM3_CHECK(stats.min_send_margin_us != km3::canopen::kNoSyncSendMarginUs);
    KM3_CHECK_EQ(can.policy.load(), SCHED_OTHER);
    KM3_CHECK(sync.WorkerError() == km3::ErrorCode::Ok);
    const auto calls = can.calls.load();
    km3::SleepUntilMonotonic(km3::MonotonicUs() + 5000);
    KM3_CHECK_EQ(can.calls.load(), calls);
    KM3_CHECK_EQ(can.last_frame.id, 0x080u);
    KM3_CHECK_EQ(can.last_frame.dlc, 0u);
}

KM3_TEST(worker_publishes_negative_send_margin_while_running_and_resets_on_restart) {
    ProbeCan can;
    can.send_delay_us = 200000;
    km3::canopen::SyncWorker sync(can, 100000);
    KM3_REQUIRE(sync.Start().ok());
    auto stats = sync.Stats();
    KM3_CHECK_EQ(stats.min_send_margin_us, km3::canopen::kNoSyncSendMarginUs);
    const auto deadline = km3::MonotonicUs() + 2000000;
    while (stats.min_send_margin_us == km3::canopen::kNoSyncSendMarginUs &&
           km3::MonotonicUs() < deadline) {
        km3::SleepUntilMonotonic(km3::MonotonicUs() + 1000);
        stats = sync.Stats();
    }
    sync.Stop();
    KM3_CHECK(stats.min_send_margin_us <= -100000);
    KM3_CHECK(sync.Stats().min_send_margin_us <= stats.min_send_margin_us);
    KM3_REQUIRE(sync.Start().ok());
    KM3_CHECK_EQ(sync.Stats().min_send_margin_us, km3::canopen::kNoSyncSendMarginUs);
    sync.Stop();
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
