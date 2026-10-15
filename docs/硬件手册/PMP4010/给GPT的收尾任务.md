# 给 GPT 的收尾任务（2026-10-15）

## 任务目标

完成 PMP4010 驱动的「资料整理与只读验证」阶段的收尾工作。

驱动实现（C++ 代码填充）将由专门的「驱动填充 agent」负责，你不需要做。

---

## 任务 1：完整身份核对（板端只读 SDO）

**现状**：你已读了 `0x1000`（设备类型 = `0x00020192`），通信成功 ✓

**要做**：补齐剩余身份项，**全部只读，不写任何参数**

在板端（`ssh root@192.168.0.188`）执行以下只读 SDO 请求，记录结果：

### 1.1 读 0x1008（厂商名，Device name）
```bash
# SDO 上传请求：601#40 08 10 00 00 00 00 00
# 期望回复：581#4F 08 10 00 <长度> <字符串前4字节> ...
```

### 1.2 读 0x100A（固件版本，Software version）
```bash
# SDO 上传请求：601#40 0A 10 00 00 00 00 00
# 期望回复：581#4F 0A 10 00 <长度> <版本字符串> ...
```

### 1.3 读 0x1018（身份对象 Identity object）
这是一个 RECORD，需要逐个子索引读：

```bash
# 0x1018:01 厂商 ID（Vendor ID）
# SDO 请求：601#40 18 10 01 00 00 00 00
# 期望：581#43 18 10 01 <4字节厂商ID>

# 0x1018:02 产品代码（Product code）
# SDO 请求：601#40 18 10 02 00 00 00 00

# 0x1018:03 修订版本（Revision number）
# SDO 请求：601#40 18 10 03 00 00 00 00

# 0x1018:04 序列号（Serial number）
# SDO 请求：601#40 18 10 04 00 00 00 00
```

### 1.4 读 200Ch（厂商通信参数，2 个子索引）

```bash
# 200Ch:02h 节点号（Node ID）
# SDO 请求：601#40 0C 20 02 00 00 00 00
# 期望：581#4F 0C 20 02 <1字节> <节点号> ...

# 200Ch:03h 波特率参数（Baudrate parameter）
# SDO 请求：601#40 0C 20 03 00 00 00 00
# 期望：581#4F 0C 20 03 <1字节> <波特率码> ...
# 波特率码对照表在 E06 手册 5.1 表5-1
```

**交付物**：

1. 把上述 8 条只读 SDO 的实际应答记录到 `docs/硬件手册/PMP4010/验证记录-2026-10-15.md`（追加到现有内容后）
2. 把读到的厂商名、固件版本、厂商 ID、产品代码、修订版本、序列号、节点号、波特率参数，整理成表格
3. 对照 E06 手册确认：厂商 ID 是否等于 `0x2D`（表10-5）、产品代码是否等于 `0x01`（表10-5）、波特率码是否等于 `7`（1 Mbps，表5-1）

---

## 任务 2：更新 CONTRACT.md

**现状**：`device/drivers/pmp4010/CONTRACT.md` 还在写「25 条全 unverified」，但 `od.json` 已是 **73 verified / 6 unverified**。

**要做**：

### 2.1 列出 73 条 verified 的索引范围

按手册章节分组，列出：

```markdown
## 已核对的对象（73 条 verified）

### 通信参数（1000h 组）
- 1000h, 1001h, 1003h:00-10h, 1008h, 1009h, 100Ah, ...
  出处：E06:10.1 表10-5

### 保存与恢复参数（1010h/1011h 组）
- 1010h:01h, 1010h:0Bh, 1011h:01-03h, 1011h:0Bh
  出处：E06:10.1 表10-5

### 心跳与标识对象
- 1017h, 1018h:00-04h
  出处：E06:10.1 表10-5

### PDO 通讯参数与映射（1400h-1403h, 1600h-1603h, 1800h-1803h, 1A00h-1A03h）
- ...
  出处：E06:10.1 表10-5

### CiA 402 控制与状态（6000h 组）
- 6040h（控制字，16位逐位定义）, 6041h（状态字，16位逐位定义）, ...
  出处：E06:4.1.2 表4-4、4.1.3 表4-5

### CiA 402 工作模式与参数（6060h-60FFh）
- 6060h, 6061h, 6064h, 607Ah, 6083h, 6084h, 6098h, 60C1h, 60FFh, ...
  出处：E06:10.3 表10-7

### 厂商对象（200Ch）
- 200Ch:02h（节点号）, 200Ch:03h（波特率参数）, 200Ch:1Bh（通信看门狗）
  出处：E06:5.1 表5-1、5.2 表5-2、5.3 表5-3
```

### 2.2 列出 6 条 unverified 的含义与阻塞原因

```markdown
## 未核对的对象（6 条 unverified）

1. **node_guard**（节点保护）：手册明确「本设备只支持心跳」（E06:3.5.2），无 1016h 行
2. **emcy_cob_id**（EMCY COB-ID）：3.4 有报文格式，10.1 表10-5 无 1014h 行
3. **sync_period**（SYNC 周期）：手册承认同步报文存在（3.5.1 表3-10），但无 1005h/1006h 行
4. **sdo_timeout**（SDO 超时）：手册只列中止代码，无毫秒数值
5. **stop_option_605BC_missing**（605Bh/605Ch 停机方式）：4.1.4 正文提及，10.3 表10-7 无此二行
6. **cia402_state_machine_missing**（状态机转换图）：图 4-2 在 md 里只有提取器补注，无完整迁移表

详见：device/drivers/pmp4010/driver/od.json 各条的 `note` 字段
```

### 2.3 更新生成物状态

```markdown
## 生成物

- `od_generated.hpp`：79 条中 73 条 verified，6 条 unverified
- `canopen_consts.py`：Python 侧常量（TUI 工具用）
- 生成命令：`python3 -I tools/hwdriver/gen-od.py --model pmp4010`
- 校验：`python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check`

## 编译期闸门

驱动在 `PMP4010_OD_VERIFIED` 为 0 时**不可编译**：
```cpp
#if !PMP4010_OD_VERIFIED && !defined(KM3_ALLOW_UNVERIFIED_OD)
#error "pmp4010 对象字典未验证（driver/od.json 里仍有 6 条 status != verified）。 ..."
#endif
```

仅在校验工具与 CI 中可定义 `KM3_ALLOW_UNVERIFIED_OD=1` 关闭本守卫。
```

---

## 任务 3：更新 README.md 的「当前状态」段

**要做**：把 `device/drivers/pmp4010/README.md` 的「当前状态」段改成：

```markdown
## 当前状态

- [x] 骨架生成（`new-driver.sh`）
- [x] 对象字典填充（79 条中 73 verified，6 unverified）
- [x] 生成物同步（`od_generated.hpp`、`canopen_consts.py`）
- [x] 契约测试通过（166 assertions，0 failures）
- [x] 板端只读探测（1 Mbps 通信成功，节点 1 应答）
- [x] 完整身份核对（1000h/1008h/100Ah/1018h/200Ch）
- [ ] **驱动实现**（`pmp4010_driver.cpp` 仍是骨架，待「驱动填充 agent」完成）
- [ ] **运动命令**（`pmp4010.py` 的 `--motion` 仅骨架，待驱动实现后填充）

**阻塞项**：6 条 unverified 条目（见 CONTRACT.md），需手册原件或厂家澄清。
```

---

## 交付标准

1. ✓ 8 条只读 SDO 的实际应答记录在 `验证记录-2026-10-15.md`
2. ✓ `CONTRACT.md` 列出 73 条 verified 的索引范围与出处
3. ✓ `CONTRACT.md` 列出 6 条 unverified 的阻塞原因
4. ✓ `README.md` 的「当前状态」段更新为上述清单

---

## 不需要做的事

- ✗ 不需要填 `pmp4010_driver.cpp`（驱动实现由「驱动填充 agent」负责）
- ✗ 不需要实现 `--motion` 命令（等驱动实现后再填）
- ✗ 不需要改架构文档（速率配置已由主会话完成）
- ✗ 不需要提交代码（你完成后，由主会话统一提交）

---

## 验收方式

完成后，主会话会检查：

1. `bash tools/check-manual-citations.sh` 退出码为 0
2. `python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check` 一致
3. `CONTRACT.md` 与 `od.json` 的 73/6 分布一致
4. `验证记录-2026-10-15.md` 包含 8 条 SDO 应答

---

## 时间预估

约 30 分钟（板端 8 条只读 SDO + 文档更新）。

---

## 参考文档

- `docs/架构设计/硬件驱动开发路径.md` §一.9（身份核对四项）
- `docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md`（索引表在 10.1 表10-5、10.3 表10-7）
- `device/drivers/pmp4010/driver/od.json`（73 verified / 6 unverified 的实际条目）
