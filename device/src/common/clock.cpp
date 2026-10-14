#include "km3/clock.hpp"

#include <cerrno>
#include <cstdio>
#include <ctime>

namespace km3 {

namespace {

std::uint64_t ReadClock(clockid_t which) noexcept {
    struct timespec ts {};
    if (::clock_gettime(which, &ts) != 0) {
        return 0;
    }
    return static_cast<std::uint64_t>(ts.tv_sec) * 1000000ULL +
           static_cast<std::uint64_t>(ts.tv_nsec) / 1000ULL;
}

}  // namespace

std::uint64_t MonotonicUs() noexcept {
    return ReadClock(CLOCK_MONOTONIC);
}

std::uint64_t WallClockUs() noexcept {
    return ReadClock(CLOCK_REALTIME);
}

std::string FormatRfc3339(std::uint64_t wall_us) {
    const auto secs = static_cast<std::time_t>(wall_us / 1000000ULL);
    const auto micros = static_cast<unsigned long long>(wall_us % 1000000ULL);

    std::tm tm_buf {};
    ::gmtime_r(&secs, &tm_buf);

    char date_part[32] = {};
    std::strftime(date_part, sizeof(date_part), "%Y-%m-%dT%H:%M:%S", &tm_buf);

    char out[48] = {};
    std::snprintf(out, sizeof(out), "%s.%06lluZ", date_part, micros);
    return std::string(out);
}

void SleepUntilMonotonic(std::uint64_t deadline_us) noexcept {
    const auto secs = static_cast<std::time_t>(deadline_us / 1000000ULL);
    const auto nsecs = static_cast<long>(deadline_us % 1000000ULL) * 1000L;

    struct timespec target {};
    target.tv_sec = secs;
    target.tv_nsec = nsecs;

    // 循环处理 EINTR：被信号打断不算到点。
    while (::clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &target, nullptr) == EINTR) {
        // 重新检查：若已过点则直接返回
        if (MonotonicUs() >= deadline_us) {
            return;
        }
    }
}

void WarmUpClock() noexcept {
    (void)MonotonicUs();
    (void)WallClockUs();
    (void)FormatRfc3339(WallClockUs());
}

}  // namespace km3
