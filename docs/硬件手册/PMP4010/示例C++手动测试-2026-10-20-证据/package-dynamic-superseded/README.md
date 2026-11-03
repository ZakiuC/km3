# PMP4010 独立 C++ 手动直线测试

本工具用于人工分阶段的 PP 直线测试。独立构建，不接入正式 driver/HAL。
复用原示例 `nimotion::SocketCan`、`CanBus`、`Servo::sdo()` 和基础读取，
由 `GuardedBus` 与外部受控流程实现目标冻结、审阅确认、Halt 使能、Go、停止、
报警复位与通信隔离；不声称原样复用示例 `configure/enable/move/resetFault` 高层逻辑。
速度换算直接链接现有 `device/drivers/pmp4010/driver/pmp4010_velocity.cpp`，
不另写浮点 rpm 换算，不定义正式驱动的 OD 编译绕过宏。

## 本地构建和离线验证

在仓库根目录运行，宿主默认不启用 sanitizer：

```sh
cmake -S tools/pmp4010_cpp_test -B build-pmp4010-cpp-host -DCMAKE_BUILD_TYPE=Debug -DPMP4010_SANITIZER=none
cmake --build build-pmp4010-cpp-host -j4
ctest --test-dir build-pmp4010-cpp-host --output-on-failure
python3 tools/pmp4010_cpp_test/tests/verify_pty.py --binary build-pmp4010-cpp-host/pmp4010_manual_test
```

ASan 和 TSan 分别使用独立构建目录：

```sh
cmake -S tools/pmp4010_cpp_test -B build-pmp4010-cpp-asan -DCMAKE_BUILD_TYPE=Debug -DPMP4010_SANITIZER=address
cmake --build build-pmp4010-cpp-asan -j4
ctest --test-dir build-pmp4010-cpp-asan --output-on-failure
cmake -S tools/pmp4010_cpp_test -B build-pmp4010-cpp-tsan -DCMAKE_BUILD_TYPE=Debug -DPMP4010_SANITIZER=thread
cmake --build build-pmp4010-cpp-tsan -j4
setarch -R ctest --test-dir build-pmp4010-cpp-tsan --output-on-failure
```

交叉构建只编译，不在宿主执行 ARM64 产物：

```sh
cmake -S tools/pmp4010_cpp_test -B build-pmp4010-cpp-arm64 -DCMAKE_BUILD_TYPE=Release -DPMP4010_SANITIZER=none -DCMAKE_TOOLCHAIN_FILE=/home/zakiu/km3/device/cmake/aarch64-linux-gnu.cmake
cmake --build build-pmp4010-cpp-arm64 -j4
```

仅构建交付程序时可加 `-DBUILD_TESTING=OFF`。`PMP4010_SANITIZER` 只接受
`none/address/thread`，交叉构建必须为 `none`。不需要配置整个设备端工程。

## 使用

```sh
bash tools/pmp4010_cpp_test/run-manual.sh --help
bash tools/pmp4010_cpp_test/run-manual.sh --mock --od-reason "本地模拟流程检查"
```

脚本默认使用 `build-pmp4010-cpp-host/pmp4010_manual_test`，可用环境变量
`PMP4010_CPP_BUILD_DIR` 指定另一构建目录。脚本不构建、不调用 sudo、不配置 CAN。
真实使用必须显式提供已有 `--interface`，由现场人员选定 `--node`、`--mm-per-rev` 等参数。
默认速度为 1 rpm；默认日志写入 `/tmp` 的进程专用路径，可用 `--log` 指定。
`--bitrate` 默认 1000000（1 Mbps），启动检查已有链路，不改接口配置；
真机需 `--exclusive-can-ack` 人工确认 RT 与其他 CAN 控制程序已停止。
工具实例锁只排除本工具的重复实例，不能证明其他程序停止。
单次 SDO 超时用 `--sdo-timeout-ms` 设置，范围为 1..500 ms。
启动仅读；输入参数后 Enter 审阅，再 Enter 执行；Esc 取消；Space/x 停止，d 失能。
z 设置软件零点，不改 `607Ch`；rpm 是 PP 轨迹速度，不是连续速度控制。
隔离节点不得原会话重试；软件停止、失能确认均不证明机械或抱闸停稳。

## OD 未核对项和证据边界

以 `device/drivers/pmp4010/driver/od.json` 的 `entries` 为权威，当前有 7 项非 verified：

| key | status |
| --- | --- |
| node_guard | unverified |
| emcy_cob_id | unverified |
| sync_period | unverified |
| sdo_timeout | unverified |
| stop_option_605BC_missing | unverified |
| brake_failsafe_behaviour | assumed |
| vendor_factory_value_policy | assumed |

操作要求非空人工理由（`--od-reason` 或会话输入），理由只属于本次测试会话，
不改变 OD 状态，也不表示 OD 全验证。CMake 将原始两个示例头与 `od.json` 的 SHA256
编入程序宏，用于追踪构建来源。

本地 `backend_tests`、`bus_preflight_tests` 与 mock PTY 只验证模拟流程；
预检测试使用注入的链路消息，不打开真实 CAN。ARM64 构建只证明交叉编译。
这些结果不替代真实 CAN、固件、负载、抱闸、机械停止或现场安全验收。
逐命令证据位于 `docs/硬件手册/PMP4010/示例C++手动测试-2026-10-20-证据/`；
其中 `record_command.py LABEL PROGRAM ARG...` 保存 argv、真实返回码、原始 stdout/stderr
及命令前后源码哈希。构建期间若源码变动，必须根据对应哈希重新验收。
