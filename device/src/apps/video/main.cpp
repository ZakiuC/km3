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
