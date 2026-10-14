#pragma once
// 周期调度的时基。见 docs/架构设计/系统总体架构.md §七-
// "周期性关键路径使用单调时钟和绝对时间推进，避免每周期相对睡眠导致累计漂移"。
//
// 拆成两层便于确定性测试：
//   DeadlineSchedule —— 纯算术，无 IO、不取时钟，可直接注入时间序列验证
//   PeriodicTimer    —— 组合真实睡眠与时钟

#include <cstdint>

namespace km3 {

// 时基推进器。所有统计都基于"迟到量"而非墙钟。
// 迟到阈值：绝对推进下，每次唤醒都会比目标晚几微秒，
// 因此"late > 0"几乎恒等于 ticks，作为健康指标没有意义。
// 只有超过阈值的迟到才算"实质性迟到"。threshold 传 0 时取周期的 10%（最小 1us）。
inline constexpr std::uint64_t kDefaultLateThresholdRatio = 10;

class DeadlineSchedule {
public:
    DeadlineSchedule(std::uint64_t period_us, std::uint64_t start_us,
                     std::uint64_t late_threshold_us = 0) noexcept;

    // 当前应当唤醒的绝对时刻。
    std::uint64_t Next() const noexcept { return next_us_; }

    // 以 now_us 为观测点推进一个周期。
    // 迟到一个周期以内 -> 记为 jitter；迟到一个周期以上 -> 记为 overrun 并追赶时基。
    void Advance(std::uint64_t now_us) noexcept;

    std::uint64_t Period() const noexcept { return period_us_; }
    // 实质性迟到的判定阈值（微秒）。
    std::uint64_t LateThresholdUs() const noexcept { return late_threshold_us_; }
    std::uint64_t Ticks() const noexcept { return ticks_; }
    // 完全被吃掉的周期数（表明控制系统来不及算）。
    std::uint64_t Overruns() const noexcept { return overruns_; }
    // 最近一次迟到量（唤醒抖动 + 执行时间超出）。
    std::uint64_t LastLateUs() const noexcept { return last_late_us_; }
    // 观测到的最大迟到量。这是实时性验收要看的关键指标。
    std::uint64_t MaxLateUs() const noexcept { return max_late_us_; }
    // **实质性**迟到的周期数：迟到量超过 LateThresholdUs()，不含被完全吃掉的周期。
    // 不要把它当作"迟到比例"来读 —— 见 LateThresholdUs() 的说明。
    std::uint64_t LateTicks() const noexcept { return late_ticks_; }

    void ResetStats() noexcept;

private:
    std::uint64_t period_us_;
    std::uint64_t next_us_;
    std::uint64_t late_threshold_us_;
    std::uint64_t ticks_ = 0;
    std::uint64_t overruns_ = 0;
    std::uint64_t late_ticks_ = 0;
    std::uint64_t last_late_us_ = 0;
    std::uint64_t max_late_us_ = 0;
};

// 组合真实时钟的周期等待器。
class PeriodicTimer {
public:
    using ClockFn = std::uint64_t (*)() noexcept;

    explicit PeriodicTimer(std::uint64_t period_us,
                           ClockFn clock = nullptr,
                           std::uint64_t late_threshold_us = 0) noexcept;

    // 睡眠到下一个时基并推进。返回本次观测到的迟到量（微秒）。
    std::uint64_t WaitNext() noexcept;

    const DeadlineSchedule& Schedule() const noexcept { return schedule_; }
    DeadlineSchedule& Schedule() noexcept { return schedule_; }

private:
    DeadlineSchedule schedule_;
    ClockFn clock_;
};

}  // namespace km3
