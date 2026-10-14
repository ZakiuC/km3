#pragma once
// 时间源。规则见 docs/架构设计/开发规范.md §1.4：
//
//   单调时钟  -> 超时、看门狗、周期推进、数据裁剪
//   墙上时钟  -> 对外上报、日志时间戳、签名
//
// 为什么必须区分：TL3588 启动早期时间可能旧于真实时间（RTC 未同步），
// 用墙上时间做超时判定会让防重放窗口与看门狗全部失效。实测记录见 实测基线.md §一。

#include <cstdint>
#include <string>

namespace km3 {

// CLOCK_MONOTONIC。用于一切"经过多久"的判断。
//
// 为什么不用 CLOCK_MONOTONIC_RAW（实测于 2026-10-13，见 实测基线.md）：
//   1. clock_nanosleep 不支持 RAW，直接返回 ENOTSUP —— 无法用它做周期睡眠；
//   2. 两个时钟不同源（本机差 162 秒）。用 RAW 算目标、用 MONOTONIC 睡，
//      目标会被当成"早已过去"，睡眠立即返回，周期循环退化为忙等。
//     该缺陷曾让 20x2ms 的定时器报告 max_late=0，掩盖了"根本没睡"。
//   CLOCK_MONOTONIC 在 NTP 下只做速率微调（slew），不会回退，满足单调性要求。
std::uint64_t MonotonicUs() noexcept;

// CLOCK_REALTIME，可被 NTP 调整。仅用于对外表达"现在几点"。
std::uint64_t WallClockUs() noexcept;

// 格式化为 RFC3339（毫秒以下保留 6 位微秒），UTC。
// 全网统一用它表达时间，见 通信协议规范.md §1.2。
std::string FormatRfc3339(std::uint64_t wall_us);

// 阻塞到指定的单调时刻。允许提前返回（信号），调用方需自行复核。
void SleepUntilMonotonic(std::uint64_t deadline_us) noexcept;

// 首次调用会触发 vDSO/时区等一次性初始化，产生页错误。
// 必须在进入实时循环之前调用，避免首次取时钟时抖动。
void WarmUpClock() noexcept;

}  // namespace km3
