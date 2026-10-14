// km3-video：视频采集与编码。独立进程，因视频会背压，需与控制面做资源隔离。
//
// 重要：分辨率、帧率、像素格式、路数全部未定案，且实测 6 路摄像头当前并未接入。
// 参数必须来自配置，不得写死（见 docs/架构设计/设备端程序架构.md §2.3.2）。
// 带宽约束：USB2.0 HUB 与 4G/WiFi/串口模块共享上行，未压缩 YUYV 1080p30 单路即超限。

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

    std::printf("km3-video started (skeleton)\n");
    std::printf("  recording: disabled by default (config: storage.recording_enabled)\n");
    std::printf("  NOTE: resolution/fps/format undecided; must come from config\n");

    while (g_running.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
    std::printf("km3-video stopped\n");
    return 0;
}
