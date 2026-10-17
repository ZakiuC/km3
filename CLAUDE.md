# km3 — 项目约定（面向 agent）

## 这是什么

km3 是一套墙面/绳索作业机器人系统。三端：

- **设备端**：TL3588 主板（RK3588，Ubuntu 22.04 + PREEMPT_RT），C++17，原生部署，是被控机器人。
- **上位机端**：云卓 G30，Android 原生应用（Kotlin）。
- **服务器端**：阿里云 VPS，C++，原生部署。

## 目录

| 路径 | 内容 |
| --- | --- |
| `docs/` | 需求、硬件清单、主板技术资料、架构设计 |
| `docs/架构设计/` | 三端架构与协议的唯一权威来源 |
| `device/` | 设备端 C++ 工程 |
| `server/` | 服务器端 C++ 工程 |
| `android/` | 上位机 Android 工程 |
| `tools/` | 主机侧安装与维护脚本 |
| `harness/` | **用户独自维护的层级。agent 不得读取、创建、修改或删除其中的任何内容。** |

## 硬性规则

1. **不要动 `harness/`。** 它不在 agent 的职责范围内，`.gitignore` 也已排除。
2. **先读文档再改代码。** `docs/架构设计/` 是设计权威；发现文档与实现对不上时，先报告差异，不要擅自改文档。
3. **人类指挥、决策；agent 落地、执行。** 架构决策、硬件调试、现场验证由人类负责；代码实现、测试、文档由 agent 负责。
4. **设备端实时路径有纪律**：实时线程内不得做动态分配、文件 IO、日志格式化、网络重连或加锁等待。跨实时/非实时边界用预分配的无锁环形缓冲。
5. **C++ 代码遵循 `docs/架构设计/开发规范.md`**：类名 PascalCase、函数 PascalCase、变量 snake_case、成员变量后缀下划线。
6. **不要提交产物**：`build*/`、`*.pb.cc`、`android/app/build/` 等已在 `.gitignore` 中。
7. **实现必须派出去，主会话不亲手改业务代码。** 目标是让便宜 agent 长出能力，
   不是让贵的那个更快。判据：**改动超过 1 个文件，或涉及发帧 / 电机 / 协议语义 —— 派出去。**
   主会话只做四件：**只读侦察**、**写任务书**、**独立验收**、**台账与文档**。
   路径见 `docs/架构设计/系统改动开发路径.md`（系统改动）与
   `docs/架构设计/硬件驱动开发路径.md`（新增硬件型号）。
   本会话的失败模式：路径只覆盖"新增型号"一种形状，其余形状默认落回主会话，
   而主会话接受了这个默认 —— **遇到没有路径的形状，先补路径，不要自己顶上。**

## 构建（主机为 x86_64）

设备端交叉编译：

```sh
# 注意：工具链文件必须是绝对路径。CMake 会把相对路径解析到**构建目录**下，
# 因此 `-DCMAKE_TOOLCHAIN_FILE=device/cmake/...` 会报 "Could not find toolchain file"。
cmake -S device -B build-arm64 \
      -DCMAKE_TOOLCHAIN_FILE="$PWD/device/cmake/aarch64-linux-gnu.cmake" \
      -DCMAKE_BUILD_TYPE=Release -DKM3_SANITIZER=none
cmake --build build-arm64 -j"$(nproc)"
cmake --build build-arm64 -t test_spsc_ring && ctest --test-dir build-arm64
```

设备端本机测试：

```sh
cmake -S device -B build-host -DCMAKE_BUILD_TYPE=Debug
cmake --build build-host -j"$(nproc)" && ctest --test-dir build-host --output-on-failure
```

服务器端：

```sh
cmake -S server -B build-server -DCMAKE_BUILD_TYPE=Debug
cmake --build build-server -j"$(nproc)" && ctest --test-dir build-server --output-on-failure
```

测试板：`ssh root@192.168.0.188`（空密码，测试机；正式部署需改为密钥登录并弃用 root）。

## 待办约束

- 视频参数（分辨率、帧率、编码、码率）尚未定，相关代码一律走可配置项，不要写死。
- 录像默认关闭，开启后需有容量与保留策略。
- 板端根分区余量很小，不要把日志、模型或录像写进根分区。
