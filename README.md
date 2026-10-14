# km3

墙面/绳索作业机器人系统。四类客户端协同：

| 端 | 硬件 | 技术栈 | 角色 |
| --- | --- | --- | --- |
| 设备端 | TL3588（RK3588，8GB/64GB eMMC，Ubuntu 22.04 + PREEMPT_RT） | C++17，原生部署 | 被控机器人 |
| 上位机端 | 云卓 G30 | Android 原生（Kotlin） | 操作控制台 |
| 服务器端 | 阿里云 VPS | C++，原生部署 | 后端能力 |
| 管理端 | 浏览器 | TypeScript + Vue 3，静态托管 | 后台：总览、日志、用户、固件下发 |

## 仓库结构

```
km3/
├── docs/架构设计/           设计权威（单一事实来源），9 份文档
├── docs/                    需求、硬件清单、主板技术资料
├── common/                  C++ 共享层：错误码、Result（设备端与服务器端共用）
├── device/                  设备端 C++ 工程（交叉编译 aarch64）
│   ├── include/km3/         spsc_ring.hpp（实时边界）、rt_types.hpp
│   ├── proto/km3.proto      与前端共用的协议定义
│   ├── src/apps/            rt-controller / core / video
│   ├── src/{hal,rt-control,middleware,services}/
│   ├── tests/               单元测试（零依赖骨架 + 将来可换 GTest）
│   ├── deploy/              systemd 单元、udev 规则
│   └── config/device.json   配置模板
├── server/                  服务器端 C++ 工程（原生部署）
│   ├── include/km3/server/  permission.hpp（权限矩阵）
│   ├── migrations/          编号 SQL（PostgreSQL 分区表）
│   ├── deploy/systemd/      km3-server.service
│   └── config/config.json
├── android/                 上位机 Android 工程（Kotlin + Compose）
├── admin-web/               管理页面 Web 工程（TypeScript + Vue 3）
├── tools/                   安装、部署、固件签名脚本
├── .github/workflows/       CI（双消毒器矩阵 + 文档引用检查）
└── harness/                 用户独自维护，agent 不介入
```

## 文档

设计权威在 `docs/架构设计/`：

- `系统总体架构.md` — 分层、网络拓扑、技术选型
- `设备端程序架构.md` — 实时控制、进程划分、硬件归属
- `服务器端程序架构.md` — 服务、数据模型、API
- `前端程序架构.md` — Android 应用架构
- `通信协议规范.md` — 三端之间的协议
- `管理端Web程序架构.md` — 管理页面（Web）技术栈、工程结构、认证与边界
- `开发规范.md` — 编码规范、Git 流程、测试与 CI
- `README.md` — 架构摘要与实施计划

## 主机环境

设备端交叉编译（aarch64）：

```sh
# 注意：工具链文件必须是绝对路径。CMake 会把相对路径解析到**构建目录**下，
# 因此 `-DCMAKE_TOOLCHAIN_FILE=device/cmake/...` 会报 "Could not find toolchain file"。
cmake -S device -B build-arm64 \
      -DCMAKE_TOOLCHAIN_FILE="$PWD/device/cmake/aarch64-linux-gnu.cmake" \
      -DCMAKE_BUILD_TYPE=Release -DKM3_SANITIZER=none
cmake --build build-arm64 -j"$(nproc)"
cmake --build build-arm64 -t test_spsc_ring && ctest --test-dir build-arm64
```

客户端本机测试（x86_64，ASan+UBSan）：

```sh
cmake -S device -B build-host -DCMAKE_BUILD_TYPE=Debug
cmake --build build-host -j"$(nproc)"
ctest --test-dir build-host --output-on-failure
```

服务器端（x86_64 原生）：

```sh
cmake -S server -B build-server -DCMAKE_BUILD_TYPE=Debug
cmake --build build-server -j"$(nproc)"
ctest --test-dir build-server --output-on-failure
```

依赖安装：

```sh
sudo bash tools/install-deps.sh   # C++ 工具链与库
bash tools/android-setup.sh       # Android（需 JDK 17 + SDK 34，脚本会打印命令）
```

测试板：`ssh root@192.168.0.188`（测试机，空密码；正式部署改用密钥并弃用 root）。

## 当前状态

- [x] 需求与硬件资料归档
- [x] 架构设计文档：三端 + 协议 + OTA + 规范 + 实测基线（9 份）
- [x] 架构文档三方评审与修订（硬件事实、需求覆盖、跨文档一致性）
- [x] 工作区骨架、git 仓库、忽略规则（`harness/` 已排除）
- [x] 交叉编译工具链、依赖脚本、`.clang-format` / `.clang-tidy`
- [x] 设备端骨架：实时边界环形缓冲、三个进程入口、systemd 单元
- [x] 服务器端骨架：权限矩阵、数据库迁移、systemd 单元
- [x] 上位机骨架：Manifest、帧编解码、通道模型、共用 proto
- [x] CI：双消毒器矩阵（ASan/UBSan 与 TSan）+ 文档引用检查
- [x] 实时原语：单调时钟、绝对推进时基、实时环境设置、配置快照
- [x] 配置加载与校验：模板占位拒绝、路径与看门狗层级校验、RT 只读快照
- [x] 实时控制律与安全监控：越限准入、CANfd 能力边界、看门狗随通道重置
- [x] 硬件抽象层：CAN / RS485 接口与模拟设备（含能力边界测试）
- [x] 路径 A 帧编解码（设备端 C++ + 前端 Kotlin，黄金向量一致）
- [x] 固件离线签名工具（Ed25519）
- [x] 管理端 Web 骨架：权限矩阵、请求层、环境切换、日志实时流（B10/B11 占位待口径）
- [x] 实机首次实时性测量（空载 1 µs 抖动）
- [ ] 设备端：真实总线后端（SocketCAN / 串口）、控制律、网络四路径
- [ ] 服务器端：Asio/Beast 接入、命令队列、认证
- [ ] 上位机：导航与业务页面
- [ ] 实机验证：千兆链路、摄像头带宽、看门狗跳线、CPU 隔离

## 构建验证

设备端与服务器端均已通过以下配置的实际构建与测试：

| 配置 | 结果 |
| --- | --- |
| device host Debug（ASan+UBSan） | 7/7 测试通过（1424 项断言） |
| server host Debug（ASan+UBSan） | 1/1 测试通过（29 项断言） |
| device aarch64 Release | 三个 ARM aarch64 ELF 产出 |
| device TSan | 7/7 测试通过 |
| 实机 1 kHz 周期抖动 | 10000 周期，最大迟到 **1 µs**，0 overrun（空循环体） |
| 帧协议跨实现一致性 | C++ 与 Kotlin 共用黄金向量，逐字节一致 |
| 固件签名 | Ed25519 生成/签名/验签/篡改拒绝 全部通过 |

**实时性结论的边界**：1 µs 是**空循环体**下的周期唤醒抖动，
不等于控制回路执行时间，也不等于端到端响应。补测要求见
`docs/架构设计/实测基线.md §七`。

## 立即需要用户确认

1. **工时统计口径**（B10）：用户已明确推迟到工程后期。管理端已做接口预留与占位页，
   不做臆造聚合——口径错误的统计被引用比没有统计更危险。
2. **跨环境展示口径**（B11）：同样推迟，处理方式与 B10 一致。
3. ~~管理端 Web 技术栈~~ **已定案**：TypeScript + Vue 3 + Vite + Pinia + Element Plus + ECharts（pnpm），
   见 `docs/架构设计/管理端Web程序架构.md`；骨架在 `admin-web/`。
4. **服务端 Cookie 会话**：管理端要求会话票据走 HttpOnly + Secure + SameSite=Strict Cookie
   （否则一次 XSS 即等于票据泄露），且需要 `GET /environments` 填充环境切换器。
   这两项是对服务端的新增要求，需同步到 `服务器端程序架构.md`。

## 需要实机验证

| 项 | 说明 |
| --- | --- |
| 千兆链路 | eth0 现为 100Mb/s；eth1 未接线 |
| 6 路摄像头带宽 | 摄像头当前未接入，参数未定 |
| MAX6369 看门狗 | J3 跳线状态与超时范围待确认 |
| `isolcpus` 隔离 | 未配置；当前 1 µs 是在无隔离下取得的 |
| 控制回路执行时间 | 循环体为空，完全无数据 |
| 端到端最坏响应 | 需在视频/USB/WiFi/SSD 负载下测 |
| 12 小时长稳 | 当前只有 10 秒样本 |

详见 `docs/架构设计/实测基线.md`。
