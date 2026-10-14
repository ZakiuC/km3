// km3-core：非实时进程。承载网络、配置、日志、OTA、数据采集。
// 绑定 CPU 0-3（A55 小核）。见 docs/架构设计/设备端程序架构.md §1.2。
//
// 关键职责（待实现）：
//   - G30Connection / ServerConnection / TunnelRelay（四条路径，见通信协议规范 §一）
//   - CommandRouter：命令分发与看门狗随通道调整（§八）
//   - ConfigStore：启动时快照为 POD，原子换指针供实时进程读取
//   - LogService、OTAManager、DataAggregator、LocalStorage、Uploader

#include <atomic>
#include <csignal>
#include <cstdio>
#include <thread>

namespace {
std::atomic<bool> g_running{true};
void HandleSignal(int) { g_running.store(false); }
}  // namespace

int main() {
    // systemd 下 stdout 是**块缓冲**（fd 1 是 socket），printf 的内容会滞留在
    // 缓冲区里，直到进程退出才落盘 —— 表现为"启动信息在 journal 里查不到"，
    // 而直接前台运行同一个二进制却能看到（实测于 TL3588，2026-10-14）。
    // 服务进程的启动日志必须立即可见，否则现场排障只能靠猜。
    std::setvbuf(stdout, nullptr, _IOLBF, 0);

    std::signal(SIGINT, HandleSignal);
    std::signal(SIGTERM, HandleSignal);

    std::printf("km3-core started (skeleton)\n");
    std::printf("  channel priority: direct -> relay (fallback)\n");
    std::printf("  storage root    : /data  (must be mounted; see 实测基线.md)\n");

    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
    std::printf("km3-core stopped\n");
    return 0;
}
