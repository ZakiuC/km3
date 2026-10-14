# device — 设备端（TL3588）

C++17，原生部署（systemd），交叉编译到 aarch64。
架构见 `docs/架构设计/设备端程序架构.md`，实时纪律见 `docs/架构设计/开发规范.md §1.4`。

## 构建

```sh
# 本机（跑单元测试）
cmake -S device -B build-host -DCMAKE_BUILD_TYPE=Debug
cmake --build build-host -j && ctest --test-dir build-host --output-on-failure

# 注意：build-host 仅用于设备端本机测试；服务器端用 build-server（见 server/README.md），
# 不要共用同一目录，否则第二份 CMake 缓存会覆盖第一份。

# 交叉编译到设备（工具链路径必须是绝对路径）
cmake -S device -B build-arm64 \
      -DCMAKE_TOOLCHAIN_FILE="$PWD/device/cmake/aarch64-linux-gnu.cmake" \
      -DCMAKE_BUILD_TYPE=Release -DKM3_SANITIZER=none
cmake --build build-arm64 -j
```

`-DKM3_SANITIZER=` 可选 `address`（ASan+UBSan）、`thread`（TSan）、`none`。
两者互斥，必须二选一。

## 结构

| 路径 | 内容 |
| --- | --- |
| `include/km3/spsc_ring.hpp` | 实时/非实时边界的无锁环形缓冲（**核心原语**） |
| `include/km3/rt_types.hpp` | 可进环形缓冲的定长 POD 记录 |
| `src/apps/rt-controller` | 实时控制进程（绑 CPU4-7，SCHED_FIFO） |
| `src/apps/core` | 网络 / 配置 / 日志 / OTA / 采集（绑 CPU0-3） |
| `src/apps/video` | 视频采集与编码（独立，因会背压） |
| `proto/km3.proto` | 与前端共用的协议定义 |
| `deploy/systemd/` | 四个 systemd 单元 |
| `deploy/70-km3-serial.rules` | 串口权限，按设备身份授予 |
| `config/device.json` | 配置模板 |

## 实时路径规则（违反即失去实时保证）

RT 线程内禁止：动态分配、加锁、文件 IO、日志格式化、网络收发、序列化、相对睡眠。
跨边界只走预分配无锁环形缓冲；满时丢弃并计数，**不得静默丢弃**。

## 硬件事实（详见 `docs/架构设计/实测基线.md`）

- CPU0–3 是 A55，**CPU4–7 是 A76**；实时绑大核。
- 主板 CAN 仅 CAN 2.0B，**不支持 CAN FD**；CANfd 设备须走 USB 转 CAN。
- 根分区仅剩 4.2 GiB，日志/缓存/录像必须落 `/data`。
- 摄像头、USB-RS485、USB-CAN 模块**当前均未接入**。

## 状态

骨架可构建、可测。HAL、总线驱动、网络层待实现。
