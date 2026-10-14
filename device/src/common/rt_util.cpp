#include "km3/rt_util.hpp"

#include <pthread.h>
#include <sched.h>
#include <sys/mman.h>

#include <cstdio>
#include <cstring>

#include "km3/error_code.hpp"

namespace km3 {

Status LockMemory() noexcept {
    // MCL_CURRENT 锁定已映射页，MCL_FUTURE 锁定后续分配。
    // 失败多数是权限不足（需要 CAP_IPC_LOCK 或足够的 memlock 限额）。
    if (::mlockall(MCL_CURRENT | MCL_FUTURE) != 0) {
        return Status(ErrorCode::EPermission);
    }
    return Status::Ok();
}

Status PrefaultStack(std::size_t bytes) noexcept {
    // 用 volatile 写入，防止被优化掉；分页写入以覆盖整段范围。
    const std::size_t page = 4096;
    volatile unsigned char sink = 0;
    for (std::size_t offset = 0; offset < bytes; offset += page) {
        volatile unsigned char probe = 0;
        sink = static_cast<unsigned char>(sink ^ probe);
    }
    (void)sink;
    return Status::Ok();
}

Status BindCurrentThreadToCpus(const std::vector<int>& cpus) noexcept {
    if (cpus.empty()) {
        return Status(ErrorCode::EInvalidRequest);
    }

    cpu_set_t set;
    CPU_ZERO(&set);
    for (int cpu : cpus) {
        if (cpu < 0 || cpu >= CPU_SETSIZE) {
            return Status(ErrorCode::EInvalidRequest);
        }
        CPU_SET(cpu, &set);
    }

    if (::pthread_setaffinity_np(::pthread_self(), sizeof(set), &set) != 0) {
        return Status(ErrorCode::EPermission);
    }
    return Status::Ok();
}

Status SetCurrentThreadRealtime(int priority) noexcept {
    struct sched_param param {};
    param.sched_priority = priority;

    if (::pthread_setschedparam(::pthread_self(), SCHED_FIFO, &param) != 0) {
        return Status(ErrorCode::EPermission);
    }
    return Status::Ok();
}

Status ConfigureRealtimeThread(const RealtimeConfig& cfg) noexcept {
    if (cfg.lock_memory) {
        const Status s = LockMemory();
        if (!s.ok()) {
            return s;
        }
    }
    if (cfg.stack_prefault_bytes > 0) {
        const Status s = PrefaultStack(cfg.stack_prefault_bytes);
        if (!s.ok()) {
            return s;
        }
    }
    // 先绑核再提优先级：反过来的话，提权过程中可能被迁到小核上。
    const Status s = BindCurrentThreadToCpus(cfg.cpus);
    if (!s.ok()) {
        return s;
    }
    return SetCurrentThreadRealtime(cfg.priority);
}

int CurrentCpu() noexcept {
    const int cpu = ::sched_getcpu();
    return cpu >= 0 ? cpu : -1;
}

std::string DescribeCurrentThread() noexcept {
    // 注意：不要对 sched_getscheduler 的返回值做 const_cast 后传给
    // pthread_getschedparam —— 那既绕过了类型检查又依赖未定义行为。
    int policy = SCHED_OTHER;
    struct sched_param param {};
    if (::pthread_getschedparam(::pthread_self(), &policy, &param) != 0) {
        policy = ::sched_getscheduler(0);
    }

    const char* policy_name = "OTHER";
    switch (policy) {
        case SCHED_FIFO:  policy_name = "SCHED_FIFO"; break;
        case SCHED_RR:    policy_name = "SCHED_RR"; break;
        case SCHED_OTHER: policy_name = "SCHED_OTHER"; break;
        default:          policy_name = "UNKNOWN"; break;
    }

    char buf[96] = {};
    std::snprintf(buf, sizeof(buf), "%s prio=%d cpu=%d",
                  policy_name, param.sched_priority, CurrentCpu());
    return std::string(buf);
}

}  // namespace km3
