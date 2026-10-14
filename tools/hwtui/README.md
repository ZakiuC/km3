# tools/hwtui — 硬件工具共用 TUI 外壳与发现器

零第三方依赖（Python 3 标准库 `curses`）。板端实测 python3 3.10.12、
`import curses` 可用、`/usr/share/terminfo` 完整，因此这是**零新依赖**方案。

| 文件 | 作用 |
| --- | --- |
| `tui.py` | 共用外壳：带边框布局、菜单、表格、滚动日志窗、按键提示状态行 |
| `discover.py` | 硬件工具发现器：扫 `device/drivers/*/tools/*.py` |
| `test_tui.py` | 主机侧回归（`python3 -m unittest`），核心是 `render()` 的逐字节断言 |

## 这不是通用 UI 库

**先读这一节再决定要不要用它。** 它是一套刻意做窄的裁剪，为"看 CAN 报文、
点电机、翻寄存器"这类一次性硬件工具服务。做成通用库的代价（宽字符、
颜色属性、鼠标、布局引擎）没有一个能换回等价的收益。

### 字形边界

只处理 **ASCII 可打印字符 + 单宽制表符号（U+2500–U+257F）**。

- 不做 wcwidth、不做组合字符、不做双宽字形。
- 由此得到唯一一条宽度规则：**一个字符 = 一个格子 = 一次 `len()`**。
  取子串、算列宽、截断、折行全部是 `len()` 和切片，不需要任何库，
  也不需要 UTF-8 解码器。
- **UI 文案一律用英文与符号。** 中文进不了界面。这是接受的代价，
  不是疏漏：要硬排中文就得做东亚字宽（区域指示符、emoji-ZWJ、
  Ambiguous 宽度随 locale 变化……），那是另一个量级的工程。
  **硬件事实照旧用中文写进代码注释与文档**——注释不影响排版。
- 网格里出现的**数据**（CAN 报文、串口、文件名、环境变量）由
  `sanitize()` 兜底：控制字符换成 `.`；默认 `strict=True` 时非 ASCII
  降级为 `?`，因为不测宽度就无法保证它占一格，放行等于允许静默错位。
  `sanitize(text, strict=False)` 是给"我清楚自己终端是什么"的人留的口子。
- 制表符号在 `zh_CN.UTF-8` 等 locale 下属 East Asian Width **Ambiguous**，
  可能被判成双宽。那种终端请用 ASCII 边框：`TuiApp(box="ascii")` 或 `--ascii`。
  这是唯一一处需要用户自己判断的地方，所以给了开关而不是替他决定。
- 不做鼠标。不做颜色与属性（理由见下）。

### 不做颜色与属性

因为 `render()` diff 的是**字符**网格，不是"格子"网格。把属性算进 diff，
网格就得从 `list[str]` 变成 `list[list[(ch, attr)]]`，纯函数的逐字节断言
立刻复杂一个量级。作为交换，本工具箱不会出现"重绘后 SGR 状态不同步"
这类幽灵 bug：选中态用 `>`、日志级别用 `[i]/[+]/[!]/[x]` 这类标记字符表达。

## `render()` 纯函数契约

```python
render(prev_grid: list[str] | None, next_grid: list[str]) -> list[str]
```

**入参**：两帧字符网格，每行等宽（`Grid.to_lines()` 的返回值就是这种形状）。
**出参**：要写给终端的转义行列表。

性质（改之前先读这里，`test_tui.py` 逐字节钉住了每一条）：

1. **没有终端、没有全局状态、没有 I/O、没有 curses。** 同一个 `prev` 与
   `next` 永远产出逐字节相同的输出。这是能在主机上断言的前提。
2. **空帧或零宽帧 → `[]`。** 板端实测 `ssh -tt` 下 `stty size` 返回 `0 0`，
   首屏就是这种帧：不抛异常、不产生任何字节。
3. **`prev` 为 `None` 或两帧形状不同 → 全量重绘。** 形状 = 每行长度的元组，
   所以行数变了、列数变了、行宽不齐了，三种情况一并退化成
   `"\x1b[2J\x1b[H" + 首行`，其后每行以 `"\r\n"` 前缀。总字节数 =
   `7 + sum(len(row)) + 2 * (行数 - 1)`。
4. **否则只发改变了的列**：每个连续变化区间一条
   `"\x1b[<行>;<列>H" + 该区间的新字符`（CSI 是 1 基）。
   相邻变化合并成一条，隔一格就拆两条。
5. **不追踪光标状态**：每个区间自带绝对定位，输出只取决于两帧之差，
   不取决于之前发过什么。这条性质让字节数可以被写死——
   例如同一行连着改 3 个格子恒为 10 字节（`"\x1b[6;11H"` 是 7 + 3 字符），
   三个互不相邻的格子恒为 22 字节（最后一条定位 `"\x1b[1;11H"` 是 7 字节，
   比前两条多一位）。测试里这些数字是手写的字面量，不是程序算的。
6. **不读不写任何东西，不改动入参。**

### 为什么把它做成纯函数

终端 TUI 的常规做法是把 `curses` 的 `addstr` 直接接到业务上，于是
"这一帧到底发了什么"只能靠人肉盯着真终端看——板端 ssh 一卡就什么也验证不了。
把接缝切成 `(帧, 帧) -> 字节` 之后，最强的回归手段变成一条 `unittest`：
不需要 pty、不需要终端、不需要板子，跑在 CI 里。

## 驱动工具如何 import 这个外壳

约定只有一条：**新硬件 = 新目录。**

```
device/drivers/<型号>/tools/<工具>.py      # 目录名用型号，见 docs/硬件手册/README.md
```

`discover.py` 不持有任何硬件清单，所以不存在"加了硬件忘了登记"这回事。
形状刻意很窄（不递归、不认 `.sh`、不认 `device/src`），
好让"为什么我的工具没被列出来"永远只有一个答案。

工具里按习惯 import 即可：

```python
from tui import TuiApp, Grid, Rect, Menu, Table
```

`discover.py --run` 会把工具自身目录与 `tools/hwtui` 一起放进 `sys.path`，
所以不必写 `from tools.hwtui import tui`（那会要求永远从仓库根调用，
而工程师会在任何地方跑它）。

```python
class MyTool(TuiApp):
    title = "PMP4010 scan"
    menu_width = 20          # 0 表示不显示左侧菜单
    log_rows = 7             # None 表示按 1/3 自动

    def on_key(self, name, key):     # 返回 True 请求退出
        ...                          # 覆盖时请先调 super().on_key(name, key)

    def poll(self):                  # 每轮调一次（默认 250ms）：真工具在这里读总线
        ...

    def build(self) -> Grid:         # 每轮调一次：只做排版，不做 I/O
        grid, lay = self.shell_grid()   # 外框 + 分隔线 + 状态行已经画好
        self.table.render(grid, lay.content, self.box)
        self.log.render(grid, lay.log, self.box)
        return grid

if __name__ == "__main__":
    raise SystemExit(MyTool().run())
```

`python3 tools/hwtui/tui.py` 跑的是自带的骨架演示（`_DemoApp`），
可以直接当样板抄；里面的数据全是占位符，不编造厂家事实——
PMP4010 的对象字典与节点号还没拿到手册，所以节点号写 `--`、
状态写 `unconfirmed`。

## 尺寸解析顺序

```
ioctl(TIOCGWINSZ)  ->  环境变量 LINES/COLUMNS  ->  命令行 --rows/--cols  ->  兜底 24x80
```

- 某一维拿到非正值（含板端 `0 0`）时才继续往下走，**两维各自独立**——
  终端报了行、列是 0 时，行用 ioctl、列继续往下找。
- 环境变量非法值（`""`、`0`、`-3`、`abc`）一律忽略，不报错。
- **终端正常时 ioctl 赢，`--rows/--cols` 不生效。** 这是有意的：
  终端自己报的尺寸最可信，命令行选项是坏终端上的修复路径，不是覆盖手段。
  需要强制指定尺寸时请先用 `LINES=30 COLUMNS=90` 那样的环境变量，
  或直接构造 `TuiApp(rows=..., cols=...)`。
- `SIGWINCH` 时重算：信号处理器里只举手，主循环里做
  `resize_term`/`resizeterm` + `clearok`，并强制下一帧全量重绘。
  装不了信号处理器（非主线程）时退化为轮询 `TIOCGWINSZ`。
- 没有 tty 或 `TERM=dumb` 时**不进 curses**：只画一帧到 stdout（纯文本），
  提示写 stderr。`--once` 是同一路径的显式开关。

## 日志窗

`LogView` 按宽度折行（按格子折，即 `len()`），`follow=True` 时钉在底部。
它是**有上限的环形缓冲**（`max_lines`，默认 2000）：板端根分区只剩约 4.1 GiB，
日志窗没有上限就等于给自己埋一个 OOM。写入的文本一律经 `sanitize()`。

## 运行回归

```sh
cd /home/zakiu/km3
python3 -m unittest discover -s tools/hwtui -p test_tui.py -v
python3 tools/hwtui/discover.py --list          # 无硬件工具时输出空列表，退出码 0
```

`discover.py` 用 `ast` 读取工具的第一行 docstring，**不 import 被发现的工具**：
工具可能 import 硬件库（本机不一定有），而"列个表"也不该触发开总线、
写文件这类顶层副作用。坏文件（非法 UTF-8、语法错、空文件）照样被列出来——
沉默地扫掉比报出来更糟。
