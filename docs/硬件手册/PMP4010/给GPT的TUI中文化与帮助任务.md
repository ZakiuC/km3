# 给 GPT 的任务：TUI 中文化与帮助菜单（2026-10-17）

## 任务目标

把 PMP4010 的 TUI 改成**中文界面**，并加**帮助菜单**。

**这两件事互相牵制**：中文是双宽字符，而现有渲染接缝假设「一个字符 = 一个格子」。所以本任务
真正的技术核心不是翻译文案，是**在保住既有回归测试的前提下让网格支持双宽字**。

---

## 零、必须先读

1. `tools/hwtui/tui.py` **全文**，尤其：
   - 文件头 40 行（现行边界声明 —— **本任务要推翻其中一条，见 §二**）
   - `sanitize()` / `fit()` / `_SUPPORTED_RANGES`
   - `render()` / `_changed_runs()` / `_shape()` / `_full_repaint()` / `cursor_to()`
   - `Grid.put()` / `Table` / `Menu` / `Field` / `Form` / `LogView`
2. `tools/hwtui/test_tui.py`（**61 个用例，是本次改造的锚点**）
3. `device/drivers/pmp4010/tools/pmp4010_tui.py`（599 行，五视图）
4. `device/drivers/pmp4010/tools/pmp4010.py` 的 `panel_rows()` —— 它**已经同时返回
   `(key_en, label_zh, value, source)`**，中文标签现成，只是 curses 里没用
5. `device/drivers/pmp4010/tools/pmp4010.py:793` 的 `_display_width()`
6. `docs/架构设计/硬件驱动开发路径.md` §9

---

## 一、已核实的环境事实（直接采用）

| 项 | 值 |
| --- | --- |
| 板上 `LANG` | `zh_CN.UTF-8` |
| `locale -a` | `zh_CN.utf8` 存在 |
| ncurses 版本 | 2.2 |
| `wcwidth` 库 | **宿主与板上都没有** → 不得 import |
| `unicodedata.east_asian_width` | 可用（`中`→`W`，`A`→`Na`） |

**所以宽度测量只能用标准库自己实现。** 好消息：`pmp4010.py:793` 已有 `_display_width()`，
用的正是 `east_asian_width`，**可以提到 `tui.py` 里，两边共用**（不要复制成两份）。

---

## 二、路线：改「格」语义，不改网格模型（**已定，不要另选**）

现状的致命点：`render()` 的 `_changed_runs()` 把**字符下标**直接当**终端列号**用：

```python
out.append(cursor_to(y, start) + after[start:end])
```

纯 ASCII 时 `字符下标 == 列号`，所以一直没事。混入中文后（`中文A` = 3 字符 4 格）就会**错位**。

**选定路线：保留字符网格，在边界处按格子换算。** 理由：

- **ASCII 路径的字节输出必须逐字节不变** —— 于是 `test_tui.py` 那 61 个逐字节断言
  **原样通过**。那套断言是本工具箱最强的回归手段，不能拆。
- 改动集中在少数函数，退回容易。

**明确不要做的事**：不要把 `Grid` 改成 `List[List[str]]` 的格子模型。
那会让 `_full_repaint` 的字节数公式与全部逐字节测试失效 —— 代价远大于收益。

### 2.1 要改的地方（逐条）

| 位置 | 改法 |
| --- | --- |
| `_SUPPORTED_RANGES` / `sanitize()` | 放行**可打印的双宽字符**（`east_asian_width` ∈ {`W`,`F`}）。**控制字符仍然必须替换成 `.`** —— 那是防注入，不是美化，绝不能因为"要中文"而放松 |
| `fit()` | 从"按 `len()` 截断"改为**按格子截断**：累计宽度到 `width` 为止，且**不得把双宽字切成两半** |
| `_changed_runs()` | 返回值语义从"字符区间"改为**明确的列区间**（或同时给出字符区间与列区间），供 `render()` 用 |
| `render()` | `cursor_to(y, start)` 前把起始字符下标换算成**列偏移** |
| `Grid.put()` | 写入时按格子推进；`x`、`self.cols - x` 都是**列**语义 |
| `Table` / `Menu` / `Form` | 列宽计算改用 `_display_width` |
| `draw_box` / `draw_hsep` | 标题与标签的 `fit()` 调用随之按格计算 |

### 2.2 一个必须处理的边界（最容易做错）

**变化的区间边界可能落在双宽字的中间。** 例如前一帧 `中文`、后一帧 `中a`：
字符下标 1 起变化，但列号是 2。若直接按列号 1 定位并发 1 个字符，终端会把 `a`
写在汉字的后半格上 —— **画面撕裂，且不报错**。

要求：**区间边界必须扩到字边界**（向左或向右对齐到双宽字的起始列），并在测试里钉住。

### 2.3 必须新增的测试

1. 纯中文行、中英混排行的 `fit()` 截断（不切半个字）
2. **区间边界落在双宽字中间**的情形（§2.2）
3. 双宽字符的 `Grid.put()` 越界裁剪
4. **ASCII 路径的字节输出与改造前逐字节相同**（回归保护）
5. `sanitize()` 对控制字符仍然替换成 `.`（防注入未被放松）

---

## 三、中文化（范围已定）

**中文为主，技术标识保留英文。** 判据：**协议事实不翻译，界面文案翻译。**

| 要翻译 | 不翻译（保留原文） |
| --- | --- |
| 菜单项、视图标题 | `ERROR-ACTIVE` / `UP` / `DOWN`（协议状态，翻了就查不到手册） |
| 字段标签 | `608F` / `6091` / `60C2` / `1400:02`（对象索引） |
| 按钮与按键提示（`F5` 执行、`Esc` 返回） | `can1` / `can-2`（接口名） |
| 状态描述（"离线"/"已回滚"/"状态未知"） | 型号 `PMPP4010B-CANopen-0HEB-004` |
| 帮助文本 | JSON 字段名、文件路径 |
| 日志的人类可读部分 | `SDO` / `NMT` / `PP` / `CSP` 等协议缩写 |

**`panel_rows()` 的 `label_zh` 直接用**——那列现成就是中文。curses 里改用 `label_zh` 替代
`key_en`。但注意：`panel_rows()` 的注释写着 curses 只能用 `key_en`，
**那条注释的前提是本任务要推翻的**，改完要同步更正注释（不要留一句过期的话在那儿）。

---

## 四、帮助菜单

### 4.1 入口

- **`?`** 键打开帮助；**同时绑 `F1`**（有些终端/复用器把 F1 拦走）
- 帮助是**独立视图**，`Esc` 或 `q` 关闭后**回到原视图与原焦点**（不是回主菜单）
- 在**文本编辑态**按 `?` 必须是普通字符（既有约束：编辑态消费全部按键）

### 4.2 内容分层

| 层级 | 内容 |
| --- | --- |
| **全局** | 五视图各自的用途一行说明；`Enter` 编辑 / `F5` 执行 / `Esc` 返回 / `q` 退出 / `PgUp`/`PgDn` 翻日志 / `?` 帮助 |
| **视图级** | 进哪个视图就显示该视图的键与注意事项 |
| **安全提示（常驻）** | ① `F5` 才发帧，`Enter` 只编辑；② 行程保护只在工具侧（`607Dh` 等于没有）；③ 中止时若读状态字失败，电机可能停在使能态 —— 用独立急停；④ `save` 写 EEPROM 不可逆 |

### 4.3 硬要求

- 帮助内容**必须是数据**（一张清单），不是散落在 `if/elif` 里的字符串 ——
  加视图时只加一条数据，不改渲染代码
- 帮助视图**可滚动**（内容超屏时），复用 `LogView` 或同类的滚动机制
- **帮助里写的键必须真的存在**。写一个没实现的键比不写更糟 —— 与 `fit_hints()` 的
  「整条丢弃、绝不截断」是同一条原则

---

## 五、硬性约束（越过即事故）

1. **不得读取、创建、修改或删除 `harness/` 下的任何内容。**
2. **不得访问板端**（`ssh root@192.168.0.188` 别碰）。本任务纯离线。
3. **不得写任何电机参数、不得运动电机、不得发 NMT。**
4. **不得引入第三方依赖** —— `wcwidth` 也不行，用 `unicodedata`。
5. **不得用颜色、SGR 属性**。中文靠字形，不靠颜色。
6. **ASCII 路径的字节输出必须逐字节不变**（`test_tui.py` 61 个用例原样通过）。
7. **不得放松 `sanitize()` 对控制字符的处理** —— 那是防注入。
8. **不得重写 `render()` 的契约**（`tui.py` 文件头与 `render()` docstring 列了 5 条性质，
   改造后必须仍然成立；不成立的要在报告里说明）。
9. **不得提交**（commit/push）。
10. **不得改 `docs/架构设计/` 下的文档**；发现不一致只报告。
11. **不得为跑通降低任何守卫**（OD 闸门、1° 硬顶、save 两段确认、编辑态按键隔离）。

---

## 六、验收（必跑，贴原始输出）

```sh
# Python（基线：pmp4010 tools 133、hwtui 197、tools 8）
python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p "_test_*.py"
python3 -I -m unittest discover -s tools/hwtui -p "test_*.py"
python3 -I -m unittest discover -s tools/tests -p "test_*.py"

# C++（基线：三套各 16/16）
cmake --build build-host -j"$(nproc)" && ctest --test-dir build-host --output-on-failure
cmake --build build-host -j"$(nproc)" && ctest --test-dir build-asan 2>/dev/null || ctest --test-dir build-asan
cmake --build build-tsan -j"$(nproc)" && setarch -R ctest --test-dir build-tsan

# 交叉构建
cmake --build build-arm64 -j"$(nproc)"

# 四闸门 + 三生成器
bash tools/check-drivers.sh; bash tools/check-docs.sh
bash tools/check-deps.sh;    bash tools/check-manual-citations.sh
python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check
python3 -I tools/hwdriver/gen-canopen.py --check
python3 -I tools/hwdriver/gen-pmp4010-access.py --check
```

### 必须额外做的

**① ASCII 回归的字节级证明**：在改造前后的 `render()` 上跑同一组纯 ASCII 输入，
**比对输出字节完全相同**。贴对比。

**② 双宽字的真实 PTY 验证**：

```sh
# 必须设 UTF-8 locale，否则看到的是乱码而不是宽度问题
LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8 \
  script -qec "python3 -I device/drivers/pmp4010/tools/pmp4010.py --rows 30 --cols 100" /dev/null
```

用 `pty.openpty` + `TIOCSWINSZ` 起真实 curses 子进程（沿用你上次的做法），
**贴出解码后的字符画**，证明中文实际排版正确。

**③ 帮助菜单的可达性**：`?` 与 `F1` 都能打开；`Esc`/`q` 关闭后**回到原视图**；
编辑字段时按 `?` 是普通字符。

### 必须覆盖的行为

1. 五视图在中英混排下的布局对齐（**没有一列错位**）
2. `fit()` 截断不切半个字
3. **区间边界落在双宽字中间**（§2.2）—— 这条是本次改造的核心风险
4. 帮助视图的内容与当前视图一致；超屏可滚动
5. 终端尺寸 `0×0` / `24×80` 仍不崩
6. 既有 61 个 `test_tui.py` 用例**全部原样通过**

---

## 七、明确不要做的事

- ✗ 不要把 `Grid` 改成格子模型
- ✗ 不要移除或改写 `test_tui.py` 的既有断言来"让它们通过"
- ✗ 不要引入 `wcwidth` 或任何第三方库
- ✗ 不要翻译协议状态、对象索引、接口名、型号（见 §三 判据）
- ✗ 不要用颜色/属性表达状态
- ✗ 不要写帮助里没实现的按键
- ✗ 不要动 `harness/`、不要改架构文档、不要提交

---

## 八、交付物

报告写到 `docs/硬件手册/PMP4010/TUI中文化报告-2026-10-17.md`，必须含：

1. **改动文件清单**
2. **双宽字改造的说明**：`render()` 的 5 条契约改造后是否仍成立？若不成立，逐条说明原因
3. **ASCII 字节级回归对比**（贴实际输出）
4. **中文排版的真实 PTY 字符画**（解码后）
5. **§2.2 那个边界情形怎么测的**（贴测试代码与实际输出）
6. **帮助菜单的完整文案**（贴出来，不要只说"已实现"）
7. **未做的事与原因**
8. **哪一处最没把握**

---

## 九、容易踩的坑

1. **`cursor_to` 的列号是 1 基**，字符下标是 0 基 —— 换算时别丢这个偏移。
2. **`_shape()` 用 `len(row)`** —— 若 `Grid.put()` 改成按格推进，`len(row)` 就不再等于列数。
   要么让行字符串长度仍等于列数（双宽字占 2 个位置），要么同步改 `_shape()`。
   **这一点必须先想清楚再动手**，它决定整个改造的形状。
3. **`fit_hints()` 的"整条丢弃"逻辑**不能因为宽度语义变化而失效。
4. **编辑态 `Field.cursor` 是字符下标**，不是列号；光标 `_` 的绘制位置要按格算。
5. **`LogView.append()` 的折行按 `len()`** —— 中文日志会折错，要一并处理。
6. **curses 自身的宽度行为**：`addstr` 遇双宽字会占 2 格，但我们走的是
   **自绘网格 + ANSI 转义**（见 `_write`），所以要自己算准。
7. **TSan 必须 `setarch -R`**。
8. **报数报实测** —— 本项目出过"说全绿但两个闸门是红的"（git log `2884dd1`）。
