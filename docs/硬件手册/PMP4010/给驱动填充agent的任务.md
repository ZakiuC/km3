# 给「驱动填充 agent」的任务（2026-10-15）

## 任务目标

填实 PMP4010 驱动的 C++ 实现与板载运动命令，完成《硬件驱动开发路径.md》的步骤 5-6。

---

## 前置工作已完成（由 GPT 完成）

✓ 手册已归档到 `docs/硬件手册/PMP4010/协议/`  
✓ 对象字典已填充：`device/drivers/pmp4010/driver/od.json`（79 条中 73 verified）  
✓ 生成物已同步：`od_generated.hpp`、`canopen_consts.py`  
✓ 板端只读探测已完成：1 Mbps 通信成功，节点 1 应答  
✓ 完整身份已核对：厂商 ID `0x509`、产品代码 `0x03000001`、设备名 "NiMotion Servo"、固件版本 "V3.3"

**实测电机型号**：PMMP4010B-CANopen-0HEB-004（以实测值为准）

---

## 你要做的两件事

### 任务 1：填实 C++ 驱动（`pmp4010_driver.cpp`）

**现状**：`device/drivers/pmp4010/driver/pmp4010_driver.{hpp,cpp}` 是骨架，方法体都是 `TODO` 或 `return Status(ErrorCode::ENotImplemented)`。

**要做**：把 `od.json` 里 73 条 `verified` 的索引接进真实的 SDO 读写路径，实现 CiA 402 状态机操作。

#### 1.1 理解现有架构

先读这些文件，理解接口约定：

1. **HAL 层**（你要用的底层接口）：
   - `device/include/km3/hal/can_device.hpp`：CAN 抽象接口（`ICanDevice`），提供 `Send()` / `Receive()` / `SetFilters()` / `Stats()`
   - `device/src/hal/mock_devices.cpp`：模拟设备（测试用）

2. **对象字典**（你要用的常量）：
   - `device/drivers/pmp4010/driver/od_generated.hpp`：生成的常量（命名空间 `km3::pmp4010::od`）
   - 结构：`kEntries[]` 数组、`kOdVerified` 标志、`kUnverifiedCount` / `kEntryCount`
   - 每个 entry 有 `index` / `subindex` / `name` / `type` / `access` / `scale` / `verified`

3. **驱动接口**（你要填实的）：
   - `device/drivers/pmp4010/driver/pmp4010_driver.hpp`：驱动对外接口
   - `device/drivers/pmp4010/tests/test_pmp4010.cpp`：契约测试（166 assertions）

4. **模板参考**：
   - `device/drivers/_template/driver/<代号>_driver.{hpp,cpp}`：实现模式参考（虽然也是骨架，但注释完整）

#### 1.2 SDO 读写路径（核心）

**CANopen SDO 帧格式**（见手册 E06:3.2.1 与 `od.json` 的 `sdo_frame_format` 条目）：

SDO 客户端（我们）→ 服务端（电机）：
- COB-ID：`0x600 + 节点号`
- 命令字节（byte 0）：
  - `0x40`：expedited upload（读，≤4 字节）
  - `0x23`：expedited download（写，4 字节）
  - `0x2F`：expedited download（写，1 字节）
  - `0x2B`：expedited download（写，2 字节）
  - 分段传输（>4 字节）：`0x60`（上传）/ `0x21`（下载）

SDO 服务端（电机）→ 客户端（我们）：
- COB-ID：`0x580 + 节点号`
- 应答字节（byte 0）：
  - `0x43`：expedited upload 应答（4 字节数据在 byte 4-7）
  - `0x4F`：expedited upload 应答（1 字节数据在 byte 4）
  - `0x4B`：expedited upload 应答（2 字节数据在 byte 4-5）
  - `0x60`：download 确认
  - `0x80`：中止（错误码在 byte 4-7）

**你要实现的方法**：

```cpp
// 读一个对象
Result<uint32_t> ReadObject(uint16_t index, uint8_t subindex);
Result<std::string> ReadString(uint16_t index, uint8_t subindex);

// 写一个对象
Status WriteObject(uint16_t index, uint8_t subindex, uint32_t value);

// CiA 402 状态机操作（见手册 E06:4.1.1 表4-2）
Status Shutdown();       // 控制字 0x06
Status SwitchOn();       // 控制字 0x07
Status EnableOperation(); // 控制字 0x0F
Status Disable();        // 控制字 0x00
Status QuickStop();      // 控制字 0x02
Status FaultReset();     // 控制字 0x80

// 读状态字（0x6041）
Result<uint16_t> ReadStatusword();

// 设置工作模式（0x6060）
Status SetOperationMode(int8_t mode);
Result<int8_t> GetOperationMode();  // 读 0x6061

// 位置模式
Status SetTargetPosition(int32_t position);  // 写 0x607A
Result<int32_t> GetActualPosition();         // 读 0x6064

// 速度模式
Status SetTargetVelocity(int32_t velocity);  // 写 0x60FF
Result<int32_t> GetActualVelocity();         // 读 0x606C
```

**关键约束**（见《开发规范.md》§1.4）：

1. **驱动不进 RT 线程**：SDO 是请求/应答事务，只能待在边界线程（非实时）
2. **不得动态分配**：预分配缓冲区，避免 `new` / `malloc` / `std::string` 拼接
3. **超时用绝对时钟**：用 `MonotonicUs()` + 绝对推进，不用 `sleep_for`
4. **错误用 `Result` 返回**，不抛异常穿过实时路径

#### 1.3 CiA 402 状态机（见手册 E06:4.1.1）

状态迁移序列（使能电机）：

```
Not ready switch on
  ↓ (自动，或 Shutdown)
Switch on disabled
  ↓ Shutdown (控制字 0x06)
Ready to switch on
  ↓ Switch on (控制字 0x07)
Switched on
  ↓ Enable operation (控制字 0x0F)
Operation enabled  ← 这时才能发运动命令
```

状态判定（读 `0x6041` 状态字，按 E06:4.1.3 表4-5 的掩码）：

```cpp
// 状态字的位定义在 od.json 的 statusword_bits 条目
bool IsReadyToSwitchOn(uint16_t sw) { return (sw & 0x6F) == 0x21; }
bool IsSwitchedOn(uint16_t sw)      { return (sw & 0x6F) == 0x23; }
bool IsOperationEnabled(uint16_t sw) { return (sw & 0x6F) == 0x27; }
bool IsFault(uint16_t sw)           { return (sw & 0x08) != 0; }
```

**你要实现的使能序列**：

```cpp
Status Pmp4010Driver::Enable() {
    // 1. 读状态字
    auto sw_result = ReadStatusword();
    if (!sw_result) return sw_result.status();
    uint16_t sw = sw_result.value();
    
    // 2. 如果是 Fault，先复位
    if (IsFault(sw)) {
        auto st = FaultReset();
        if (!st) return st;
        // 等待状态字变化...
    }
    
    // 3. Shutdown（控制字 0x06 写到 0x6040）
    auto st = WriteObject(0x6040, 0x00, 0x06);
    if (!st) return st;
    // 等待状态字变为 Ready to switch on...
    
    // 4. Switch on（控制字 0x07）
    st = WriteObject(0x6040, 0x00, 0x07);
    if (!st) return st;
    
    // 5. Enable operation（控制字 0x0F）
    st = WriteObject(0x6040, 0x00, 0x0F);
    if (!st) return st;
    
    // 6. 读状态字确认
    sw_result = ReadStatusword();
    if (!sw_result) return sw_result.status();
    if (!IsOperationEnabled(sw_result.value())) {
        return Status(ErrorCode::EDeviceNotReady);
    }
    
    return Status::Ok();
}
```

#### 1.4 编译期闸门（必须保持）

驱动在 `PMP4010_OD_VERIFIED` 为 0 时**不可编译**：

```cpp
#if !PMP4010_OD_VERIFIED && !defined(KM3_ALLOW_UNVERIFIED_OD)
#error "pmp4010 对象字典未验证（driver/od.json 里仍有 6 条 status != verified）。..."
#endif
```

**不要删除这个 `#error`**。运动方法（`SetTargetPosition` / `SetTargetVelocity`）在 `kOdVerified` 为 false 时必须**零字节发出**：

```cpp
Status Pmp4010Driver::SetTargetPosition(int32_t position) {
    if (!od::kOdVerified) {
        // 零字节发出：不调用 WriteObject，不调用 can_device_->Send()
        return Status(ErrorCode::EInvalidConfig, 
                      "对象字典未验证，运动命令被闸门阻止");
    }
    // ... 真实实现
}
```

#### 1.5 验收标准

1. **契约测试通过**：
   ```bash
   cd /tmp && mkdir pmp-driver-test && cd pmp-driver-test
   g++ -std=c++17 -O0 -DKM3_ALLOW_UNVERIFIED_OD=1 \
     -I /path/to/km3/device/include -I /path/to/km3/common/include \
     -I /path/to/km3/device/tests -I /path/to/km3/device/drivers/pmp4010/driver \
     /path/to/km3/device/drivers/pmp4010/tests/test_pmp4010.cpp \
     /path/to/km3/device/drivers/pmp4010/driver/pmp4010_driver.cpp \
     /path/to/km3/device/src/hal/mock_devices.cpp \
     /path/to/km3/device/src/common/*.cpp /path/to/km3/common/src/*.cpp \
     -o test_pmp -lpthread
   ./test_pmp
   # 期望：166 assertions, 0 failures
   ```

2. **不带旁路开关必须编不过**：
   ```bash
   g++ -std=c++17 -fsyntax-only \
     -I ... \
     /path/to/km3/device/drivers/pmp4010/driver/pmp4010_driver.cpp
   # 期望：#error "pmp4010 对象字典未验证..."
   ```

3. **机检全绿**：
   ```bash
   bash tools/check-drivers.sh
   bash tools/check-deps.sh
   ```

---

### 任务 2：填实板载运动命令（`pmp4010.py` 的 `--motion`）

**现状**：`device/drivers/pmp4010/tools/pmp4010.py` 的 `--motion` 子命令只是骨架（`cmd_motion` 函数无条件拒绝）。

**要做**：实现 `--motion enable/disable/stop/home` 四个子命令，调用真实的 CiA 402 序列。

#### 2.1 理解现有工具架构

先读这些文件：

1. **工具框架**：
   - `device/drivers/pmp4010/tools/pmp4010.py`：主工具（已有 `--scan`、`--bus-stats`、`--init` 能跑）
   - `tools/hwtui/canopen.py`：CANopen 协议层（`SdoClient`、`NmtController`）
   - `tools/hwtui/canbus.py`：SocketCAN 封装
   - `tools/hwtui/tui.py`：TUI 框架

2. **模板参考**：
   - `device/drivers/_template/tools/<代号>.py`：工具模板（`--motion` 的骨架在这里）

#### 2.2 要实现的四个子命令

##### 2.2.1 `--motion enable`

使能电机（CiA 402 状态机操作）：

```python
def motion_enable(sdo: SdoClient, node_id: int) -> bool:
    """使能电机：Shutdown -> Switch on -> Enable operation"""
    
    # 1. 读状态字（0x6041）
    sw = sdo.upload(node_id, 0x6041, 0x00, timeout_ms=500)
    if sw is None:
        print("错误：读状态字失败", file=sys.stderr)
        return False
    
    # 2. 如果是 Fault（bit3=1），先复位
    if sw & 0x08:
        print("检测到故障，执行故障复位...", file=sys.stderr)
        if not sdo.download(node_id, 0x6040, 0x00, 0x80, size=2, timeout_ms=500):
            print("错误：故障复位失败", file=sys.stderr)
            return False
        time.sleep(0.1)
    
    # 3. Shutdown（控制字 0x06）
    if not sdo.download(node_id, 0x6040, 0x00, 0x06, size=2, timeout_ms=500):
        print("错误：Shutdown 失败", file=sys.stderr)
        return False
    time.sleep(0.05)
    
    # 4. Switch on（控制字 0x07）
    if not sdo.download(node_id, 0x6040, 0x00, 0x07, size=2, timeout_ms=500):
        print("错误：Switch on 失败", file=sys.stderr)
        return False
    time.sleep(0.05)
    
    # 5. Enable operation（控制字 0x0F）
    if not sdo.download(node_id, 0x6040, 0x00, 0x0F, size=2, timeout_ms=500):
        print("错误：Enable operation 失败", file=sys.stderr)
        return False
    time.sleep(0.05)
    
    # 6. 读状态字确认（bit0-3 应为 0x7）
    sw = sdo.upload(node_id, 0x6041, 0x00, timeout_ms=500)
    if sw is None or (sw & 0x6F) != 0x27:
        print(f"错误：状态字 0x{sw:04X} 不是 Operation enabled", file=sys.stderr)
        return False
    
    print("✓ 电机已使能（Operation enabled）")
    return True
```

##### 2.2.2 `--motion disable`

禁用电机（控制字 0x00，回到 Switch on disabled）：

```python
def motion_disable(sdo: SdoClient, node_id: int) -> bool:
    """禁用电机：控制字 0x00"""
    if not sdo.download(node_id, 0x6040, 0x00, 0x00, size=2, timeout_ms=500):
        print("错误：Disable 失败", file=sys.stderr)
        return False
    print("✓ 电机已禁用")
    return True
```

##### 2.2.3 `--motion stop`

快速停机（控制字 0x02，Quick stop）：

```python
def motion_stop(sdo: SdoClient, node_id: int) -> bool:
    """快速停机：控制字 0x02"""
    if not sdo.download(node_id, 0x6040, 0x00, 0x02, size=2, timeout_ms=500):
        print("错误：Quick stop 失败", file=sys.stderr)
        return False
    print("✓ 电机已快速停机")
    return True
```

##### 2.2.4 `--motion home`

回零（工作模式 6，回零方法见 E06:6.12）：

```python
def motion_home(sdo: SdoClient, node_id: int) -> bool:
    """回零：设置工作模式 6（Homing mode），执行回零序列"""
    
    # 1. 先使能（如果未使能）
    sw = sdo.upload(node_id, 0x6041, 0x00, timeout_ms=500)
    if sw is None or (sw & 0x6F) != 0x27:
        print("电机未使能，先执行使能...", file=sys.stderr)
        if not motion_enable(sdo, node_id):
            return False
    
    # 2. 设置工作模式 6（Homing mode）
    if not sdo.download(node_id, 0x6060, 0x00, 6, size=1, timeout_ms=500):
        print("错误：设置回零模式失败", file=sys.stderr)
        return False
    
    # 3. 设置回零方法（0x6098，见 E06:6.12.3 表6-42）
    # 方法 1：负限位开关 + Index
    if not sdo.download(node_id, 0x6098, 0x00, 1, size=1, timeout_ms=500):
        print("错误：设置回零方法失败", file=sys.stderr)
        return False
    
    # 4. 启动回零（控制字 bit4=1，即 0x1F）
    if not sdo.download(node_id, 0x6040, 0x00, 0x1F, size=2, timeout_ms=500):
        print("错误：启动回零失败", file=sys.stderr)
        return False
    
    # 5. 等待回零完成（轮询状态字 bit10=1 bit12=1）
    print("回零中，等待完成...", file=sys.stderr)
    for _ in range(100):  # 最多等 10 秒
        time.sleep(0.1)
        sw = sdo.upload(node_id, 0x6041, 0x00, timeout_ms=500)
        if sw is None:
            print("错误：读状态字失败", file=sys.stderr)
            return False
        # bit10=Target reached, bit12=Homing attained
        if (sw & 0x1400) == 0x1400:
            print("✓ 回零完成")
            return True
    
    print("错误：回零超时（10 秒）", file=sys.stderr)
    return False
```

#### 2.3 准入闸门（必须保持）

**三条准入规则**（见 `pmp4010.py` 文件头注释）：

1. **对象字典闸门**（`refuse_motion`）：`od.json` 里只要还有 `status != verified` 的条目，运动类子命令打印「N 项对象字典未核对」并拒绝。
2. **标准层**（`require_std_layer`）：发帧路径必须走 `tools/hwtui` 里被审计过的那一条。
3. **实时进程确认**（`require_rt_ack`）：`km3-rt-controller` active 时它正在用这条总线，两条控制流同时持有同一会话，只能由人确认（`--i-know-rt-is-running`）。

**不要删除这三条闸门**。运动命令在 `cmd_motion` 里调用前，必须先过这三关：

```python
def cmd_motion(args):
    """运动类子命令：enable / disable / stop / home"""
    
    # 闸门 1：对象字典必须全部 verified
    if not refuse_motion():  # 返回 False 表示"有未核对项"
        return 3
    
    # 闸门 2：标准层必须存在
    if not require_std_layer():
        return 3
    
    # 闸门 3：实时进程确认
    if not require_rt_ack(args):
        return 3
    
    # ... 真实运动逻辑
```

#### 2.4 验收标准

1. **工具语法无误**：
   ```bash
   python3 -m py_compile device/drivers/pmp4010/tools/pmp4010.py
   ```

2. **discover 能列出**：
   ```bash
   python3 tools/hwtui/discover.py --list
   # 应看到 pmp4010
   ```

3. **--help 能跑**：
   ```bash
   python3 device/drivers/pmp4010/tools/pmp4010.py --help
   # 应看到 --motion {enable,disable,stop,home}
   ```

4. **物理标识检查通过**：
   ```bash
   grep -c 'can1' device/drivers/pmp4010/tools/pmp4010.py
   # 必须为 0（物理标识只在 device.json）
   ```

5. **板端实测**（在 `ssh root@192.168.0.188` 上）：
   ```bash
   # 复制工具到板端 /tmp
   scp -r tools/hwtui device/drivers/pmp4010/tools root@192.168.0.188:/tmp/
   
   # 以 km3 用户运行
   ssh root@192.168.0.188 'su - km3 -c "cd /tmp && python3 pmp4010/tools/pmp4010.py --motion enable --node 1 --i-know-rt-is-running"'
   # 期望：✓ 电机已使能（Operation enabled）
   ```

---

## 交付标准

### 任务 1（驱动实现）

1. ✓ `pmp4010_driver.cpp` 填实（`ReadObject` / `WriteObject` / `Enable` / `SetTargetPosition` 等方法）
2. ✓ 契约测试通过（166 assertions, 0 failures）
3. ✓ 不带旁路开关编不过（`#error` 守卫生效）
4. ✓ 机检全绿（`check-drivers.sh`、`check-deps.sh`）

### 任务 2（运动命令）

1. ✓ `pmp4010.py` 的 `--motion enable/disable/stop/home` 填实
2. ✓ 工具语法无误（`py_compile` 通过）
3. ✓ discover 能列出
4. ✓ 物理标识检查通过（`grep -c 'can1'` = 0）
5. ✓ 板端实测：`--motion enable` 能使能电机（节点 1）

---

## 不需要做的事

- ✗ 不需要改 `od.json`（已由 GPT 填充完成）
- ✗ 不需要改架构文档（除非发现文档错误）
- ✗ 不需要实现位置/速度/转矩的闭环控制（那是上层编排的事）
- ✗ 不需要实现 TUI 界面（`--motion` 是命令行子命令，不是 TUI）
- ✗ 不需要提交代码（你完成后，由主会话统一提交）

---

## 参考文档

### 必读

1. `docs/架构设计/硬件驱动开发路径.md` §一.5-6（驱动实现 + 板载工具）
2. `docs/架构设计/开发规范.md` §1.4（实时路径纪律）
3. `docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md`：
   - 3.2.1（SDO 帧格式）
   - 4.1.1（CiA 402 状态机）
   - 4.1.2-4.1.3（控制字 / 状态字位定义）
   - 6.9-6.12（位置模式 / 速度模式 / 回零模式）
4. `device/drivers/pmp4010/driver/od.json`（73 条 verified 的索引）
5. `device/drivers/pmp4010/CONTRACT.md`（已核对的对象清单）

### 可选

- `device/drivers/_template/driver/<代号>_driver.{hpp,cpp}`（实现模式参考）
- `device/drivers/_template/tools/<代号>.py`（工具模板）
- `tools/hwtui/canopen.py`（`SdoClient` 的实现）

---

## 时间预估

- 任务 1（驱动实现）：4-6 小时（取决于对 CANopen 与 CiA 402 的熟悉度）
- 任务 2（运动命令）：2-3 小时

总计：6-9 小时

---

## 关键约束（必读）

1. **驱动不进 RT 线程**：SDO 是请求/应答事务，只能待在边界线程
2. **运动方法在 `kOdVerified` 为 false 时零字节发出**：不调用 `can_device_->Send()`
3. **三条准入闸门不得删除**：对象字典闸门、标准层、实时进程确认
4. **编译期 `#error` 守卫不得删除**：`PMP4010_OD_VERIFIED` 为 0 时驱动不可编译
5. **物理标识只在 `device.json`**：工具里不得出现 `can1` 等硬编码

---

## 验收方式

完成后，主会话会检查：

1. `g++ ... test_pmp4010.cpp` → 166 assertions, 0 failures
2. `g++ -fsyntax-only pmp4010_driver.cpp`（不带旁路）→ `#error` 阻止编译
3. `bash tools/check-drivers.sh && bash tools/check-deps.sh` → rc=0
4. `python3 -m py_compile pmp4010.py` → 无语法错误
5. `grep -c 'can1' pmp4010.py` → 0
6. 板端 `--motion enable --node 1` → 电机使能成功

---

## 开始前的建议

1. 先读 `device/include/km3/hal/can_device.hpp`，理解 `ICanDevice` 接口
2. 再读 `device/drivers/pmp4010/driver/od_generated.hpp`，理解常量结构
3. 然后读 `tools/hwtui/canopen.py` 的 `SdoClient.upload` / `download`，理解 SDO 事务
4. 最后读 E06:3.2.1 与 4.1.1，理解帧格式与状态机

**不要跳过前三步**，否则你会不知道 `can_device_->Send()` 怎么用、`od::kEntries[i].index` 是什么、`sdo.download()` 的参数是什么意思。

---

祝顺利 🚀
