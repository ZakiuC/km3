# pmp4010 驱动契约

接口签名以 `driver/pmp4010_driver.hpp` 为准。反馈能力只在 [硬件资料索引](../../../docs/硬件手册/PMP4010/README.md#反馈能力唯一声明) 声明；本文件约束调用方、线程和发帧边界。

## 当前状态与阻塞

厂家 E06/D01 已收，**原 PDF 亦已收（2026-10-16）**。OD 为 **94 条中 87 条 verified、5 条 unverified、2 条 assumed**，整体 `kOdVerified=false`。余项按事实分类：节点/寿命保护已明确不支持；1014h 仅有部分报文/索引依据，缺对象属性；SYNC 配置及生产者能力手册未记载（**但本机实测已确认它是同步消费者**，见下）；SDO 等待值属于主机策略；605Bh/605Ch 行为有表但缺字典宽度等元数据。**assumed 2 条是 2026-10-16 厂家微信答复**（抱闸失电抱紧、手册出厂值列不作本机比对依据），无书面文件故不记 verified。**已销账**：完整状态边（原图 4-2）已由原 PDF 读出，与表 4-2 交叉验证相等。具体来源和待补资料见硬件索引，不以"全部手册缺失"解释。

**⚠ 口径变更（2026-10-16 厂家答复）**：厂家明确「手册是通用的，对应很多型号；出厂值以实际软件读出为准」。因此**手册「出厂设定」列不可用作本机比对基准**，而**数据类型/宽度/范围/访问权限/生效方式仍可信**（不随型号变）——已 verified 条目的结构性字段不受影响，不需要重写。

PDO默认映射与动态改写已有E06 §3.3.4、表10-5依据；仍需读回现场映射和通信参数，核对固件、单位、节拍及总线负载后才能接受周期路径。C++ `km3_canopen` 已提供非实时、有界 SDO/NMT 事务；`km3_driver_api` 尚缺，因此主工程仍不接入具体驱动。生产协议入口仍受全局 OD 闸门限制。核对OD不会自动打开运动或得到1ms控制能力。

上层还须提供唯一且实机核对的1~127节点号、实际标度/方向/零偏。已有节点1在1Mbps响应的证据不证明4台现场电机身份、映射或健康均已核对；保存节点或通信参数、重新通电均属另行授权的维护动作。上电自动使能配置、内置终端电阻与6073h电流单位冲突仍未消解，禁止推导已验证电流上限。

本次节点1的8项身份/通信参数上传均已取得应答，但1018h:01h厂商ID=`0x00000509`、1018h:02h产品代码=`0x03000001`，与E06表10-5的`0x2D`、`0x01`不同；设备名为`NiMotion Servo`、软件版本`V3.3`。**此项已降级（2026-10-16 厂家答复）**：厂家明确「手册是通用的，对应很多型号，出厂值以实际软件读出为准」——因此 0x509 与 0x2D 的差异**很可能是同一个「通用手册」的原因（手册跨多个型号），而非「手册与这批货不配套」**。它从「需厂家澄清的兼容性阻塞」降级为「已知解释」，**不再单独阻塞**；但"读取完成"仍然不等于"身份匹配"，此区分保留。200Ch:02h读值1、200Ch:03h读值8（均为uint16），按E06 §3.6.1 表3-13，8=1Mbps、7=800kbps；任务模板中的“7=1Mbps”不采纳。原始应答与读值表见 [2026-10-15验证记录](../../../docs/硬件手册/PMP4010/验证记录-2026-10-15.md#identity-readonly-20261015)。

## 已核对的对象（87 条 verified）

以 `driver/od.json` 的 **entries 数组**计数：**94 = 87 verified + 5 unverified + 2 assumed**。顶层 `device` 的资料状态不计入此数。每行对应一个唯一 key；多对象说明、位域、协议表和冲突登记仍各算一条，并非 87 个独立可读写对象。表中的“主索引”是 JSON 实际 index/sub；“说明覆盖”是该条 note 涉及的范围，不表示生成器为范围中每个对象都生成独立常量。

下文 E06 指 [一体化低压伺服电机 CANopen 通信用户手册 E06](../../../docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md)。`verified` 表示手册核对到表中列明的事实，**不表示板端、运动或带载验收通过**。结构条目的 index/sub 为 null；生成头保留它们且 index 为 `kIndexUnset`，不能仅凭 verified 标志将其作为可发送索引。

### 通信参数、心跳与标识（11 条）

| OD key（含义） | 索引/子索引及覆盖范围 | E06 出处 |
| --- | --- | --- |
| `device_type_missing`（0x1000 设备类型（无数值）） | 1000h:00h | 10.1 表10-5 |
| `error_register`（错误寄存器） | 1001h:00h | 10.1 表10-5 |
| `alarm_history`（预警错误场（报警数量与 16 条历史队列）） | 主索引 1003h:00h；说明覆盖 1003h:00h–10h | 10.1 表10-5 |
| `heartbeat_time`（心跳生产者时间） | 1017h:00h | 3.5.2 |
| `firmware_version`（固件版本读取索引） | 主索引 1018h:03h；说明覆盖 1018h:00h–04h、100Ah:00h | 10.1 表10-5 |
| `identity_object`（标识对象（厂商 / 产品 / 版本 / 序列号）） | 主索引 1018h:01h；与 firmware_version 合看 1018h:00h–04h | 10.1 表10-5 |
| `nmt_commands`（NMT 命令字与报文格式） | 结构条目；无独立索引 | 3.5.1 表3-9 |
| `sdo_frame_format`（SDO 传输报文格式与命令符） | 结构条目；无独立索引 | 3.2.1 |

| `device_name` / `hardware_version` / `software_version`（变长只读字符串） | 1008h:00h / 1009h:00h / 100Ah:00h | 10.1 表10-5 |

### 保存与恢复参数（2 条）

| OD key（含义） | 索引/子索引及覆盖范围 | E06 出处 |
| --- | --- | --- |
| `store_parameters`（参数掉电保存） | 主索引 1010h:01h；说明覆盖 1010h:01h、0Bh | 7.7.1 |
| `restore_parameters`（参数恢复） | 主索引 1011h:01h；说明覆盖 1011h:01h–03h、0Bh | 7.7.2 |

### PDO 通信参数与映射（3 条）

| OD key（含义） | 索引/子索引及覆盖范围 | E06 出处 |
| --- | --- | --- |
| `pdo_default_mapping`（默认 PDO 映射（RPDO / TPDO 1-4）） | 结构条目；1400h–1403h:00h–02h、1800h–1803h:00h–05h；1600h–1602h:00h–02h、1603h:00h–01h；1A00h–1A01h:00h–02h、1A02h–1A03h:00h–01h | 10.1 表10-5 |
| `pdo_transmission_types`（PDO 传输类型 / 禁止时间 / 事件计时器） | 结构条目；1400h–1403h:02h；1800h–1803h:02h、03h、05h | 3.3.3 |
| `pdo_mapping_writable`（PDO 映射是否允许改写） | 结构条目；上述 PDO 通信/映射对象的动态修改流程 | 3.3.4 |

### CiA 402 控制、状态与停机定义（7 条）

| OD key（含义） | 索引/子索引及覆盖范围 | E06 出处 |
| --- | --- | --- |
| `controlword`（控制字） | 6040h:00h | 10.3 表10-7 |
| `statusword`（状态字） | 6041h:00h | 10.3 表10-7 |
| `controlword_bits`（控制字逐位定义） | 结构条目；6040h:00h 的 bit0–15（未命名/保留位见 note） | 4.1.2 |
| `statusword_bits`（状态字逐位定义） | 结构条目；6041h:00h 的 bit0–15（厂家/模式位见 note） | 4.1.3 表4-4 |
| `stop_option_codes`（停机方式：快速停机 / 暂停 / 故障停机） | 结构条目；605Ah:00h、605Dh:00h、605Eh:00h | 4.1.4 表4-6 |
| `cia402_command_table`（控制字指令表与状态字掩码（状态机）） | 结构条目；无独立索引 | 4.1.1 表4-2 |
| `fault_reaction_605E`（故障停机方式 605Eh（访问权限冲突）） | 结构条目；605Eh:00h 的访问权限冲突登记 | 10.3 表10-7 |

### CiA 402 工作模式、标度与参数（34 条）

| OD key（含义） | 索引/子索引及覆盖范围 | E06 出处 |
| --- | --- | --- |
| `modes_of_operation`（工作模式设定） | 6060h:00h | 10.3 表10-7 |
| `modes_of_operation_display`（工作模式回显） | 6061h:00h | 10.3 表10-7 |
| `target_position`（目标位置） | 607Ah:00h | 10.3 表10-7 |
| `actual_position`（实际位置） | 6064h:00h | 10.3 表10-7 |
| `position_scale`（位置标度与零位偏置） | 结构条目；608Fh:01h–02h、6091h:01h–02h 的位置换算 | 5.1 |
| `user_unit_scale`（用户单位换算（608Fh / 6091h）） | 主索引 608Fh，sub=null；说明覆盖 608Fh:01h–02h、6091h:01h–02h | 5.1 表5-2 |
| `target_velocity`（目标速度） | 60FFh:00h | 10.3 表10-7 |
| `actual_velocity`（实际速度） | 606Ch:00h | 10.3 表10-7 |
| `velocity_scale`（速度标度） | 结构条目；608Fh:01h–02h、6091h:01h–02h 的速度换算 | 5.2 |
| `profile_acceleration`（轮廓加速度 / 减速度） | 6083h:00h | 10.3 表10-7 |
| `profile_deceleration`（轮廓减速度） | 6084h:00h | 10.3 表10-7 |
| `max_profile_acceleration`（最大轮廓加速度 / 减速度） | 主索引 60C5h:00h；说明覆盖 60C5h:00h、60C6h:00h | 10.3 表10-7 |
| `profile_velocity`（轮廓速度与最大轮廓速度） | 主索引 6081h:00h；说明覆盖 6081h:00h、607Fh:00h | 10.3 表10-7 |
| `profile_end_velocity`（轮廓终点速度） | 6082h:00h | 10.3 表10-7 |
| `profile_ramp_type`（轮廓斜坡类型（线性 / S 曲线）） | 6086h:00h | 10.3 表10-7 |
| `profile_jerk`（轮廓加加速度 / 减加速度） | 主索引 60A4h:01h；说明覆盖 60A4h:01h–02h | 10.3 表10-7 |
| `homing_method`（回零方法与回零速度） | 6098h:00h | 6.7.4.1 表6-21 |
| `homing_speeds`（找开关速度 / 找原点速度） | 主索引 6099h:01h；说明覆盖 6099h:01h–02h | 10.3 表10-7 |
| `homing_acceleration`（原点回归加速度） | 609Ah:00h | 6.7.2 表6-17 |
| `home_offset`（原点偏移值与零位偏置） | 607Ch:00h | 10.3 表10-7 |
| `position_offset_60B0`（位置偏置（周期同步用）） | 60B0h:00h | 10.3 表10-7 |
| `software_position_limit_max`（软件绝对位置上限） | 607Dh:02h | 10.3 表10-7 |
| `software_position_limit`（软件位置限位（最小 / 最大）） | 主索引 607Dh:01h；说明覆盖 607Dh:01h–02h | 10.3 表10-7 |
| `position_window`（位置到达阈值与时间窗口） | 主索引 6067h:00h；说明覆盖 6067h:00h、6068h:00h | 10.3 表10-7 |
| `following_error`（位置偏差过大阈值与超时） | 主索引 6065h:00h；说明覆盖 6065h:00h、6066h:00h | 10.3 表10-7 |
| `quick_stop_deceleration`（快速停机减速度） | 6085h:00h | 10.3 表10-7 |
| `error_code_object`（错误码 603Fh） | 603Fh:00h | 10.3 表10-7 |
| `supported_drive_modes`（支持的驱动模式位图） | 6502h:00h | 10.3 表10-7 |
| `polarity`（指令极性（位置 / 速度）） | 607Eh:00h | 5.3 表5-5 |
| `max_motor_speed`（电机最大速度） | 6080h:00h | 10.3 表10-7 |
| `position_option_code`（定位选项（相对位置的基准）） | 60F2h:00h | 10.3 表10-7 |
| `interpolation_period`（插补周期（t × 10^n）） | 主索引 60C2h:01h；说明覆盖 60C2h:01h–02h | 10.3 表10-7 |
| `max_current`（最大电流） | 6073h:00h | 10.3 表10-7 |
| `torque_feedback`（转矩反馈值） | 6077h:00h | 10.3 表10-7 |

### 工作模式、握手与回零规则（9 条）

| OD key（含义） | 索引/子索引及覆盖范围 | E06 出处 |
| --- | --- | --- |
| `mode_codes`（工作模式码取值表） | 结构条目；无独立索引 | 6.1 表6-3 |
| `controlword_bits_per_mode`（控制字 bit4~bit6 与 bit8 的逐模式含义） | 结构条目；无独立索引 | 6.3.3 表6-7 |
| `statusword_bits_per_mode`（状态字 bit10 / bit12 / bit13 的逐模式含义） | 结构条目；无独立索引 | 6.3.3 表6-9 |
| `enable_sequence`（使能序列与各模式使能终值） | 结构条目；无独立索引 | 6.3.6 |
| `pp_trigger_patterns`（PP 模式触发位组合（绝对/相对 × 立即/非立即）） | 结构条目；无独立索引 | 6.3.6 表6-8 |
| `homing_di_function_codes`（回零所需的 DI / VDI 功能号） | 结构条目；2003h:03h–14h、2017h:01h–20h 的回零端子功能 | 6.7.4.1 表6-20 |
| `mode_switch_restriction`（运行中切换模式的限制） | 结构条目；无独立索引 | 6.1 |
| `mode_select_manual_conflict`（0x2002:01h 的宽度与默认值冲突（登记）） | 结构条目；2002h:01h 的宽度/默认值冲突登记 | 6.1 表6-2 |
| `fault_reset`（故障复位方式） | 结构条目；无独立索引 | 9.3 |

### 厂商对象与故障报文（11 条）

| OD key（含义） | 索引/子索引及覆盖范围 | E06 出处 |
| --- | --- | --- |
| `control_mode_select`（控制模式总开关（CiA402 / NiMotion）） | 2002h:01h | 10.2 表10-6 2002h |
| `fault_reaction_config`（报警码配置（200Eh 故障代码组）） | 主索引 200Eh:01h；说明覆盖 200Eh:01h–11h（17 项） | 10.2 表10-6 200Eh |
| `bus_bitrate`（CAN 波特率设置） | 200Ch:03h | 3.6.1 表3-13 |
| `node_id_object`（驱动器轴地址（节点 ID）） | 200Ch:02h | 3.6.2 |
| `comm_method`（通信方式选择） | 200Ch:01h | 10.2 表10-6 200Ch |
| `velocity_feedback_unit`（606Ch 速度反馈的单位选择） | 2006h:12h | 10.2 表10-6 2006h |
| `comm_watchdog_time`（通信看门狗喂狗时间） | 200Ch:1Bh | 7.10 |
| `monitor_bus_voltage`（母线电压值与模块温度值） | 主索引 200Bh:15h；说明覆盖 200Bh:01h、02h、04h–06h、0Ah、0Fh、12h–16h、2Dh | 10.2 表10-6 200Bh |
| `emcy_frame_format`（EMCY 报文框架） | 结构条目；无独立索引 | 3.4 表3-7 |
| `emcy_vendor_codes`（EMCY 厂商错误码表） | 结构条目；无独立索引 | 9.1 表9-1 |
| `fault_reaction_codes`（故障反应码取值表） | 结构条目；无独立索引 | 9.2 |

其中 1008h/1009h/100Ah 为表10-5 的设备名/硬件版本/软件版本行；当前 entries 已为它们各建 verified 字符串条目。607Dh:02h 也已独立登记为上软限位，四项均计入 77。60C1h:01h 是默认 RPDO2 映射涉及的对象，也未单独建 entry。200Ch:02h/03h 出处是 §3.6.2 / §3.6.1 表3-13 及 §10.2 表10-6；200Ch:1Bh 出处是 §7.10，不是 §5 的单位换算表。

已有冲突继续保留：6073h 的电流单位在表6-30与表10-7不一致，verified 不覆盖物理换算；2002h:01h 的宽度/默认值、605Eh 的访问权限、1003h 的队列语义、PDO 传输类型宽度及60C2h的默认周期均须按各条 note 处理，不能用手册核对状态消除冲突。

## 未核对的对象（6 条 unverified）

以下六条均保持 `index/sub=null`、`cite=null`；依据与缺口来自 `driver/od.json` 各条 `note`，不能填入 CiA 惯例值来解除闸门。

| OD key（含义） | 具体阻塞原因与所需证据 |
| --- | --- |
| `node_guard`（节点/寿命保护） | E06 §3.5.2 明确“本设备只支持心跳”；表10-5 无1016h行，不能把100Ch/100Dh或1016h惯例补成能力。此项是已知不支持的能力占位，非等待一个周期值；需调整能力声明/结构的专门决策，不能伪造对象使其 verified。 |
| `emcy_cob_id`（EMCY COB-ID） | §3.4 图3-12提到1014h，表3-7给出0x80+节点ID报文格式，但表10-5无1014h的类型、默认值、访问权限、生效方式；缺厂家对象属性和可读写能力确认。 |
| `sync_period`（SYNC周期/COB-ID） | §3.5.1 表3-10、§3.3.3与同步模式章节承认同步报文/节拍；表10-5无1005h/1006h/1007h，无SYNC COB-ID、周期值与生产者能力说明。需原件或厂家补充；不能推导1ms能力，也不能断言设备不可能作生产者。 |
| `sdo_timeout`（SDO应答等待时间） | §3.2.2 表3-2仅有从站“SDO协议超时”中止码，没有主站毫秒等待值；示例500ms是作者策略。需明确主机策略与厂家事实的区分，不能把示例值登记成手册参数。 |
| `stop_option_605BC_missing`（605Bh/605Ch停机方式） | §4.1.4 表4-8/4-9出现编号及0/1行为，但表10-7无两行的类型、范围、访问、默认值、生效方式；编号语义疑似与惯例对调，不能凭惯例纠正。需厂家澄清及完整字典元数据。 |
| ~~`cia402_state_machine_missing`（完整状态迁移图）~~ | **✅ 已消解（2026-10-16）**：原始 PDF（LMS-C12-19011，第 14 页图 4-2）到手后读出全部转换号 → 起止状态，记入 `cia402_state_transitions`。**与表4-2 的转换号逐条交叉验证相等**（Shutdown={2,6,8}、Disable voltage={7,9,10,12}、Quick stop={7,10,11}、Disable operation={5}、Enable operation={4,16}、Fault reset={15}、Switch on={3}）。注意图 4-2 未画转换 16，由表4-2 的「→4,16」与 NOTE 共同确定。 |
| `brake_failsafe_behaviour` / `vendor_factory_value_policy`（assumed） | 2026-10-16 厂家微信答复，**无书面文件**，故记 assumed 而非 verified（cite 必须为 null）。assumed 与 unverified 都挡住运动闸门。前者记「断电/失能/报警都抱紧」；后者记「手册出厂值列是跨型号通用值，不作本机比对依据」。**待办**：如有机会，请厂家就此两点出具书面说明（邮件/盖章文件），届时可转 verified。 |

## 生成物

- `driver/od_generated.hpp`：`kEntryCount=94`、`kUnverifiedCount=7`，87 条 verified；默认 `PMP4010_OD_VERIFIED=0`、`kOdVerified=false`。
- 仓库根目录 `tools/hwtui/canopen_consts.py`：Python维护工具共享常量，同源94条、5条未核对、2条assumed；不在型号tools目录另存一份。
- 从仓库根目录生成：`python3 -I tools/hwdriver/gen-od.py --model pmp4010`。
- 只读漂移校验：`python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check`。

## 编译期闸门

当前生成头的实际条件如下（错误消息缩写；完整消息以生成头为准）：

```cpp
#ifndef PMP4010_OD_VERIFIED
#define PMP4010_OD_VERIFIED 0
#endif
#if !PMP4010_OD_VERIFIED
#if !defined(KM3_ALLOW_UNVERIFIED_OD) || !KM3_ALLOW_UNVERIFIED_OD
#error "pmp4010 对象字典未验证（driver/od.json 里仍有 6 条 status != verified）。..."
#else
// 未核对旁路标记，见生成头
#endif
#endif
```

默认编译被拒绝。源码按宏真值判旁路，因此定义 `KM3_ALLOW_UNVERIFIED_OD=0` 仍会触发此 `#error`；强制 `PMP4010_OD_VERIFIED=1` 也能跳过它。这是当前预处理行为的说明，不是许可。不得通过强制verified宏伪造资料状态。唯一额外区分是本地固定假设备夹具的私有编译定义：它仅让隔离可执行文件覆盖真实协议分支，不更改任何资料或生产构建，且绝不连接真实后端；详见下文。按架构硬停规则第2/4条和第6步，`KM3_ALLOW_UNVERIFIED_OD=1` 仅用于隔离CI/mock或未接电机的控制器回环验证；不能进入接有电机的现场运行、生产运动或用于解除运行时守卫。本轮资料收尾未运行此类构建或回环。

## RT 可达性

驱动实例及总线会话由非实时边界线程独占；RT线程通过既有无锁缓冲交换请求/快照，不直接调用驱动发帧API。SDO是请求/应答事务，不能进入实时线程。公开常量可在编译期使用；普通getter本身无分配不等于允许跨线程共享可变实例。

| 函数 | 可进入RT | 理由 |
| --- | --- | --- |
| 构造 / 析构、`Open` / `Close` | 否 | 生命周期、配置与HAL状态操作属于边界线程 |
| `ProbeNode` / `SendNmt` | 否 | 协议入口，后端收发与完成时序无RT保证 |
| `Enable` / `Disable` / `MoveTo` / `SetVelocity` / `Stop` / `Home` | 否 | 统一守卫；未来协议事务仍须非实时执行 |
| `SdoRead` / `SdoWrite`、`ReadBytes` / `ReadObject` / `ReadString` / `WriteObject`、全部类型化读写与状态操作 | 否 | SDO有请求/应答等待，实例独占 |
| `GuardTransmit` | 否 | 修改统计，限实例所属线程使用 |
| `Stats` / `Config` / `LogicalBus` / `IsOpen` | 否（实例约束） | 虽只读，仍不得与边界线程并发访问可变实例 |
| `UnverifiedOdCount` / `OdEntryCount` | 是 | constexpr编译期元数据，不访问总线 |

以上描述不声称每个getter或生命周期方法实际调用HAL收发，也不把mock耗时作为RT证明。

## 发帧与错误纪律

1. `kOdVerified=false` 时，所有协议入口经全局守卫拒绝，MockCanDevice发送列表必须为空。生成头默认编译闸门与运行时守卫同时保留，测试旁路不构成生产许可。
2. 输入错误在发帧前拒绝；节点号来自调用方配置并校验合法/唯一，不能取手册默认1充当4台现场地址。
3. `Stop` 在守卫关闭时也不发帧，不能绕过OD限制。关闭服务或断总线会触发何种机械响应尚未验收，**不得宣称停服务就能安全停机**。现场停机由独立安全设计负责。
4. 标准层负责 CANopen 帧、应答相关性、分段传输与中止；型号驱动负责生成 OD 的宽度/权限校验和有依据的 CiA402 编排。未实现的周期 PDO/心跳会话能力不冒充成功。
5. 生产运动还需状态机、PDO、故障/失联策略、物理标度与载荷验收；本地单测只证明隔离条件下的行为。

## 配置与维护工具

逻辑总线 `can-2` 的底层映射来自 `device/config/device.json`；接口up/down及位速率归 `km3-can.service` 所有，驱动和工具不接管。Python维护工具是独立路径：`--test` 严格离线；`--od-dump` 仅展示本地OD元数据，不打开总线；`--scan` 是会发请求的只读SDO操作，RT服务active时必须显式确认。全局运动守卫不会因该确认而打开。部署工具包必须同时带共享hwtui、型号脚本及其同版OD；包验证不等于板端协议验收。


## 有界 C++ 实现与本地验收边界

`device/include/km3/canopen.hpp` 是 C++ 非实时标准层；`km3_canopen` 为其 CMake interface 目标。帧格式与 CiA402 状态/命令常量从 `tools/hwtui/canopen_wire.json` 生成到 C++/Python；事务执行器分别实现，不宣称跨语言共享算法。两者重放同一份 `tools/hwtui/vectors/canopen_golden.json` 完整事务作为一致性检查。PMP4010 E06 §3.2.1 只给出加急传输；通用分段协议依据 CANopenNode 的 [SDO 协议说明](https://canopennode.github.io/CANopenNode/CO__SDOserver_8h_source.html)，本地分段测试不证明此型号/固件支持全部分段能力。

`od_access_generated.hpp` 从 `od.json` 的已核对、可寻址条目生成宽度、权限和变长标记，共57项（54项数值、3条变长字符串）。新增字符串与上软限位不改变五项 unverified、两条 assumed 或全局置信度。结构条目没有地址，不会发向哨兵；多对象 note 不自动扩成可写索引。1008h/1009h/100Ah 字符串为手册表10-5的只读公共对象。未单列的对象/子索引保持拒绝，避免从说明文本猜测参数。

SDO 内核、类型化读写及 `ReadString` 均使用固定256字节 `SdoBuffer`（bytes + size），不返回会分配的 `std::string`；这明确调整了任务示例的字符串返回类型，以满足无动态分配约束。保留原 `SdoRead(vector&)`，调用方须在事务前 `reserve(256)`，容量不足在发帧前返回 `EInvalidRequest`，失败保持原值。构造/配置的既有 string/vector 类型及 HAL 自身行为不纳入协议内核无分配证明；实例仍禁止进入RT。

主机必须显式配置 `sdo_timeout_ms>0`，每次完整 SDO（含全部段）使用单调时钟绝对期限；使能/PP握手也共享一个总期限。状态轮询按绝对时刻休眠。错误传播保留 HAL 错误码；相关 SDO abort 返回 `ECommandRejected`，原32位码可用 `LastSdoAbortCode()` 取得，格式/宽度/toggle错误返回 `EDeviceError`。`Stats()` 同时给出实际收发、SDO超时和失败清理计数。

实例必须独占 HAL 接收队列：事务开始有界清除旧帧，事务期间消费其他 COB-ID；不安装/篡改调用方过滤器。CANopen无事务ID，相同对象的迟到应答若在新请求后到达无法从帧上区分；标准层在已发送请求的事务超时后隔离该节点，后续 SDO 返回 EDeviceBusy 且不发送。只有独立恢复从站与传输、确认旧应答不可能残留后才能 ConfirmExternalRecovery；单纯清队列或 NMT 不能证明这一点。型号驱动不自动解除隔离；控制事务超时后的清理可能被隔离挡下，cleanup_failures 会记录，不能宣称机械停止。

所有6040h控制字写（包括通用SDO写）均先只读确认2002h:01h为CiA402(0)，读失败即拒绝；普通只读事务不附加选择器读取。`Enable` 再确认模式1/3/4/6/8/9/10，再执行0x06→0x07→0x0F并逐步等待状态。模式2需要0x7F，模式7需要0x1F且会激活插补；当前一次性使能接口在任何写入前拒绝这两种模式，不将模式触发混入通用使能。故障复位为先0再0x80，等待故障位清除后再降回0；故障反应态先等其完成。

`SetTargetPosition` 写用户单位的原始目标，先确认Operation enabled 并读607Dh:01h/02h闭区间；越界、上下限倒置或读取失败拒绝目标写。`MoveTo` 在首个控制字写入前预读两项限位。`WriteObject`/`SdoWrite` 同样不能绕过目标限位或控制字 CiA402 检查。读取限位与选择器会发送只读请求；“拒绝时零帧”在此指零运动/控制写，只有OD关闭时才保证零总帧。`MoveTo` 只接受PP(1)，实现0x0F→0x1F上升沿、状态字bit12握手、再清bit4；`SetTargetVelocity` / `SetVelocity(Direction)` 以及通用60FFh写入当前统一返回 EInvalidConfig，不发送：6080h单位rpm，而60FFh单位用户单位/s，E06 §5.2要求608Fh/6091h四项实际参数构成换算链。TODO：接通并核对该链后再比较速度与6080h；现有 counts_per_unit 不是这个换算，不能启用速度。模式切换采取手册示例的失能态策略，写后验证6061h。

`Home` 要求明确配置 `homing_method`（17..30或35）与 `homing_timeout_ms>0`；在任何写入前检查CiA402选择器，失能后设置并读回模式6和方法，再使能并产生0x0F→0x1F边沿。端子、速度、加速度、负载和安全范围必须由上层维护配置与现场核对，不猜测方法1。完成判据为Operation enabled、无fault/homing error、bit10+bit12；若触发前已完成，须先观察不完整再接受完成，防止旧状态误报。启动后失败、丢失ACK、超时会尽力清触发/快速停机，保留原错误；`cleanup_failures` 表示未获得清理状态确认。状态确认不等于机械停止验收。

测试分成未核对OD零帧契约、默认生产负编译、独立“已核对”固定内存假设备夹具。夹具的 `PMP4010_OD_VERIFIED=1` 仅为独立测试目标私有定义，不传播至生产或零帧目标，且不链接真实 SocketCAN 后端。夹具验证实际事务、共享金标、错误路径和无分配；不解除五项OD阻塞、两条assumed、身份差异或现场验收要求。

漂移检查：`python3 tools/hwdriver/gen-canopen.py --check`、`python3 tools/hwdriver/gen-pmp4010-access.py --check`。隔离构建入口：`cmake -S device/drivers/pmp4010/tests -B /tmp/pmp4010-test -DKM3_SANITIZER=address`，随后构建与CTest；这不是生产设备端的native构建或部署。

## PDO 传输内核（2026-10-16，纯代码交付）

`device/include/km3/canopen_pdo.hpp` 提供通用固定容量 codec 与 PdoSession；
`device/drivers/pmp4010/driver/pdo_config.hpp` 从生成 OD 映射与调用方节点、RtParams 构建型号配置。
该型号工厂保留默认编译闸门和 `kOdVerified` 运行守卫，当前拒绝；没有接入生产运动入口。
结构化映射位于 od.json 的 pdo_default_mapping.pdo_layouts，:00 的条目数决定
有效映射字及精确 DLC；默认模板不得冒充现场读回匹配。

RPDO 与 SYNC 由同一个显式 SCHED_OTHER SyncWorker 独占发送，RT 只可 Submit 固定批次、
Pop 固定反馈。单调绝对时钟推进、过期跳槽，无补发；命令队列每周期有界取最新批次，
空队列不自动重播旧命令。SCHED_OTHER 抖动可接受范围尚未完成 CSP 验证，
不能据此宣布 4 ms 带载闭环达标。实际完整线序需确认后端驱动语义与总线负载。

必须等待每个 RPDO 的驱动发送完成后才允许排入 SYNC，不能把 Send 成功或
内核队列清空当作完成（低 ID 的 SYNC 可能抢仲裁）。SocketCanDevice 的默认模式仍
关闭 socket loopback；只有新建时显式 confirm_tx=true 的 PDO 专用模式开启 loopback
和 RECV_OWN_MSGS，并要求 netlink IFF_ECHO，拒绝核心软件 fallback 回环。
仅匹配本 socket MSG_CONFIRM、ID、DLC、数据的回显才放行；普通 echo 不算。
SYNC 本身也确认，保持至多一个未完成帧。等待期间的 TPDO 存入预分配 64 帧缓冲，
任何满缓冲、错误帧、超时、错误确认均失败。已发送后失败隔离 socket 至关闭重开；
任何 PDO 周期失败都锁存 session，停止 producer，禁止后续 SYNC 施加半批旧 RPDO。
清理/重建对象不证明从站已恢复，重新启动前必须独立处理从站暂存的部分数据。

此确认机制不是 candump 发帧证据；现场仍以接口 TX 计数差分取证。IFF_ECHO/
MSG_CONFIRM 的 Linux 契约参见 https://docs.kernel.org/networking/can.html；
当前仅作 syscall mock 验证，未在板端核实该内核/驱动完成时序。

TPDO 不等待、不要求每周期齐全（类型0变化触发、1..240分频、0xFF异步均可缺帧）；
逐帧严格校验经典帧标志和精确长度，按小端解码。反馈 processed_us 记录**出队处理时间**，
不是总线到达时间，不能用于证明新鲜度。实际接收时间戳与失联/陈旧反馈安全策略未实现，
后续运动集成前必须补齐。命令/反馈队列满均有独立丢弃计数，Stats 只能在 worker join 后读。
整个传输内核不调用 SDO Download/NMT，不执行参数配置、使能、运动或故障恢复。
