# pmp4010 — 机身抬降电机 A 驱动

PMP4010 ×4 的 CANopen 驱动骨架与板载调试 TUI。挂主板第二路 CAN，逻辑名 **`can-2`**
（物理标识只在 `device/config/device.json`，本目录任何文件不得出现物理接口名）。

**当前状态：骨架。** 厂家手册尚未提供，`driver/od.json` 里每一条索引、位定义、
标度、周期全部是 `status=unverified` / `cite=null`。驱动因此被 `kOdVerified=false`
挡住，一个字节都不会发到总线上——这不是待办，是当前的正确行为。

## 职责

- 把「机身抬降电机 A」的物理语义（位置 / 速度 / 使能 / 回零）映射到 CANopen 报文。
- 持有 4 台从站的对象字典**声明**（哪些条目被用到、各自置信度如何），
  让「未确认项」在代码里显式可见，而不是散落在注释里。
- 提供 `tools/pmp4010.py`：板载 TUI，用于 `--init` 写总线参数、监听总线、
  查总线状态。工具**只读优先**（`--scan` / `--od-dump` / `--bus-stats` / `--raw`），
  发帧类操作（`--nmt` / `--motion`）在 `km3-rt-controller` 为 active 时
  必须带 `--i-know-rt-is-running`，否则拒绝执行。

## 接口

| 文件 | 内容 |
| --- | --- |
| `driver/pmp4010_driver.hpp` | `Pmp4010Driver`：`Open` / `Close` / `ProbeNode` / `SendNmt` / `Enable` / `MoveTo` / `SetVelocity` / `Stop` / `Home` / `SdoRead` / `SdoWrite` |
| `driver/od_generated.hpp` | **生成物**（`tools/hwdriver/gen-od.py` 从 `od.json` 产出，首行 DO NOT EDIT）。带 `PMP4010_OD_VERIFIED` 宏与 `#error` 守卫；驱动转发其 `od::kOdVerified` |
| `driver/od.json` | 对象字典**声明**：每条的 `index`/`sub`/`bits`/`scale`/`status`/`cite` |

命名遵循 `docs/架构设计/开发规范.md §1.2`：类/函数 PascalCase，变量 snake_case，
成员变量后缀下划线。

## 依赖

只链 4 个目标，不得引入其他依赖：

```
km3_core        实时原语与基础类型（Result / Status / 时间源）
km3_hal         ICanDevice 抽象（驱动只认逻辑名，不认 /dev/* 与内核接口名）
km3_canopen     CANopen 协议栈（SDO / PDO / NMT / EMCY）—— 由协议模块提供
km3_driver_api  驱动统一生命周期接口 —— 由驱动框架提供
```

`km3_canopen` 与 `km3_driver_api` 在本骨架落地时**尚不存在**（另有人实现）。
`CMakeLists.txt` 里用 `if(NOT TARGET ...)` 守卫，缺失时打一条 STATUS 消息而不是
配置失败——否则本目录会挡住整个设备端工程。守卫不是"以后可以删的临时代码"，
它是这张表与当前仓库状态的**差异声明**：谁先落地谁去掉自己那一半。

硬约束：**本驱动不进实时路径**（见 `CONTRACT.md` 的「RT 可达性」节）。

`driver/od_generated.hpp` 由 `tools/hwdriver/gen-od.py` 从 `driver/od.json` 生成
（同时产出板端工具侧的 `tools/hwtui/canopen_consts.py`），**不得手改**。
它默认有毒：od.json 里 25 条仍未核对，于是生成物里是一个 `#error`，本驱动编不过。
仅 CI 与未接电机的回环验证可以带 `-DKM3_ALLOW_UNVERIFIED_OD=1` 旁路。

## 测试方式

`od_generated.hpp` 默认是**有毒的**（od.json 里 25 条仍为 `unverified`），
所以除了 CI 与未接电机的回环验证，本驱动**编不过**。要跑测试必须显式带旁路开关：

```sh
# 1) 语法（交叉编译器，不链接；不动 device/CMakeLists.txt 的主阶梯）
#    不带旁路开关 -> 必须失败，且错误来自 od_generated.hpp 的 #error
aarch64-linux-gnu-g++ -std=c++17 -Wall -Wextra -Wpedantic -Wshadow -Wconversion \
  -fsyntax-only -I device/include -I common/include -I device/tests \
  -I device/drivers/pmp4010/driver \
  device/drivers/pmp4010/driver/pmp4010_driver.cpp
# 期望：error: #error "pmp4010 对象字典未验证（driver/od.json 里仍有 25 条 ...）"

# 1b) 带旁路开关 -> 必须成功（这就是闸门两个方向都有效的证据）
aarch64-linux-gnu-g++ -std=c++17 -Wall -Wextra -Wpedantic -Wshadow -Wconversion \
  -DKM3_ALLOW_UNVERIFIED_OD=1 -c \
  -I device/include -I common/include -I device/drivers/pmp4010/driver \
  device/drivers/pmp4010/driver/pmp4010_driver.cpp -o /tmp/pmp4010.o

# 2) 单测（用仓库既有的零依赖骨架 + MockCanDevice）
g++ -std=c++17 -O0 -DKM3_ALLOW_UNVERIFIED_OD=1 \
  -I device/include -I common/include -I device/tests \
  -I device/drivers/pmp4010/driver \
  device/drivers/pmp4010/tests/test_pmp4010.cpp \
  device/drivers/pmp4010/driver/pmp4010_driver.cpp \
  device/src/hal/mock_devices.cpp device/src/common/device_kind.cpp \
  device/src/common/clock.cpp common/src/error_code.cpp \
  -o /tmp/test_pmp4010 -lpthread && /tmp/test_pmp4010

# 3) 生成物漂移检查（改 od.json 后必须重新生成，CI 就查这一条）
python3 tools/hwdriver/gen-od.py --model pmp4010 --check

# 4) 解耦与溯源检查（CI 的 drivers job 跑的就是这两个）
bash tools/check-drivers.sh
bash tools/check-manual-citations.sh

# 5) TUI
python3 -m py_compile device/drivers/pmp4010/tools/pmp4010.py
python3 device/drivers/pmp4010/tools/pmp4010.py --test      # 不带硬件的自检
scp device/drivers/pmp4010/tools/pmp4010.py root@192.168.0.188:/tmp/
ssh root@192.168.0.188 'python3 /tmp/pmp4010.py --init --verify'   # 纯只读
```

测试断言的**核心一条**：`kOdVerified` 为 false 时，所有运动方法返回
`EDeviceError` 且 **`MockCanDevice::SentFrames()` 为空**。索引未知就发帧，
也就是拿一台真实电机做盲试；这条断言是它的门闩。

### 工具子命令与权限

| 子命令 | 类别 | `km3-rt-controller` active 时 |
| --- | --- | --- |
| `--init --verify` | 纯只读 | 不受限 |
| `--test` | 只读自检 | 不受限 |
| `--scan` | 只读（被动监听，**不发帧**） | 不受限 |
| `--od-dump` | 只读 | 不受限 |
| `--bus-stats` | 只读 | 不受限 |
| `--raw` | 只读（只监听） | 不受限 |
| `--init`（不带 `--verify`） | 写 `/etc/km3/can.env` | 不受限（改的是下一次启动的取值） |
| `--nmt` | **发帧** | 需 `--i-know-rt-is-running` |
| `--motion` | **发帧** | 需 `--i-know-rt-is-running`；且因对象字典未验证而**始终拒绝执行** |

`--nmt` / `--motion` 在没有该确认时返回 4 且**零帧发出**。状态取不到时
（`systemctl` 不可用）按**更严**一侧处理，同样要求确认——未知不等于安全。

CANopen 的帧组装与 NMT 状态机来自标准层 `tools/hwtui/canopen.py`（`NmtMaster` /
`nmt_command_frame`），本工具不重复实现；标准层不可用时退化到内置的 SocketCAN
收发，并在输出里明说"正在使用降级实现"。

## 资料索引

未确认项的完整清单、以及「放哪份文件能消解哪一条」，见
[`docs/硬件手册/PMP4010/README.md`](../../../docs/硬件手册/PMP4010/README.md)。
该目录是资料的唯一入口（协议原文放其 `协议/` 子目录）；本目录不复制资料内容，
只引用条目编号。

| 手册目录的条目 | 落到本目录的什么 |
| --- | --- |
| A1–A6（对象字典 / 模式码 / 标度 / 心跳 / EMCY / 固件版本） | `driver/od.json` 的对应条目 → 把 `status` 改为 `verified`、补 `cite` |
| B7（PDO 映射） | `CONTRACT.md`「阻塞项」；决定 1 ms 周期是否成立 |
| C8（EDS / DCF） | 有 EDS 时应改为**由 EDS 生成** `od_generated.hpp`，不再手抄 |
| D9–D12（节点号 / 上电状态 / 终端电阻 / CiA 规范号） | 节点号进配置；上电状态与终端电阻进 `CONTRACT.md` |
| E13（can-2 上 8 节点带宽预算） | 设计归属，不在本目录 |
