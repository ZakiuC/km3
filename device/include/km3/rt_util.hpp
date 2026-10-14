#pragma once
// 实时运行环境设置。依据 docs/架构设计/开发规范.md §1.4 与
// 设备端程序架构.md §7.1/§7.2、§9.2（能力授予而非 root）。
//
// 全部接口返回 Status，不抛异常 —— 实时路径与启动路径都不用异常。
// 调用时机：**进程启动、进入实时循环之前**，由主线程完成。

#include <cstddef>
#include <string>
#include <vector>

#include "km3/result.hpp"

namespace km3 {

struct RealtimeConfig {
    int priority = 90;                       // SCHED_FIFO 优先级
    std::vector<int> cpus = {4, 5, 6, 7};    // 默认绑 A76 大核簇
    std::size_t stack_prefault_bytes = 256 * 1024;
    bool lock_memory = true;
};

// 锁定当前及将来的全部内存（mlockall）。
// 目的不是"省内存"，而是消除缺页导致的不可预测停顿。
Status LockMemory() noexcept;

// 预先触碰一段栈空间，避免实时循环中首次深调用触发缺页。
Status PrefaultStack(std::size_t bytes) noexcept;

// 把当前线程绑定到给定 CPU 集合。
Status BindCurrentThreadToCpus(const std::vector<int>& cpus) noexcept;

// 把当前线程设为 SCHED_FIFO。需要 CAP_SYS_NICE。
Status SetCurrentThreadRealtime(int priority) noexcept;

// 依次执行上述三项。任一步失败都会返回对应错误码，调用方决定是否降级运行。
Status ConfigureRealtimeThread(const RealtimeConfig& cfg) noexcept;

// 当前线程实际所在的 CPU；取不到时返回 -1。
int CurrentCpu() noexcept;

// 供日志使用的可读描述，例如 "SCHED_FIFO prio=90 cpu=6"。
// 便于现场确认"以为设了其实没设"这类问题。
std::string DescribeCurrentThread() noexcept;

}  // namespace km3
