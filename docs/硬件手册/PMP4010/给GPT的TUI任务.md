# 给 GPT 的任务：把 PMP4010 工具做成 TUI（2026-10-17）

## 任务目标

按用户要求「**每硬件一个工具、工具必须是 TUI**」，把 `pmp4010.py` 做成交互式 TUI 工具。

**TUI 是默认入口；CLI 子命令全部保留**（给脚本与自动化用，两者共用同一份底层逻辑）。

**本任务不写任何电机参数、不动板端、不提交。**

---

## 零、必须先读

1. `tools/hwtui/tui.py` **文件头 40 行** —— 那是本工具箱 TUI 的**边界声明**，本任务不得越界（见 §二）
2. `tools/hwtui/tui.py` 的 `TuiApp` / `Menu` / `Table` / `LogView` / `Grid` / `Rect` / `Layout`
3. `device/drivers/pmp4010/tools/pmp4010.py`（尤其 `panel_rows()`、`HwPanel`、各 `cmd_*`）
4. `device/drivers/pmp4010/tools/pmp4010_position.py`（`validate()` / `check_start()` / `check_envelope()`）
5. `device/drivers/pmp4010/tools/pmp4010_params.py`（`plan()` / `execute()` / `restore_attempted()` / `confirmation()`）
6. `docs/硬件手册/PMP4010/运动验收流程.md`（人类执行的安全清单）
7. `docs/硬件手册/PMP4010/参数写入工具.md`
8. `docs/架构设计/硬件驱动开发路径.md` §9（TUI 面板的既有要求）
9. `docs/架构设计/系统改动开发路径.md`（本项目的改动纪律）

---

## 一、为什么现在做这个（背景）

选型结论（记录在案）明确写过：

> **按用户要求 2 的字面（每硬件一个工具、工具必须是 TUI）**，方案 B 唯一能碰总线的是无头 CLI → 出局。

也就是说：**"没有 TUI"本身就是当初被淘汰的理由**。而现在交付的 `--motion` / `--position` / `--params` / `--calibrate` 全是无头子命令，只有一个只读概览面板按 `q` 退出。

**这次要把它补上，不是再做一层壳。**

---

## 二、硬边界：TUI 外壳的既有约束（**不得违反**）

`tools/hwtui/tui.py` 文件头已声明，本任务必须遵守：

| 约束 | 内容 |
| --- | --- |
| **零第三方依赖** | 只用 Python 标准库 `curses`。板上 `python3 3.10.12`，根分区只剩 2.1 GiB |
| **字形边界** | **只处理 ASCII 可打印字符与单宽制表符号（U+2500–U+257F）**。不碰 wcwidth、组合字符、双宽字形。**UI 文案一律用英文与符号，终端里排不了中文——这是接受的代价，不是疏漏** |
| **无颜色与属性** | `render()` 是**字符**网格 diff，不是"格子"diff。选中态用 `>`、失败用 `x` 这类标记字符表达。**不要引入 SGR/颜色** |
| **`render()` 是纯函数** | 吃两帧字符网格、吐转义序列。没有终端、没有全局状态、没有 I/O。改动它要同步改 `tools/hwtui/test_tui.py` |

**数据与文案分离**：`panel_rows()` 已经给每行同时返回英文 key 与中文 label。curses 里用英文 key（排不了中文），纯文本输出用中文 label。**沿用这个模式**。

---

## 三、设计准则（来自 `tui-design` skill，摘录为约束）

这些是**设计准则，不是 API**。项目不引入该 skill，也不新增依赖；只照着做。

1. **围绕重复动作设计，而不是围绕功能列表。** 操作者在这里反复做的是：看状态 → 改参数 → 跑一次 → 看结果。
2. **破坏性操作必须显示确切目标与后果，确认强度随不可逆性变化。**
   - 写参数（RAM）＝ 可撤销 → 一次确认
   - **`save` 写 EEPROM ＝ 不可逆** → **最强的二次确认**，且必须显示"将持久化到设备"
   - 回滚 ＝ 写回原值 → 中等确认
3. **状态、输入、渲染分离。** `build()` 只做排版，**不做 I/O**（`TuiApp` 已写明）。总线读取放 `poll()`。
4. **文本输入时 `q` / `j` / `/` 不得全局触发。** 输入框获得焦点时，这些字符属于字段内容。
5. **区分"取消等待"与"确认已回滚"。** 这是本项目审计查出的真实问题：写操作中途失败时，**回滚不保证完成**。TUI 里必须如实显示"已回滚"或"状态未知，需人工核验"，**不许显示成成功**。
6. **终止后必须恢复终端状态**，包括正常退出、异常、以及 `Ctrl-C`。curses 里发 CAN 帧，信号路径也要恢复。
7. **`80x24` 是测试用例，不是"禁用功能的许可"。** 板端 `stty size` 返回 `0 0`，面板必须在尺寸不可知时也能画。
8. **渲染时机 = 状态变化时**，不是固定 FPS。已有 `poll_ms=250` 机制，沿用。

---

## 四、要做的三件事

### 任务 A：补 TUI 外壳能力（`tools/hwtui/tui.py`）

现有外壳**缺两样**，已核实：

1. **没有输入框/表单控件**（全文无 `Input` / `Form` / `Field` 类）
2. **`Menu` 没有"按 Enter 切换视图"的机制**（demo 里按 Enter 只打日志）

要做：

- **`Form` / `Field` 控件**：文本字段、数值字段、只读字段；编辑态光标；`Enter` 提交、`Esc` 取消。
  **必须与 `render()` 的纯函数接缝一致**——光标用字符标记表达（如字段末尾 `_`），不要用颜色或下划线属性。
- **视图切换**：`TuiApp` 增加"当前视图"概念，`Menu` 选中后 `Enter` 切到对应视图，`Esc` 返回菜单。
  **保持 `render()` 与 `resolve_size()` 不动**（它们是既有测试的锚点，61 个测试在 `test_tui.py`）。
- 新控件必须有**纯函数级单测**（像 `test_tui.py` 现有的 `render` 逐字节断言那样），不依赖 pty。

**⚠ 不要重写外壳。** 增量扩展，保持既有 API 与行为兼容——`pmp4010.py` 的 `HwPanel` 与 `_DemoApp` 都在用。

### 任务 B：PMP4010 的 TUI 界面

做成菜单 + 视图的结构。**建议形态**（可调整，但每项都要有）：

```
┌─ PMP4010 lift motor A ─────────────┬─ status ────────────────────┐
│ > Status      bus/OD/identity      │ logical bus  : can-2        │
│   Calibrate   read 608F/6091       │ iface        : can1         │
│   Position    single move          │ CAN state    : ERROR-ACTIVE │
│   Params      backup/apply/save    │ od gate      : 7 unverified │
│   Raw frames  listen only          │ rt-controller: active       │
├────────────────────────────────────┴─────────────────────────────┤
│ log                                                               │
│ [i] snapshot: no frames sent                                      │
└───────────────────────────────────────────────────────────────────┘
 q quit  Up/Dn move  Enter select  Esc back
```

**各视图要求**：

| 视图 | 内容 | 关键约束 |
| --- | --- | --- |
| **Status** | 复用 `panel_rows()` 的字段（bus/iface/state/bitrate/rt/od gate） | 只读，**不开总线** |
| **Calibrate** | 读 `608F/6091` 四项，显示换算结果：`1° 电机轴 = ? 用户单位` | 只读；`--apply` 语义是"真读设备" |
| **Position** | 表单：`target` / `velocity` / `accel` / `decel` / `soft-min` / `soft-max` / `max-displacement` / `move-timeout` / `motion-log` | **必须实时显示换算角度**（见下） |
| **Params** | 备份 / 插补批次 / 传输类型批次 / 回滚 / save | 分层确认（见 §三.2） |
| **Raw frames** | 只监听，滚动显示 | 只读 |

**Position 视图的核心要求（这是 TUI 相对 CLI 的真正价值）**：

- 用户填 `--max-displacement` 时，**当场显示它折算成电机轴角度是多少**（用实读标定）。
- 工具另有 **1° 硬顶**（`check_start()` 里 `max_displacement * motor_degrees_per_user_unit > 1` 即拒）。
  **界面要在输入时就提示"还剩多少额度"**，而不是等提交后才报错。
- 目标位移、软行程上下限同样显示角度与相对起点的偏移。
- **`--apply` 是真发帧**：表单底部必须是明确的动作键（如 `[F5] execute`），**不能是回车即跑**。
- 执行中的状态要实时显示（轮询到位 / 超时 / 中止），并且**中止后显示电机是否已确认失能**。

### 任务 C：CLI 与 TUI 共用同一份逻辑

**这是本任务最容易做错的地方。**

- **CLI 子命令全部保留**，行为不变（现有 103 个 Python 测试必须全绿）。
- TUI **不得复制** `validate()` / `check_start()` / `check_envelope()` / `plan()` / `execute()` 的逻辑——
  必须**调用同一批函数**。复制一份的后果是两边判据漂移，而漂移的表现是"CLI 拦住了、TUI 没拦住"。
- 参数校验、安全闸门（`refuse_motion` / `--od-gaps-bypass`）、日志（`--motion-log` JSONL）
  在两条路径上必须**完全一致**。

---

## 五、硬性约束（越过即事故）

1. **不得读取、创建、修改或删除 `harness/` 下的任何内容。**
2. **不得访问板端**（`ssh root@192.168.0.188` 别碰）。本任务纯离线。
3. **不得写任何电机参数、不得运动电机、不得发 NMT。** 开发与测试全程用 `MockBus`。
4. **不得引入第三方依赖**（`curses` 是标准库，可以用）。
5. **不得用颜色、SGR 属性、双宽字形**——见 §二。
6. **不得重写 `render()` / `resolve_size()` 的语义**（61 个既有测试是锚点）。
7. **不得硬编码** `can1` / 节点号 / COB-ID / 周期。
8. **不得提交**（commit/push）。
9. **不得改 `docs/架构设计/` 下的文档**；发现不一致只报告。
10. **不得为跑通降低任何守卫**（OD 闸门、1° 硬顶、save 二次确认、FD 拒绝、DLC 校验）。

---

## 六、验收（必跑，贴原始输出）

```sh
# 构建与三套 CTest（沿用既有基线：各 16/16）
cmake --build build-host -j"$(nproc)" && ctest --test-dir build-host --output-on-failure
cmake --build build-asan  -j"$(nproc)" && ctest --test-dir build-asan
cmake --build build-tsan  -j"$(nproc)" && setarch -R ctest --test-dir build-tsan

# 交叉编译（工具链必须绝对路径）
cmake --build build-arm64 -j"$(nproc)"

# 四个机检闸门 + 三生成器
bash tools/check-drivers.sh; bash tools/check-docs.sh
bash tools/check-deps.sh;    bash tools/check-manual-citations.sh
python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check
python3 -I tools/hwdriver/gen-canopen.py --check
python3 -I tools/hwdriver/gen-pmp4010-access.py --check

# Python 套件（基线：pmp4010 tools 103、hwtui 168、tools 8）
python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p "_test_*.py"
python3 -I -m unittest discover -s tools/hwtui -p "test_*.py"
python3 -I -m unittest discover -s tools/tests -p "test_*.py"
```

### 必须额外做的：真实 PTY 验证

`tui-design` 准则第 8 条：「快照测试不覆盖终端模式与输入协议」。**必须至少跑一次真实 PTY**：

```sh
# 无终端尺寸的降级路径（板端实测 stty size 返回 0 0）
python3 -I tools/hwtui/tui.py --rows 24 --cols 80 --once

# 真实交互：用 script(1) 或 pty 跑一次，验证
#   - 进得去、画得出、按 q 能退
#   - 退出后**终端状态恢复**（光标可见、echo 正常）
script -qec "python3 -I device/drivers/pmp4010/tools/pmp4010.py" /dev/null
```

**把实际输出贴进报告。** 只写"验证通过"不算。

### 必须覆盖的行为

1. 尺寸 `0x0` / `24x80` / 宽屏 三种布局都不崩
2. `q` 退出；**在文本输入框里按 `q` 不退出**（只输入字符）
3. `Esc` 从视图返回菜单，焦点与选中项保持
4. **破坏性操作的确认强度**：save 的确认明显强于写参数
5. **中止/失败后的状态如实显示**（"已回滚" vs "状态未知"）
6. 所有 CLI 子命令行为不变（既有测试全绿）

---

## 七、明确不要做的事

- ✗ 不要访问板端、不要真发帧、不要运动
- ✗ 不要引入颜色、动画、图标、鼠标
- ✗ 不要重写 TUI 外壳或改 `render()` 语义
- ✗ 不要在 TUI 里复制校验逻辑（必须调同一批函数）
- ✗ 不要删掉任何 CLI 子命令
- ✗ 不要动 `harness/`、不要改架构文档、不要提交

---

## 八、交付物

报告写到 `docs/硬件手册/PMP4010/TUI实现报告-2026-10-17.md`，必须含：

1. **改动文件清单**（每文件一句话）
2. **TUI 的界面形态**（贴实际的字符画，不要只描述）
3. **§三 八条设计准则各自怎么落实的**（逐条对应，说不出就说"未落实及原因"）
4. **`render()` 是否被改动**（若改，说明为什么不可避免）
5. **CLI 与 TUI 共用逻辑的证据**（哪些函数被两边调用，`file:line`）
6. **真实 PTY 验证的原始输出**（含退出后终端恢复的验证）
7. **未做的事与原因**
8. **哪一处最没把握**

---

## 九、容易踩的坑

1. **curses 里排不了中文** —— UI 文案用英文，数据可含中文但**不要在 curses 里画**。
2. **`stty size` 返回 0 0** —— 尺寸必须降级，`resolve_size()` 已处理，别绕过它。
3. **`build()` 不能做 I/O** —— 会把一屏画成挤牙膏，且读失败无法降级成文本。
4. **`render()` 是纯函数，有逐字节断言** —— 改它要同步改测试，别让它们悄悄失效。
5. **回调里发异常** —— curses 模式下异常会毁掉终端状态；退出路径要 `try/finally` 恢复。
6. **`Menu.move()` 到头即停、不循环** —— 这是刻意的（"到顶"要可见），别改成循环。
7. **`fit_hints()` 会整条丢弃放不下的提示，不截断** —— 别改成截断。
8. **Ctrl-C 在 curses 里** —— 已有 `TuiApp` 处理，覆盖 `on_key` 时**先调 `super().on_key()`**。
9. **TSan 必须 `setarch -R`**，否则报 `unexpected memory mapping`（不是代码问题）。
10. **报数报实测** —— 本项目出过"说全绿但两个闸门是红的"（git log `2884dd1`）。

---

## 十、做完之后

TUI 做完后，现场手动测试的入口就从 shell 命令变成 TUI 菜单。但**安全边界不变**：

- `--apply` 仍是真发帧；TUI 里也要有明确动作键
- 行程保护仍是**工具侧**唯一防线（`607Dh` = ±21 亿，等于没有）
- 抱闸三项试验仍需人类做（用户已确认过，但记录要留）
- `PdoSession` 仍未接生产，**传输类型仍需保持 `0xFF`**
