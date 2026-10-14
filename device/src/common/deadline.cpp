#include "km3/deadline.hpp"

#include "km3/clock.hpp"

namespace km3 {

DeadlineSchedule::DeadlineSchedule(std::uint64_t period_us,
                                   std::uint64_t start_us,
                                   std::uint64_t late_threshold_us) noexcept
    : period_us_(period_us),
      next_us_(start_us + period_us),
      late_threshold_us_(late_threshold_us != 0
                             ? late_threshold_us
                             : (period_us / kDefaultLateThresholdRatio > 0
                                    ? period_us / kDefaultLateThresholdRatio
                                    : 1)) {
}

void DeadlineSchedule::Advance(std::uint64_t now_us) noexcept {
    ++ticks_;

    const std::uint64_t late = (now_us > next_us_) ? (now_us - next_us_) : 0;
    last_late_us_ = late;
    // 只统计超过阈值的迟到；否则绝对推进下这里会恒等于 ticks_。
    if (late > late_threshold_us_) {
        ++late_ticks_;
    }
    if (late > max_late_us_) {
        max_late_us_ = late;
    }

    // 绝对推进：next += period，绝不用 now + period。
    // 后者会让每一周期的抖动累积成长期漂移。
    next_us_ += period_us_;

    if (next_us_ <= now_us) {
        // 已经落后一个完整周期以上：控制系统没跟上。
        // 统计被吃掉的周期数，并把时基跳到第一个仍在未来的时刻。
        const std::uint64_t behind = now_us - next_us_;
        const std::uint64_t missed = behind / period_us_ + 1;
        overruns_ += missed;
        next_us_ += missed * period_us_;
    }
}

void DeadlineSchedule::ResetStats() noexcept {
    ticks_ = 0;
    overruns_ = 0;
    late_ticks_ = 0;
    last_late_us_ = 0;
    max_late_us_ = 0;
}

PeriodicTimer::PeriodicTimer(std::uint64_t period_us, ClockFn clock,
                             std::uint64_t late_threshold_us) noexcept
    : schedule_(period_us, 0),
      clock_(clock != nullptr ? clock : &MonotonicUs) {
    // 以当前时刻为起点，避免构造时就把第一个周期算成迟到。
    schedule_ = DeadlineSchedule(period_us, clock_(), late_threshold_us);
}

std::uint64_t PeriodicTimer::WaitNext() noexcept {
    const std::uint64_t deadline = schedule_.Next();
    SleepUntilMonotonic(deadline);
    schedule_.Advance(clock_());
    return schedule_.LastLateUs();
}

}  // namespace km3
