// km3-rt-controller：实时控制进程。
//
// 约束见 docs/架构设计/设备端程序架构.md §2.2.0 与 开发规范.md §1.4：
//   - SCHED_FIFO 优先级 90，绑定 CPU 4-7（A76 大核），内存锁定
//   - 周期用单调时钟**绝对推进**，不用相对睡眠
//   - 周期内不得分配内存、加锁、写日志、做网络或序列化
//   - 状态经预分配无锁环形缓冲交给本进程的 BoundaryThread
//
// 本程序当前是**骨架**：实时环境设置与周期循环已到位并可验证，
// 硬件 HAL 与具体控制律待接入（见 src/hal/、src/rt-control/）。
//
// 用法：
//   km3-rt-controller [--config PATH] [--require-realtime]
//                     [--period-us N] [--duration-s N] [--require-config]
//
// 配置来源：--config 指定的 JSON（默认 /etc/km3/device.json）。
// 周期与控制参数**来自配置**，不在代码里写死；见 docs/架构设计/设备端程序架构.md §2.3.5。
//
// --require-realtime 时，实时环境设置失败即退出（非零）。
// 默认**不要求**，便于在开发机上跑通逻辑；但生产单元文件应带上该标志 ——
// 静默地以非实时方式运行一个"实时"进程，比启动失败危险得多。
//
// --require-config 时，配置缺失或校验不过即退出。生产同样应带上：
// 用默认值继续跑一个"看起来正常"的控制回路，比拒绝启动危险得多。

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

#include "km3/clock.hpp"
#include "km3/config.hpp"
#include "km3/config_snapshot.hpp"
#include "km3/deadline.hpp"
#include "km3/rt_types.hpp"
#include "km3/rt_util.hpp"
#include "km3/spsc_ring.hpp"

namespace {

std::atomic<bool> g_running{true};

void HandleSignal(int) { g_running.store(false); }

// 预分配的边界缓冲：容量编译期确定，运行期不再分配。
constexpr std::size_t kStateRingCapacity = 1024;
constexpr std::size_t kSampleRingCapacity = 4096;

km3::SpscRing<km3::MotionSnapshot, kStateRingCapacity> g_state_ring;
km3::SpscRing<km3::SensorSample, kSampleRingCapacity> g_sample_ring;
km3::SpscRing<km3::SafetyEvent, 64> g_safety_ring;

// 边界线程：非实时（SCHED_OTHER）。负责出队、序列化、交给消息总线。
// 所有可能阻塞或分配的工作都在这里，绝不在实时线程里做。
void BoundaryLoop(std::atomic<bool>& running) {
    km3::MotionSnapshot motion;
    km3::SensorSample sample;
    km3::SafetyEvent event;
    std::uint64_t drained = 0;

    while (running.load(std::memory_order_acquire)) {
        while (g_safety_ring.Pop(event)) {
            // TODO: 安全事件优先上报，走 P1 通道
            ++drained;
        }
        while (g_state_ring.Pop(motion)) {
            // TODO: 序列化并交给 km3-core
            ++drained;
        }
        while (g_sample_ring.Pop(sample)) {
            // TODO: 批量落盘 / 上报
            ++drained;
        }
        // 边界线程允许睡眠；它不在实时路径上。
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

struct Options {
    bool require_realtime = false;
    bool require_config = false;
    std::string config_path = "/etc/km3/device.json";
    std::uint64_t period_us = 1000;      // 1kHz；配置有效时以配置为准
    bool period_overridden_by_cli = false;
    int duration_s = 0;                  // 0 = 直到收到信号
};

// 实时线程只读的参数快照。由启动阶段一次性填入，运行期不再分配。
km3::ConfigSnapshot<km3::RtParams> g_rt_params{km3::RtParams{}};

Options ParseArgs(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--require-realtime") {
            o.require_realtime = true;
        } else if (arg == "--require-config") {
            o.require_config = true;
        } else if (arg == "--config" && i + 1 < argc) {
            o.config_path = argv[++i];
        } else if (arg == "--period-us" && i + 1 < argc) {
            o.period_us = std::strtoull(argv[++i], nullptr, 10);
            o.period_overridden_by_cli = true;
        } else if (arg == "--duration-s" && i + 1 < argc) {
            o.duration_s = std::atoi(argv[++i]);
        } else if (arg == "--help" || arg == "-h") {
            std::printf("usage: %s [--config PATH] [--require-realtime] [--require-config]"
                        " [--period-us N] [--duration-s N]\n", argv[0]);
            std::exit(0);
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", arg.c_str());
            std::exit(2);
        }
    }
    if (o.period_us == 0) {
        o.period_us = 1000;
    }
    return o;
}

int Fail(const char* what, km3::ErrorCode code) {
    std::fprintf(stderr, "km3-rt-controller: %s failed (%s)\n", what, km3::ToString(code));
    return 3;
}

}  // namespace

int main(int argc, char** argv) {
    Options opts = ParseArgs(argc, argv);

    std::signal(SIGINT, HandleSignal);
    std::signal(SIGTERM, HandleSignal);

    std::printf("km3-rt-controller starting\n");

    // ---- 配置：加载 → 校验 → 填入实时快照 ----
    //
    // 实时线程绝不解析 JSON：那会分配内存、需要加锁，且异常被吞掉后会
    // 静默用默认值继续运动，比崩溃更危险。这里在启动阶段一次性完成，
    // 之后周期循环只读原子快照。
    {
        const auto cfg = km3::LoadConfigFile(opts.config_path);
        if (!cfg.ok()) {
            std::printf("  config      : %s (%s)\n",
                        km3::ToString(cfg.code()), opts.config_path.c_str());
            if (opts.require_config) {
                std::fprintf(stderr,
                    "km3-rt-controller: --require-config 已指定但配置不可用，退出。\n"
                    "  用默认参数运行控制回路是危险配置，不应静默继续。\n");
                return Fail("config load", cfg.code());
            }
            std::printf("  → 以默认参数继续（仅供开发验证；生产必须传 --require-config）\n");
        } else {
            const km3::Status v = km3::ValidateConfig(cfg.value());
            if (!v.ok()) {
                std::printf("  config      : INVALID (%s) %s\n",
                            km3::ToString(v.code()), opts.config_path.c_str());
                if (opts.require_config) {
                    std::fprintf(stderr,
                        "km3-rt-controller: 配置校验未通过，拒绝以默认值继续。\n"
                        "  常见原因：device.id 仍是 REPLACE 占位、server_url 缺 /api/v1、\n"
                        "  data_root 落在根分区、看门狗阈值层级颠倒。\n");
                    return Fail("config validate", v.code());
                }
                std::printf("  → 忽略校验结果继续（仅供开发验证）\n");
            } else {
                std::printf("  config      : ok  (%s)\n", opts.config_path.c_str());
                // 发布到实时快照
                g_rt_params.Publish(cfg.value().rt);
                if (!opts.period_overridden_by_cli) {
                    opts.period_us = cfg.value().rt.control_period_us;
                }
                std::printf("  device id   : %s\n", cfg.value().device_id.c_str());
                std::printf("  data root   : %s\n", cfg.value().storage.data_root.c_str());
            }
        }
    }
    const km3::RtParams rt_params = g_rt_params.Get();
    std::printf("  period      : %llu us\n", (unsigned long long)opts.period_us);
    std::printf("  watchdog    : direct=%llu ms relay=%llu ms\n",
                (unsigned long long)(rt_params.watchdog_direct_us / 1000),
                (unsigned long long)(rt_params.watchdog_relay_us / 1000));
    std::printf("  late thresh : %llu us (周期/10；超阈值才计入 late ticks)\n",
                (unsigned long long)(opts.period_us / 10 > 0 ? opts.period_us / 10 : 1));
    std::printf("  ring caps   : state=%zu sample=%zu safety=%zu\n",
                g_state_ring.Capacity(), g_sample_ring.Capacity(), g_safety_ring.Capacity());

    // 取时钟会触发一次性初始化，必须在实时循环之前完成，避免首周期抖动。
    km3::WarmUpClock();

    // ---- 实时环境设置 ----
    km3::RealtimeConfig rt;
    rt.priority = 90;
    rt.cpus = {4, 5, 6, 7};        // A76 大核簇（实测依据见 实测基线.md §二）

    const km3::Status rt_status = km3::ConfigureRealtimeThread(rt);
    if (rt_status.ok()) {
        std::printf("  realtime    : ok\n");
    } else {
        std::printf("  realtime    : FAILED (%s)\n", km3::ToString(rt_status.code()));
        std::printf("                环境需要 CAP_SYS_NICE + CAP_IPC_LOCK\n");
        std::printf("                （systemd: AmbientCapabilities=CAP_SYS_NICE CAP_IPC_LOCK）\n");
        if (opts.require_realtime) {
            std::fprintf(stderr,
                         "km3-rt-controller: --require-realtime 已指定但实时设置失败，退出。\n"
                         "  以非实时方式运行实时进程是危险配置，不应静默继续。\n");
            return Fail("realtime setup", rt_status.code());
        }
        std::printf("  → 以非实时方式继续（仅供开发验证；生产必须传 --require-realtime）\n");
    }
    std::printf("  thread      : %s\n", km3::DescribeCurrentThread().c_str());

    // ---- 启动边界线程（非实时）----
    std::thread boundary(BoundaryLoop, std::ref(g_running));

    // ---- 实时周期循环 ----
    //
    // 时基用绝对推进（DeadlineSchedule 内部 next += period），
    // 因此每一周期的抖动不会累积成长期漂移。
    km3::PeriodicTimer timer(opts.period_us);
    const std::uint64_t start_us = km3::MonotonicUs();
    std::uint64_t last_report_us = start_us;

    km3::MotionSnapshot snapshot;
    while (g_running.load(std::memory_order_acquire)) {
        timer.WaitNext();

        const std::uint64_t now = km3::MonotonicUs();

        // ---- 实时工作（此处只放采样与控制计算）----
        // TODO: 接入 HAL 后在此读取反馈并执行控制律。
        snapshot.ts_us = now;
        snapshot.position_mm = 0.0F;
        snapshot.speed_mps = 0.0F;
        snapshot.subsystem = static_cast<std::uint8_t>(km3::Subsystem::Climb);

        // 满时不阻塞：丢弃并计数，由边界线程与健康指标负责暴露。
        g_state_ring.Push(snapshot);

        if (opts.duration_s > 0 && now - start_us >= static_cast<std::uint64_t>(opts.duration_s) * 1000000ULL) {
            break;
        }

        // 周期性打印统计。这本身会调用 printf（非实时友好），
        // 因此只按秒打印，且这是骨架阶段的临时手段；
        // 正式实现应把统计压入环形缓冲，由边界线程输出。
        if (now - last_report_us >= 1000000ULL) {
            last_report_us = now;
            std::printf("  [%llus] ticks=%llu overrun=%llu late=%llu max_late=%lluus drop=%llu\n",
                        (unsigned long long)((now - start_us) / 1000000ULL),
                        (unsigned long long)timer.Schedule().Ticks(),
                        (unsigned long long)timer.Schedule().Overruns(),
                        (unsigned long long)timer.Schedule().LateTicks(),
                        (unsigned long long)timer.Schedule().MaxLateUs(),
                        (unsigned long long)g_state_ring.Dropped());
        }
    }

    g_running.store(false, std::memory_order_release);
    boundary.join();

    // ---- 收尾报告 ----
    const auto& sched = timer.Schedule();
    std::printf("\nkm3-rt-controller stopped\n");
    std::printf("  ticks        : %llu\n", (unsigned long long)sched.Ticks());
    std::printf("  overruns     : %llu\n", (unsigned long long)sched.Overruns());
    std::printf("  late ticks   : %llu (阈值 %llu us；并非\"迟到比例\")\n",
                (unsigned long long)sched.LateTicks(),
                (unsigned long long)sched.LateThresholdUs());
    std::printf("  max late     : %llu us\n", (unsigned long long)sched.MaxLateUs());
    std::printf("  dropped      : state=%llu sample=%llu safety=%llu\n",
                (unsigned long long)g_state_ring.Dropped(),
                (unsigned long long)g_sample_ring.Dropped(),
                (unsigned long long)g_safety_ring.Dropped());

    if (g_state_ring.Dropped() > 0 || g_sample_ring.Dropped() > 0) {
        std::printf("  WARNING: 存在丢弃记录，边界线程可能跟不上实时节奏\n");
    }
    return 0;
}
