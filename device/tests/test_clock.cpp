// 时间源与周期时基的验证。
//
// 重点：DeadlineSchedule 是纯算术，可以注入时间序列做**确定性**验证。
// 实时性缺陷大多是时基漂移造成的，这类测试比"睡一觉看看"可靠得多。
#include <thread>
#include <vector>

#include "km3/clock.hpp"
#include "km3/deadline.hpp"
#include "km3/rt_util.hpp"
#include "km3/config_snapshot.hpp"
#include "test_harness.hpp"

using km3::DeadlineSchedule;
using km3::MonotonicUs;
using km3::PeriodicTimer;
using km3::WallClockUs;

// ---------- 时间源 ----------

KM3_TEST(monotonic_clock_never_goes_backwards) {
    std::uint64_t prev = MonotonicUs();
    KM3_CHECK(prev > 0);
    for (int i = 0; i < 1000; ++i) {
        const std::uint64_t now = MonotonicUs();
        KM3_CHECK(now >= prev);
        prev = now;
    }
}

KM3_TEST(monotonic_advances_with_real_time) {
    const std::uint64_t t0 = MonotonicUs();
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    const std::uint64_t t1 = MonotonicUs();
    // 至少过去 4ms（容忍调度抖动），且不应出现量级错误
    KM3_CHECK(t1 - t0 >= 4000);
    KM3_CHECK(t1 - t0 < 200000);
}

KM3_TEST(wall_clock_is_after_2020) {
    // 墙上时钟可能因 RTC 未同步而偏移，但不应回到 2020 年之前。
    // 这正是"超时必须用单调时钟"的原因 —— 见 实测基线.md §一。
    const std::uint64_t now = WallClockUs();
    const std::uint64_t y2020 = 1577836800ULL * 1000000ULL;
    KM3_CHECK(now > y2020);
}

KM3_TEST(rfc3339_formatting_is_utc_with_micros) {
    KM3_CHECK_EQ(km3::FormatRfc3339(0ULL), std::string("1970-01-01T00:00:00.000000Z"));
    KM3_CHECK_EQ(km3::FormatRfc3339(1700000000000000ULL), std::string("2023-11-14T22:13:20.000000Z"));
    KM3_CHECK_EQ(km3::FormatRfc3339(1700000000123456ULL), std::string("2023-11-14T22:13:20.123456Z"));
    KM3_CHECK_EQ(km3::FormatRfc3339(1702000000000001ULL), std::string("2023-12-08T01:46:40.000001Z"));
}

KM3_TEST(sleep_until_monotonic_actually_waits) {
    const std::uint64_t t0 = MonotonicUs();
    const std::uint64_t target = t0 + 5000;             // 目标是 5ms 后
    km3::SleepUntilMonotonic(target);
    const std::uint64_t t1 = MonotonicUs();

    KM3_CHECK(t1 >= target - 200);                      // 到点了
    // 回归断言：必须真的消耗了时间。
    // 早期实现用 CLOCK_MONOTONIC_RAW 取时间、CLOCK_MONOTONIC 睡眠，两个时钟不同源，
    // 目标被当作已过去而立即返回，上面那条断言也会因"现在已远超 target"而通过。
    // 因此必须显式检查流逝量，否则"没睡"会被误判成"睡够了"。
    KM3_CHECK(t1 - t0 >= 4000);
}

KM3_TEST(clock_nanosleep_and_monotonic_us_share_its_timebase) {
    // 直接验证时基一致：用 MonotonicUs 算目标，必须能真正等到。
    // 若两者回到不同时钟（如 RAW vs MONOTONIC），这里会立即失败。
    for (int i = 0; i < 5; ++i) {
        const std::uint64_t t0 = MonotonicUs();
        km3::SleepUntilMonotonic(t0 + 2000);
        const std::uint64_t elapsed = MonotonicUs() - t0;
        KM3_CHECK(elapsed >= 1500);
    }
}

// ---------- 周期时基（纯算术，确定性）----------

KM3_TEST(schedule_advances_by_absolute_period) {
    DeadlineSchedule s(1000, 0);          // 周期 1ms，起点 0
    KM3_CHECK_EQ(s.Next(), std::uint64_t{1000});

    s.Advance(1000);                      // 准点
    KM3_CHECK_EQ(s.Ticks(), std::uint64_t{1});
    KM3_CHECK_EQ(s.LastLateUs(), std::uint64_t{0});
    KM3_CHECK_EQ(s.Next(), std::uint64_t{2000});

    s.Advance(2005);                      // 迟到 5us
    KM3_CHECK_EQ(s.Ticks(), std::uint64_t{2});
    KM3_CHECK_EQ(s.LastLateUs(), std::uint64_t{5});
    KM3_CHECK_EQ(s.MaxLateUs(), std::uint64_t{5});
    KM3_CHECK_EQ(s.Next(), std::uint64_t{3000});
    KM3_CHECK_EQ(s.Overruns(), std::uint64_t{0});
}

KM3_TEST(schedule_does_not_drift_over_many_cycles) {
    // 每周期都迟到 3us。绝对推进意味着迟到量不会累积成漂移：
    // 第 N 次唤醒的**目标时刻**始终是 N*period，而不是 N*(period+3)。
    DeadlineSchedule s(1000, 0);
    for (int i = 1; i <= 10000; ++i) {
        s.Advance(static_cast<std::uint64_t>(i) * 1000 + 3);
    }
    KM3_CHECK_EQ(s.Ticks(), std::uint64_t{10000});
    KM3_CHECK_EQ(s.MaxLateUs(), std::uint64_t{3});   // 迟到量恒定，未累积
    KM3_CHECK_EQ(s.Next(), std::uint64_t{10000} * 1000 + 1000);
}

KM3_TEST(schedule_detects_overrun_and_catches_up) {
    DeadlineSchedule s(1000, 0);
    s.Advance(1000);                      // 准点，Next -> 2000
    KM3_CHECK_EQ(s.Next(), std::uint64_t{2000});

    // 一次睡了 5ms（相当于控制回路卡死 5 个周期）
    s.Advance(5000);
    // 时基必须跳到未来，不能留在过去反复追赶
    KM3_CHECK(s.Next() > 5000);
    KM3_CHECK(s.Overruns() > 0);
    // 具体：after Advance(5000)，next=3000 <= 5000，behind=2000，missed=3
    KM3_CHECK_EQ(s.Overruns(), std::uint64_t{3});
    KM3_CHECK_EQ(s.Next(), std::uint64_t{6000});
}

KM3_TEST(schedule_counts_materially_late_cycles_only) {
    // 默认阈值 = 周期/10。1ms 周期 -> 100us。
    DeadlineSchedule s(1000, 0);
    KM3_CHECK_EQ(s.LateThresholdUs(), std::uint64_t{100});

    s.Advance(1010);   // 迟到 10us：未超阈值
    s.Advance(2020);   // 迟到 20us：未超阈值
    KM3_CHECK_EQ(s.LateTicks(), std::uint64_t{0});
    KM3_CHECK_EQ(s.MaxLateUs(), std::uint64_t{20});   // 最大值仍如实记录
    KM3_CHECK_EQ(s.Overruns(), std::uint64_t{0});

    s.Advance(3300);   // 迟到 300us：超阈值
    KM3_CHECK_EQ(s.LateTicks(), std::uint64_t{1});
    KM3_CHECK_EQ(s.MaxLateUs(), std::uint64_t{300});

    s.ResetStats();
    KM3_CHECK_EQ(s.Ticks(), std::uint64_t{0});
    KM3_CHECK_EQ(s.MaxLateUs(), std::uint64_t{0});
    KM3_CHECK_EQ(s.LateTicks(), std::uint64_t{0});
}

KM3_TEST(schedule_honours_explicit_late_threshold) {
    // 显式传阈值 0 之外的较小值：任何非零迟到都计数。
    DeadlineSchedule strict(1000, 0, 1);
    strict.Advance(1005);
    KM3_CHECK_EQ(strict.LateTicks(), std::uint64_t{1});

    // 显式传较大阈值：少量迟到不计数。
    DeadlineSchedule loose(1000, 0, 5000);
    loose.Advance(1010);
    loose.Advance(1020);
    KM3_CHECK_EQ(loose.LateTicks(), std::uint64_t{0});
}

KM3_TEST(periodic_timer_runs_and_reports_jitter) {
    // 用真实时钟跑 20 个 2ms 周期。
    constexpr std::uint64_t kPeriod = 2000;
    constexpr int kIters = 20;

    const std::uint64_t t0 = MonotonicUs();
    PeriodicTimer timer(kPeriod);
    std::uint64_t total_late = 0;
    for (int i = 0; i < kIters; ++i) {
        total_late += timer.WaitNext();
    }
    const std::uint64_t elapsed = MonotonicUs() - t0;

    KM3_CHECK_EQ(timer.Schedule().Ticks(), std::uint64_t{kIters});
    KM3_CHECK_EQ(timer.Schedule().Overruns(), std::uint64_t{0});

    // 回归断言：总时长至少要接近 20*2ms。允许下限放宽到 30ms，
    // 以容忍 CI 上的调度抖动，但必须能识别出"完全没有睡眠"这种情况。
    KM3_CHECK(elapsed >= 30000);

    // 迟到量打印出来供人工观察；不作为断言（CI 机器负载不可控）。
    std::printf("    [info] %dx%llums: elapsed=%llu us, max_late=%llu us, total_late=%llu us\n",
                kIters, (unsigned long long)(kPeriod / 1000),
                (unsigned long long)elapsed,
                (unsigned long long)timer.Schedule().MaxLateUs(),
                (unsigned long long)total_late);
}

// ---------- 实时环境设置 ----------

KM3_TEST(cpu_affinity_binds_to_requested_core) {
    std::vector<int> cpus = {0};          // CPU0 一定存在
    const km3::Status s = km3::BindCurrentThreadToCpus(cpus);
    KM3_CHECK(s.ok());
    KM3_CHECK_EQ(km3::CurrentCpu(), 0);

    // 恢复：允许迁移到全部核，避免影响后续用例
    cpu_set_t all;
    CPU_ZERO(&all);
    for (int i = 0; i < 8; ++i) CPU_SET(i, &all);
    (void)::pthread_setaffinity_np(::pthread_self(), sizeof(all), &all);
}

KM3_TEST(cpu_affinity_rejects_bad_input) {
    KM3_CHECK(!km3::BindCurrentThreadToCpus({}).ok());
    KM3_CHECK(!km3::BindCurrentThreadToCpus({-1}).ok());
    KM3_CHECK(!km3::BindCurrentThreadToCpus({99999}).ok());
}

KM3_TEST(realtime_setup_degrades_gracefully_without_privileges) {
    // 非 root 环境下 mlockall / SCHED_FIFO 应失败并返回 EPermission，
    // 而不是崩溃或静默假装成功。
    const km3::Status mem = km3::LockMemory();
    if (!mem.ok()) {
        KM3_CHECK(mem.code() == km3::ErrorCode::EPermission);
    }

    const km3::Status rt = km3::SetCurrentThreadRealtime(90);
    if (rt.ok()) {
        // 有权限：确认真的生效了
        KM3_CHECK(km3::DescribeCurrentThread().find("SCHED_FIFO") != std::string::npos);
        // 恢复为非实时，避免影响后续用例
        struct sched_param p {};
        p.sched_priority = 0;
        (void)::pthread_setschedparam(::pthread_self(), SCHED_OTHER, &p);
    } else {
        KM3_CHECK(rt.code() == km3::ErrorCode::EPermission);
    }
}

KM3_TEST(describe_thread_is_readable) {
    const std::string d = km3::DescribeCurrentThread();
    KM3_CHECK(!d.empty());
    // 必须含有调度策略与 CPU 编号，便于现场确认"以为设了其实没设"
    KM3_CHECK(d.find("prio=") != std::string::npos);
    KM3_CHECK(d.find("cpu=") != std::string::npos);
}

KM3_TEST(configure_realtime_thread_reports_step_failure) {
    km3::RealtimeConfig cfg;
    cfg.cpus = {};                        // 非法：空核集
    cfg.lock_memory = false;
    cfg.stack_prefault_bytes = 0;
    const km3::Status s = km3::ConfigureRealtimeThread(cfg);
    KM3_CHECK(!s.ok());
    KM3_CHECK(s.code() == km3::ErrorCode::EInvalidRequest);
}

// ---------- 配置快照 ----------

struct PodConfig {
    float climb_speed_max_mps;
    std::uint32_t watchdog_us;
    std::uint8_t flags;
};

KM3_TEST(config_snapshot_swaps_atomically) {
    PodConfig initial{1.0f, 500000, 0};
    km3::ConfigSnapshot<PodConfig> snap(initial);

    KM3_CHECK_EQ(snap.Get().watchdog_us, std::uint32_t{500000});
    KM3_CHECK_EQ(snap.PublishedCount(), std::uint64_t{0});

    PodConfig updated{2.0f, 3000000, 1};
    snap.Publish(updated);

    KM3_CHECK_EQ(snap.Get().watchdog_us, std::uint32_t{3000000});
    KM3_CHECK(snap.Get().climb_speed_max_mps > 1.99f);
    KM3_CHECK_EQ(snap.PublishedCount(), std::uint64_t{1});

    // 再发布一次，确认来回切换不错乱
    snap.Publish(initial);
    KM3_CHECK_EQ(snap.Get().watchdog_us, std::uint32_t{500000});
    KM3_CHECK_EQ(snap.PublishedCount(), std::uint64_t{2});
}

KM3_TEST(config_snapshot_stays_within_preallocated_slots) {
    // 反复发布不得增长内存（没有动态分配）。用地址集合来验证只有两个槽。
    PodConfig cfg{1.0f, 1000, 0};
    km3::ConfigSnapshot<PodConfig> snap(cfg);

    const void* a = static_cast<const void*>(&snap.Get());
    snap.Publish(cfg);
    const void* b = static_cast<const void*>(&snap.Get());
    snap.Publish(cfg);
    const void* c = static_cast<const void*>(&snap.Get());

    KM3_CHECK(a != b);
    KM3_CHECK_EQ(a, c);                   // 只有两个槽，第三次回到第一个
}

KM3_TEST_MAIN()
