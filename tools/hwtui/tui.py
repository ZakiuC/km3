#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""km3 硬件工具共用 TUI 外壳 —— Python 3 标准库（curses），零第三方依赖。

为什么是标准库
    板端实测 python3 3.10.12、import curses 可用、/usr/share/terminfo 完整，
    所以共用外壳不需要引入任何新依赖。板端根分区只剩约 4.1 GiB，
    也没有必要为"画个框"装一个包。

字形边界（完整理由见 README.md）
    网格仍是 list[str]；行的字符数可变，终端列宽固定。标准库 unicodedata
    的 W/F 字形占两列，ASCII 与制表符号占一列，Ambiguous 按一列处理。
    不处理组合字符或 emoji 字形簇；控制字符始终替换，不能注入终端指令。

渲染接缝（本文件最重要的设计）
    render(prev_grid, next_grid) -> list[str] 是纯函数：
    吃两帧字符网格（list[str]，逐行等宽），吐一串转义序列。
    没有终端、没有全局状态、没有 I/O、没有 curses。
    因此它能在主机上逐字节断言——改了 3 个格子，输出的字节数是一个
    可以写死的常数，不需要 pty、不需要真终端。这是本工具箱最强的回归手段。

不做颜色与属性
    因为 render() 是**字符**网格的 diff，不是"格子"网格的 diff。
    把属性算进去，网格就得变成 (字符, 属性) 的格子，纯函数的断言立刻
    复杂一个量级。作为交换，本工具箱没有"重绘后 SGR 状态不同步"这类
    幽灵 bug：选中态用 '>'、失败用 'x' 这类标记字符表达。

用法（硬件工具侧）
    from tui import TuiApp, Grid, Rect, Layout, Menu, Table, LogView

    class MyTool(TuiApp):
        title = "PMP4010 scan"
        menu_width = 20
        def build(self):
            grid, lay = self.shell_grid()
            ...
            return grid

    if __name__ == "__main__":
        raise SystemExit(MyTool().run())

    见 README.md 的"驱动工具如何 import 这个外壳"一节（discover.py --run 会替你设好路径）。
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import unicodedata
from collections import deque, namedtuple
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Sequence, Tuple

try:  # 没有 curses 的宿主也能 import 本模块来跑纯函数（主机回归测试就是这么干的）
    import curses  # type: ignore
except Exception:  # pragma: no cover - ImportError 之外还有 terminfo 缺失等
    curses = None  # type: ignore[assignment]

__all__ = [
    "ESC",
    "DEFAULT_ROWS",
    "DEFAULT_COLS",
    "Grid",
    "Rect",
    "Layout",
    "render",
    "sanitize",
    "_display_width",
    "fit",
    "draw_box",
    "draw_hsep",
    "draw_vsep",
    "Menu",
    "Field",
    "Form",
    "Table",
    "LogView",
    "TuiApp",
    "resolve_size",
    "key_name",
]

ESC = "\x1b"
CLEAR_SCREEN = ESC + "[2J"
CURSOR_HOME = ESC + "[H"
DEFAULT_ROWS = 24
DEFAULT_COLS = 80
TRUNCATE_MARK = "~"

#: 单宽制表符号（unicode 边框）。这些字符在非 CJK locale 下宽度为 1；
#: 在 zh_CN.UTF-8 等 locale 下属于 East Asian Width "Ambiguous"，可能被判成 2——
#: 那种终端请用 ascii 边框（TuiApp(box="ascii") / --ascii）。
BOX_UNICODE = {
    "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "h": "─", "v": "│", "lt": "├", "rt": "┤",
}
BOX_ASCII = {
    "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "h": "-", "v": "|", "lt": "+", "rt": "+",
}

# 受支持的字符区间：ASCII 可打印 + 单宽制表符号。
_SUPPORTED_RANGES = ((0x20, 0x7E), (0x2500, 0x257F))


def box_chars(style: str = "unicode") -> Dict[str, str]:
    return BOX_ASCII if style == "ascii" else BOX_UNICODE


def _display_width(text: str) -> int:
    """终端列宽：East Asian Width W/F 占两列，其余占一列。

    组合字符/字形簇不在网格支持范围内；先用 sanitize(..., wide=True)
    清理外部文本。Ambiguous 按一列，终端采用双宽策略时请用 ASCII 框线。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def _is_supported(ch: str, wide: bool = False) -> bool:
    o = ord(ch)
    return (any(lo <= o <= hi for lo, hi in _SUPPORTED_RANGES)
            or (wide and unicodedata.east_asian_width(ch) in ("W", "F")))


def sanitize(text: str, strict: bool = True, wide: bool = False) -> str:
    """把不能安全落进网格的字符换掉。这是防注入，不是美化。

    网格里会出现来自 CAN 报文、串口、文件名、环境变量的字节。若放行 ESC，
    一段损坏或恶意的数据就能改写终端状态（清屏、伪造提示行、把光标搬走）。
    控制字符一律换成 '.'。

    wide=True 放行可打印的 W/F 双宽字形（Grid 与控件显式启用）。默认值保留
    旧调用方的 ASCII/框线约定。strict=False 还允许其他单宽可打印字符。
    两种模式都拒绝控制/格式字符与组合字符，避免注入与不可计量的字形簇。
    """
    out = []
    for ch in text:
        category = unicodedata.category(ch)
        if not ch.isprintable() or category.startswith("C"):
            out.append(".")
        elif category.startswith("M") or (strict and not _is_supported(ch, wide)):
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)


def _glyphs(text: str):
    """临时列跨度；持久网格仍是行字符串。"""
    col = 0
    for ch in text:
        end = col + _display_width(ch)
        yield col, end, ch
        col = end


def _column_slice(text: str, start: int, end: int) -> str:
    """取列区间；相交的半个双宽字用空格擦除，不留下孤立半字。"""
    out = []
    for left, right, ch in _glyphs(text):
        if left >= end:
            break
        if right <= start:
            continue
        out.append(ch if start <= left and right <= end
                   else " " * (min(end, right) - max(start, left)))
    return "".join(out)


def _prefix_columns(text: str, width: int) -> str:
    out = []
    for left, right, ch in _glyphs(text):
        if right > width:
            break
        out.append(ch)
    return "".join(out)


def fit(text: str, width: int, mark: str = TRUNCATE_MARK) -> str:
    """按终端列截断，不切半个字。被截断时保留完整的 mark。"""
    if width <= 0:
        return ""
    text = sanitize(text, strict=False, wide=True)
    if _display_width(text) <= width:
        return text
    mark = _prefix_columns(sanitize(mark, strict=False, wide=True), width)
    return _prefix_columns(text, width - _display_width(mark)) + mark


# --------------------------------------------------------------------------
# 尺寸解析：ioctl(TIOCGWINSZ) -> LINES/COLUMNS -> --rows/--cols -> 24x80
# --------------------------------------------------------------------------
def _ioctl_size() -> Tuple[int, int]:
    """TIOCGWINSZ。拿不到（非 tty / ioctl 失败 / 板端返回 0 0）时返回 (0, 0)。"""
    try:
        import fcntl
        import struct
        import termios
    except Exception:  # pragma: no cover - 非 POSIX
        return (0, 0)
    for fd in (sys.stdout, sys.stderr, sys.stdin):
        try:
            buf = fcntl.ioctl(fd.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
        except Exception:
            continue
        try:
            rows, cols = struct.unpack("HHHH", buf)[:2]
        except Exception:
            continue
        if rows > 0 and cols > 0:
            return int(rows), int(cols)
        # 板端实测：ssh -tt 下 stty size 返回 0 0，此时必须继续往下走，不能当成"20 行"。
    return (0, 0)


def _env_int(env, key: str) -> int:
    try:
        v = env.get(key)
    except Exception:
        return 0
    if v is None:
        return 0
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def resolve_size(rows=None, cols=None, ioctl=None, env=None) -> Tuple[int, int]:
    """解析终端尺寸，返回 (rows, cols)，两者都 >= 1。

    顺序：ioctl(TIOCGWINSZ) -> 环境变量 LINES/COLUMNS -> 传入的 rows/cols -> 24x80。
    某一维拿到非正值时才继续往下走，两维各自独立——这正是板端
    "stty size 返回 0 0" 场景的用武之地。

    注意这个顺序的后果：终端正常时 ioctl 赢，--rows/--cols 不生效。
    这是有意的——终端自己报的尺寸最可信，命令行选项是坏终端上的修复路径，
    不是覆盖手段。见 README.md「尺寸解析顺序」。
    """
    ioctl_fn = ioctl if ioctl is not None else _ioctl_size
    env_map = os.environ if env is None else env
    try:
        io_rows, io_cols = ioctl_fn()
    except Exception:
        io_rows, io_cols = 0, 0

    def pick(io_val, env_key, forced, default):
        for cand in (int(io_val or 0), _env_int(env_map, env_key), int(forced or 0)):
            if cand > 0:
                return cand
        return default

    out_rows = pick(io_rows, "LINES", rows, DEFAULT_ROWS)
    out_cols = pick(io_cols, "COLUMNS", cols, DEFAULT_COLS)
    return out_rows, out_cols


# --------------------------------------------------------------------------
# 纯函数渲染接缝
# --------------------------------------------------------------------------
def _shape(grid: Sequence[str]) -> Tuple[int, ...]:
    """把一帧的"形状"表达成每行终端列宽组成的元组。

    行数不同、宽度不同、行宽不齐——三种情况一次性全captured，
    所以形状不等就等于"不能逐格 diff，必须全量重绘"。
    """
    return tuple(_display_width(row) for row in grid)


def cursor_to(row: int, col: int) -> str:
    """0 基 (row, col) -> 绝对定位转义序列（CSI 是 1 基）。"""
    return ESC + "[%d;%dH" % (row + 1, col + 1)


def _changed_runs(before: str, after: str) -> List[Tuple[int, int]]:
    """变化列区间，边界必须同时是新旧帧的字形边界。

    共同边界一次完成传递扩张：错开一列的连续双宽字可能一直扩到行尾。
    """
    def columns(text):
        cells, boundaries = [], {0}
        for left, right, ch in _glyphs(text):
            cells.extend((ch, offset) for offset in range(right - left))
            boundaries.add(right)
        return cells, boundaries

    old, old_edges = columns(before)
    new, new_edges = columns(after)
    edges = sorted(old_edges & new_edges)
    runs: List[Tuple[int, int]] = []
    for start, end in zip(edges, edges[1:]):
        if old[start:end] != new[start:end]:
            if runs and runs[-1][1] == start:
                runs[-1] = (runs[-1][0], end)
            else:
                runs.append((start, end))
    return runs


def _full_repaint(grid: Sequence[str]) -> List[str]:
    """全量重绘：清屏 + 回原点，然后每行以 CRLF 分隔。

    返回的第一项是 "\x1b[2J\x1b[H" + 第 0 行；其后每项是 "\r\n" + 对应行。
    UTF-8 总字节数 = 7 + sum(len(row.encode("utf-8"))) + 2 * (行数 - 1)。
    """
    out = [CLEAR_SCREEN + CURSOR_HOME + grid[0]]
    out.extend("\r\n" + row for row in grid[1:])
    return out


def render(prev_grid: Optional[Sequence[str]], next_grid: Sequence[str]) -> List[str]:
    """渲染接缝。纯函数：同样的两帧永远产出逐字节相同的输出。

    入参是两帧安全字符网格（每行等显示列宽；Grid.to_lines() 返回这种形状），
    出参是要写给终端的转义行列表。

    契约（改动前请先读 README.md，测试逐字节钉住了这些性质）：

    1. next_grid 为空帧或零宽 -> 返回 []。板端首屏尺寸为 0x0 时走这条路，
       不抛异常、不产生任何字节。这是明确要求的行为，不是"顺手兼容"。
    2. prev_grid 为 None 或两帧形状不同 -> 全量重绘（见 _full_repaint）。
    3. 否则逐行逐格比较，只发"改变了的列"：
       每个连续变化区间一条 "\x1b[<行>;<列>H" + 该区间的新字符。
       双宽字要求区间扩到新旧帧共同字形边界，可能连带重画相邻未变格。
    4. 不追踪光标状态：每个区间自带绝对定位，所以输出只取决于两帧之差，
       不取决于之前发过什么。这条性质让字节数可以被写死。
    5. 不读不写任何东西，不改动入参。
    """
    next_lines = list(next_grid)
    if not next_lines or all(len(row) == 0 for row in next_lines):
        return []
    prev_lines = None if prev_grid is None else list(prev_grid)
    if prev_lines is None or _shape(prev_lines) != _shape(next_lines):
        return _full_repaint(next_lines)
    out: List[str] = []
    for y, (before, after) in enumerate(zip(prev_lines, next_lines)):
        for start, end in _changed_runs(before, after):
            out.append(cursor_to(y, start) + _column_slice(after, start, end))
    return out


# --------------------------------------------------------------------------
# 几何与网格
# --------------------------------------------------------------------------
class Rect(namedtuple("RectBase", "y x h w")):
    """终端矩形，坐标 0 基。尺寸退化时 h/w 可以是 0——所有绘制函数都必须容忍。"""

    __slots__ = ()

    @property
    def valid(self) -> bool:
        return self.h > 0 and self.w > 0

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def inner(self, n: int = 1) -> "Rect":
        return Rect(self.y + n, self.x + n, max(0, self.h - 2 * n), max(0, self.w - 2 * n))


class Layout(namedtuple("LayoutBase", "outer body menu content log status")):
    """共用外壳的版面。纯几何计算，不知道里面画什么。

    0x0 或小到画不下时，除 outer 外全是零矩形；widget 对无效矩形一律不画。
    """

    __slots__ = ()

    @staticmethod
    def compute(rows: int, cols: int, menu_w: int = 0, log_rows: Optional[int] = None) -> "Layout":
        outer = Rect(0, 0, max(0, rows), max(0, cols))
        empty = Rect(0, 0, 0, 0)
        inner = outer.inner()
        if inner.h < 4 or inner.w < 8:
            return Layout(outer, empty, empty, empty, empty, empty)
        h, w = inner.h, inner.w
        if log_rows is None:
            log_rows = max(3, h // 3)
        log_rows = max(0, min(int(log_rows), h - 4))  # 至少要留出 1 行主体 + 状态 + 分隔
        body_h = h - 1 - log_rows          # 减状态行，(h-1)
        if log_rows > 0:
            body_h -= 1                    # 再减日志上方的分隔线
        body = Rect(inner.y, inner.x, max(1, body_h), w)
        status = Rect(inner.y + h - 1, inner.x, 1, w)
        log = empty
        if log_rows > 0:
            # 日志紧贴状态行上方：底边 = status.y - 1，因此 y = status.y - log_rows。
            # （曾经写成 status.y - 1 - log_rows，日志会吃掉状态行一行。）
            log = Rect(status.y - log_rows, inner.x, log_rows, w)
        menu = empty
        content = body
        if menu_w and body.w >= menu_w + 4:
            menu = Rect(body.y, body.x, body.h, int(menu_w))
            content = Rect(body.y, body.x + menu_w + 1, body.h, body.w - menu_w - 1)
        return Layout(outer, body, menu, content, log, status)


class Grid:
    """定宽字符网格。行数/列数可为 0（板端 stty size 返回 0 0 时就是这种）。"""

    def __init__(self, rows: int, cols: int, fill: str = " ", strict: bool = True) -> None:
        self.rows = max(0, int(rows))
        self.cols = max(0, int(cols))
        self.strict = strict
        self._fill = sanitize((fill or " ")[:1], strict, wide=True)
        if _display_width(self._fill) != 1:
            self._fill = " "
        self._lines: List[str] = [self._fill * self.cols for _ in range(self.rows)]

    @property
    def shape(self) -> Tuple[int, int]:
        return (self.rows, self.cols)

    def to_lines(self) -> List[str]:
        return list(self._lines)

    def put(self, y: int, x: int, text: str) -> None:
        """在 (y, x) 写 text。越界一律裁掉，绝不抛异常。负数 x 会裁掉左边。"""
        if self.rows <= 0 or self.cols <= 0 or not text:
            return
        if not (0 <= y < self.rows):
            return
        text = sanitize(text, self.strict, wide=True)
        start, end = max(0, x), min(self.cols, x + _display_width(text))
        if start >= end:
            return
        piece = _column_slice(text, start - x, end - x)
        line = self._lines[y]
        self._lines[y] = (_column_slice(line, 0, start) + piece
                          + _column_slice(line, end, self.cols))

    def fill_rect(self, rect: Rect, ch: str = " ") -> None:
        if not rect.valid:
            return
        for i in range(rect.h):
            self.put(rect.y + i, rect.x, _column_slice(
                sanitize(ch, self.strict, wide=True) * rect.w, 0, rect.w))

    def hline(self, y: int, x: int, w: int, ch: str = "-") -> None:
        if w > 0:
            self.put(y, x, _column_slice(sanitize(ch, self.strict, wide=True) * w, 0, w))

    def box(self, rect: Rect, title: Optional[str] = None, style: str = "unicode") -> None:
        draw_box(self, rect, title, style)


def draw_box(grid: Grid, rect: Rect, title: Optional[str] = None, style: str = "unicode") -> None:
    """画带边框的矩形；rect 小到画不下框时退化成一条横线。"""
    b = box_chars(style)
    if not rect.valid:
        return
    if rect.h < 2 or rect.w < 2:
        grid.hline(rect.y, rect.x, rect.w, b["h"])
        return
    mid = b["h"] * (rect.w - 2)
    grid.put(rect.y, rect.x, b["tl"] + mid + b["tr"])
    for i in range(1, rect.h - 1):
        grid.put(rect.y + i, rect.x, b["v"])
        grid.put(rect.y + i, rect.x + rect.w - 1, b["v"])
    grid.put(rect.y + rect.h - 1, rect.x, b["bl"] + mid + b["br"])
    if title:
        grid.put(rect.y, rect.x + 2, " " + fit(sanitize(title, grid.strict, wide=True), max(0, rect.w - 5)) + " ")


def draw_hsep(grid: Grid, y: int, x: int, w: int, style: str = "unicode", label: Optional[str] = None) -> None:
    """横分隔线，可带居中偏左的标签（用来标注下方区域）。"""
    b = box_chars(style)
    if w <= 0:
        return
    if w < 2:
        grid.hline(y, x, w, b["h"])
        return
    grid.put(y, x, b["lt"] + b["h"] * (w - 2) + b["rt"])
    if label:
        grid.put(y, x + 2, " " + fit(sanitize(label, grid.strict, wide=True), max(0, w - 5)) + " ")


def draw_vsep(grid: Grid, x: int, y: int, h: int, style: str = "unicode") -> None:
    b = box_chars(style)
    for i in range(max(0, h)):
        grid.put(y + i, x, b["v"])


# --------------------------------------------------------------------------
# 按键解码
# --------------------------------------------------------------------------
_KEY_TABLE_CACHE: Optional[Dict[int, str]] = None


def _key_table() -> Dict[int, str]:
    global _KEY_TABLE_CACHE
    if _KEY_TABLE_CACHE is None:
        table: Dict[int, str] = {}
        if curses is not None:
            named = (
                ("up", "KEY_UP"), ("down", "KEY_DOWN"), ("left", "KEY_LEFT"), ("right", "KEY_RIGHT"),
                ("home", "KEY_HOME"), ("end", "KEY_END"), ("pgup", "KEY_PPAGE"), ("pgdn", "KEY_NPAGE"),
                ("backspace", "KEY_BACKSPACE"), ("delete", "KEY_DC"), ("resize", "KEY_RESIZE"),
                ("enter", "KEY_ENTER"),
            )
            for name, attr in named:
                val = getattr(curses, attr, None)
                if val is not None:
                    table[int(val)] = name
            for i in range(1, 13):
                val = getattr(curses, "KEY_F%d" % i, None)
                if val is not None:
                    table[int(val)] = "f%d" % i
        _KEY_TABLE_CACHE = table
    return _KEY_TABLE_CACHE


def key_name(key: int) -> str:
    """把 curses getch() 的返回值解码成稳定名字：'up'、'enter'、'q'、'f5'…

    未知键返回空串（调用方应当忽略它，而不是拿它当字符）。
    """
    if key is None:
        return ""
    if key in (10, 13):
        return "enter"
    if key == 27:
        return "esc"
    if key == 9:
        return "tab"
    if key in (8, 127):
        return "backspace"
    if key >= 256:
        return _key_table().get(int(key), "")
    ch = chr(key)
    if 0x20 <= key <= 0x7E:
        return ch
    return ""


# --------------------------------------------------------------------------
# 控件（都是"往网格里画"，不认识 curses，也不认识纯函数 render）
# --------------------------------------------------------------------------
class Menu:
    """单选菜单。选中用 '>' 标记，不用颜色——见文件头的"不做颜色与属性"。"""

    def __init__(self, items: Optional[Sequence[str]] = None, selected: int = 0, marker: str = ">") -> None:
        self.items: List[str] = list(items or [])
        self.selected = 0
        self.offset = 0
        self.marker = marker
        self.set_selected(selected)

    def set_items(self, items: Sequence[str]) -> None:
        self.items = list(items)
        self.set_selected(self.selected)

    def set_selected(self, idx: int) -> None:
        if not self.items:
            self.selected = 0
            self.offset = 0
            return
        self.selected = max(0, min(int(idx), len(self.items) - 1))

    @property
    def current(self) -> Optional[str]:
        if not self.items:
            return None
        return self.items[self.selected]

    def move(self, delta: int) -> None:
        """上下移动，到头即停（不循环——循环会让长列表里"到顶"这件事不可见）。"""
        if self.items:
            self.set_selected(self.selected + delta)

    def ensure_visible(self, height: int) -> None:
        if height <= 0 or not self.items:
            return
        if self.selected < self.offset:
            self.offset = self.selected
        elif self.selected >= self.offset + height:
            self.offset = self.selected - height + 1
        self.offset = max(0, min(self.offset, max(0, len(self.items) - height)))

    def render(self, grid: Grid, rect: Rect, style: str = "unicode") -> None:
        if not rect.valid:
            return
        self.ensure_visible(rect.h)
        for i in range(rect.h):
            idx = self.offset + i
            if idx >= len(self.items):
                break
            prefix = self.marker + " " if idx == self.selected else "  "
            grid.put(rect.y + i, rect.x, fit(prefix + self.items[idx], rect.w))


class Field:
    """One text input (ASCII keyboard), with a separate draft until Enter commits it.

    ``number`` checks only numeric syntax and finiteness. Device limits and
    units remain the caller's responsibility. A readonly field never edits.
    The cursor is an inserted '_' character, so the renderer needs no attrs.
    """

    def __init__(self, name: str, value: object = "", label: Optional[str] = None,
                 kind: str = "text", max_length: int = 1024) -> None:
        if kind not in ("text", "number", "readonly"):
            raise ValueError("unknown field kind: %s" % kind)
        self.name = name
        self.label = label if label is not None else name
        self.kind = kind
        self.value = str(value)
        self.max_length = max(1, int(max_length))
        self.editing = False
        self.cursor = 0
        self.error = ""
        self._draft = self.value

    @property
    def text(self) -> str:
        return self._draft if self.editing else self.value

    def begin_edit(self) -> bool:
        if self.kind == "readonly":
            return False
        self._draft = self.value
        self.cursor = len(self._draft)
        self.error = ""
        self.editing = True
        return True

    def cancel(self) -> None:
        self.editing = False
        self._draft = self.value
        self.cursor = len(self.value)
        self.error = ""

    def commit(self) -> bool:
        if not self.editing:
            return False
        if self.kind == "number":
            try:
                valid = Decimal(self._draft.strip()).is_finite()
            except (InvalidOperation, ValueError):
                valid = False
            if not valid:
                self.error = "finite number required"
                return False
        self.value = self._draft
        self.editing = False
        self.error = ""
        return True

    def on_key(self, name: str, key: int) -> bool:
        """Consume every key while editing, including global shortcuts.

        Ctrl-C belongs to the application and is deliberately left unconsumed.
        """
        if not self.editing or key == 3:
            return False
        if name == "enter":
            self.commit()
        elif name == "esc":
            self.cancel()
        elif name == "left":
            self.cursor = max(0, self.cursor - 1)
        elif name == "right":
            self.cursor = min(len(self._draft), self.cursor + 1)
        elif name == "home":
            self.cursor = 0
        elif name == "end":
            self.cursor = len(self._draft)
        elif name == "backspace" and self.cursor:
            self._draft = self._draft[:self.cursor - 1] + self._draft[self.cursor:]
            self.cursor -= 1
            self.error = ""
        elif name == "delete":
            self._draft = self._draft[:self.cursor] + self._draft[self.cursor + 1:]
            self.error = ""
        elif len(name) == 1 and 0x20 <= ord(name) <= 0x7E:
            if len(self._draft) < self.max_length:
                self._draft = self._draft[:self.cursor] + name + self._draft[self.cursor:]
                self.cursor += 1
                self.error = ""
        return True

    def display(self, width: int) -> str:
        """Return a clipped, sanitized value while keeping the cursor visible."""
        if width <= 0:
            return ""
        text = sanitize(self.text, wide=True)
        if not self.editing:
            return fit(text + (" [ro]" if self.kind == "readonly" else ""), width)
        # cursor 始终是草稿的字符下标；滚动窗口的起点必须落在字形边界。
        cursor_col = _display_width(text[:self.cursor])
        text = text[:self.cursor] + "_" + text[self.cursor:]
        first_col = max(0, cursor_col - width + 1)
        start = next((left for left, _, _ in _glyphs(text) if left >= first_col), 0)
        return _prefix_columns(_column_slice(text, start, _display_width(text)), width)


class Form:
    """A scrollable collection of fields; Enter edits/commits, Esc cancels.

    ``values`` contains committed strings; ``current.text`` includes the draft
    for live previews. Form handling has no device actions or I/O.
    """

    def __init__(self, fields: Sequence[Field], selected: int = 0) -> None:
        self.fields = list(fields)
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("field names must be unique")
        self.selected = max(0, min(int(selected), len(self.fields) - 1))
        self.offset = 0

    @property
    def current(self) -> Optional[Field]:
        return self.fields[self.selected] if self.fields else None

    @property
    def editing(self) -> bool:
        return bool(self.current and self.current.editing)

    @property
    def values(self) -> Dict[str, str]:
        return {field.name: field.value for field in self.fields}

    def on_key(self, name: str, key: int) -> bool:
        if self.editing:
            return self.current.on_key(name, key)
        if key == 3 or not self.fields:
            return False
        if name in ("up", "k", "down", "j", "tab"):
            delta = -1 if name in ("up", "k") else 1
            self.selected = max(0, min(self.selected + delta, len(self.fields) - 1))
            return True
        if name == "enter":
            self.current.begin_edit()
            return True
        return False

    def render(self, grid: Grid, rect: Rect, style: str = "unicode") -> None:
        if not rect.valid:
            return
        if self.selected < self.offset:
            self.offset = self.selected
        elif self.selected >= self.offset + rect.h:
            self.offset = self.selected - rect.h + 1
        self.offset = max(0, min(self.offset, max(0, len(self.fields) - rect.h)))
        for row, field in enumerate(self.fields[self.offset:self.offset + rect.h]):
            marker = "x " if field.error else ("> " if row + self.offset == self.selected else "  ")
            label_width = min(_display_width(sanitize(field.label, wide=True)) + 2, max(0, rect.w - 6))
            label = fit(sanitize(field.label, wide=True) + ": ", label_width)
            prefix = marker + label
            value = field.display(max(0, rect.w - _display_width(prefix)))
            grid.put(rect.y + row, rect.x, fit(prefix + value, rect.w))


class Table:
    """带表头、可滚动的文本表。列宽按内容算，再按可用宽度收缩。"""

    def __init__(
        self,
        headers: Optional[Sequence[str]] = None,
        rows: Optional[Sequence[Sequence[object]]] = None,
        gap: str = " ",
    ) -> None:
        self.headers: List[str] = [str(h) for h in (headers or [])]
        self.rows: List[List[str]] = [[_cell(c) for c in row] for row in (rows or [])]
        self.gap = gap
        self.selected = 0
        self.offset = 0
        self._widths: List[int] = []

    def set_rows(self, rows: Sequence[Sequence[object]]) -> None:
        self.rows = [[_cell(c) for c in row] for row in rows]
        self.set_selected(self.selected)

    def set_selected(self, idx: int) -> None:
        if not self.rows:
            self.selected = 0
            self.offset = 0
            return
        self.selected = max(0, min(int(idx), len(self.rows) - 1))

    @property
    def current(self) -> Optional[List[str]]:
        if not self.rows:
            return None
        return self.rows[self.selected]

    def move(self, delta: int) -> None:
        if self.rows:
            self.set_selected(self.selected + delta)

    def page(self, delta: int, height: int) -> None:
        self.move(delta * max(1, height - 1))

    def ensure_visible(self, height: int) -> None:
        if height <= 0 or not self.rows:
            return
        if self.selected < self.offset:
            self.offset = self.selected
        elif self.selected >= self.offset + height:
            self.offset = self.selected - height + 1
        self.offset = max(0, min(self.offset, max(0, len(self.rows) - height)))

    def _sep_width(self, ncols: int, style: str) -> int:
        return (_display_width(sanitize(self.gap, wide=True)) * 2 + 1) * max(0, ncols - 1)

    def resolve_widths(self, avail: int, style: str = "unicode") -> List[int]:
        """算列宽：先取内容自然宽度，再按可用宽度收缩；放不下的列整列丢掉。

        丢掉而不是继续压缩，是因为列宽压到 1 时表格已经不可读，
        还不如少显示一列（"列不全"是可理解的，"列糊成一团"不是）。

        返回值长度可能小于总列数，调用方必须按 len(widths) 取单元格。
        """
        ncols = max(len(self.headers), max((len(r) for r in self.rows), default=0))
        if ncols <= 0:
            self._widths = []
            return []
        natural = [0] * ncols
        for row in [self.headers] + self.rows:
            for c_i in range(min(ncols, len(row))):
                natural[c_i] = max(natural[c_i], _display_width(sanitize(row[c_i], wide=True)))
        natural = [max(1, w) for w in natural]
        widths = list(natural)
        while sum(widths) + self._sep_width(len(widths), style) > avail and max(widths) > 3:
            widths[widths.index(max(widths))] -= 1

        # 仍放不下 -> 整列丢弃（末尾列先丢：视口宽度不够时，
        # 靠左的列是主键/状态这类更要紧的内容）。
        while len(widths) > 1 and sum(widths) + self._sep_width(len(widths), style) > avail:
            widths.pop()
        if sum(widths) + self._sep_width(len(widths), style) > avail:
            widths = [max(1, min(widths[0], max(1, avail)))]

        # 有余量就补给最后一列（通常是备注/状态列）。
        slack = avail - sum(widths) - self._sep_width(len(widths), style)
        if slack > 0:
            widths[-1] += slack
        self._widths = widths
        return widths

    def _draw_row(self, grid: Grid, y: int, x: int, rect: Rect, cells: Sequence[str],
                  widths: Sequence[int], selected: bool, style: str) -> None:
        """画一行，**严格停在 rect 内**。

        这一条是硬约束而不是谨慎：表格画在窗口边框里，一旦多写一个格子就会
        把右边框覆盖成表格内容——那是最难看出成因的一类显示错乱。
        列宽收缩到下限后仍放不下时，宁可少画几列。
        """
        b = box_chars(style)
        limit = x + rect.w
        pos = x
        grid.put(y, pos, fit(self.gap + (">" if selected else " ") + " ", rect.w, mark=""))
        pos += _display_width(sanitize(self.gap, wide=True)) + 2
        for i, w in enumerate(widths):
            if pos >= limit:
                break
            text = cells[i] if i < len(cells) else ""
            grid.put(y, pos, fit(text, min(w, limit - pos)))
            pos += w
            if i + 1 < len(widths):
                sep = self.gap + b["v"] + self.gap
                if pos + _display_width(sanitize(sep, wide=True)) > limit:
                    break
                grid.put(y, pos, sep)
                pos += _display_width(sanitize(sep, wide=True))

    def render(self, grid: Grid, rect: Rect, style: str = "unicode") -> None:
        if not rect.valid:
            return
        avail = max(1, rect.w - (_display_width(sanitize(self.gap, wide=True)) + 2))
        widths = self.resolve_widths(avail, style)
        if not widths:
            return
        self._draw_row(grid, rect.y, rect.x, rect, self.headers, widths, False, style)
        if rect.h >= 2:
            draw_hsep(grid, rect.y + 1, rect.x, rect.w, style)
        body_h = max(0, rect.h - 2)
        self.ensure_visible(body_h)
        for i in range(body_h):
            idx = self.offset + i
            if idx >= len(self.rows):
                break
            self._draw_row(grid, rect.y + 2 + i, rect.x, rect, self.rows[idx], widths,
                           idx == self.selected, style)


def _cell(value: object) -> str:
    return "" if value is None else str(value)


class LogView:
    """滚动日志窗。按宽度折行；follow=True 时钉在底部。

    按终端列宽折行，不拆双宽字。
    """

    TAGS = {"info": "[i]", "ok": "[+]", "warn": "[!]", "err": "[x]"}

    def __init__(self, max_lines: int = 2000) -> None:
        self.max_lines = max(1, int(max_lines))
        self._entries: deque = deque(maxlen=self.max_lines)  # [(tag, text), ...]
        self.follow = True
        self.scroll_back = 0
        self._version = 0
        self._cache_key = None
        self._cache: List[str] = []
        self._last_height = 0
        self._last_total = 0

    # ---- 写入 ----
    def append(self, text: object, level: str = "info") -> None:
        tag = self.TAGS.get(level, self.TAGS["info"])
        for line in str(text).splitlines() or [""]:
            self._entries.append((tag, line))
        self._version += 1
        if self.follow:
            self.scroll_back = 0

    def clear(self) -> None:
        self._entries.clear()
        self._version += 1
        self.scroll_back = 0
        self.follow = True

    # ---- 滚动 ----
    def scroll(self, delta: int) -> None:
        """delta > 0 表示往上翻（看更旧的内容）。"""
        if delta > 0:
            self.follow = False
        self.scroll_back = max(0, self.scroll_back + delta)
        if self.scroll_back == 0:
            self.follow = True

    def page(self, direction: int) -> None:
        step = max(1, self._last_height - 1)
        self.scroll(direction * step)

    def goto_end(self) -> None:
        self.scroll_back = 0
        self.follow = True

    def goto_start(self) -> None:
        self.follow = False
        self.scroll_back = max(self.scroll_back, self._last_total)

    @property
    def line_count(self) -> int:
        """已折行后的行数（渲染过一次才是准的）。"""
        return len(self._cache)

    # ---- 渲染 ----
    def _wrapped(self, width: int) -> List[str]:
        key = (width, self._version)
        if key == self._cache_key:
            return self._cache
        width = max(1, width)
        out: List[str] = []
        for tag, text in self._entries:
            head = _prefix_columns(tag + " ", max(0, width - 1))
            avail = max(1, width - _display_width(head))
            text = sanitize(text, wide=True)
            if not text:
                out.append(head.rstrip())
                continue
            piece, used, first = "", 0, True
            for ch in text:
                size = _display_width(ch)
                if size > avail:
                    # 一列视口无法容纳双宽字；显式标记，不能循环或切半字。
                    ch, size = TRUNCATE_MARK, 1
                if used + size > avail:
                    out.append((head if first else " " * _display_width(head)) + piece)
                    piece, used, first = "", 0, False
                piece += ch
                used += size
            if piece:
                out.append((head if first else " " * _display_width(head)) + piece)
        self._cache = out
        self._cache_key = key
        return out

    def render(self, grid: Grid, rect: Rect, style: str = "unicode") -> None:
        if not rect.valid:
            return
        wrapped = self._wrapped(rect.w)
        total = len(wrapped)
        height = rect.h
        self._last_height = height
        self._last_total = max(0, total - height)
        max_back = max(0, total - height)
        back = 0 if self.follow else min(self.scroll_back, max_back)
        self.scroll_back = back
        start = max(0, total - height - back)
        for i in range(height):
            idx = start + i
            if idx >= total:
                break
            grid.put(rect.y + i, rect.x, wrapped[idx])

    def render_boxed(self, grid: Grid, rect: Rect, title: str = "log", style: str = "unicode") -> None:
        if not rect.valid:
            return
        draw_box(grid, rect, title, style)
        draw_hsep(grid, rect.y + 1, rect.x, rect.w, style, "follow" if self.follow else "paused")
        self.render(grid, Rect(rect.y + 2, rect.x + 1, rect.h - 3, rect.w - 2), style)


# --------------------------------------------------------------------------
# 应用外壳
# --------------------------------------------------------------------------
class TuiApp:
    """共用外壳。子类至少覆盖 build()，通常还会覆盖 hints() / on_key() / poll()。

    首屏尺寸为 0x0 时不会崩：resolve_size 兜底 24x80，Grid 容忍 0 行 0 列，
    render 对空帧返回 []。板端实测 ssh -tt 下 stty size 就是 0 0。
    """

    title = "km3 hw tools"
    menu_width = 0        # 0 表示不显示左侧菜单
    log_rows: Optional[int] = None   # None 表示按 1/3 自动
    box = "unicode"

    def __init__(self, rows=None, cols=None, box: Optional[str] = None,
                 poll_ms: int = 250, log_lines: int = 2000) -> None:
        self._forced_rows = rows
        self._forced_cols = cols
        self.rows, self.cols = resolve_size(rows, cols)
        if box:
            self.box = box
        self.poll_ms = max(1, int(poll_ms))
        self.log = LogView(max_lines=log_lines)
        self._running = False
        self._winch = False
        self._winch_installed = False
        self._previous_winch = None
        self.views: Dict[str, object] = {}
        self.current_view: Optional[str] = None
        self.focused_form: Optional[Form] = None

    def set_views(self, views: Dict[str, object], menu: Optional[Menu] = None) -> None:
        """Opt into menu navigation; view rendering remains owned by the app.

        Menu entries are view keys, in insertion order. Existing applications
        that do not call this method keep their original on_key behavior.
        """
        self.views = dict(views)
        self.menu = menu if menu is not None else Menu(list(self.views))
        self.back_to_menu()

    def show_view(self, name: str) -> None:
        if name not in self.views:
            raise KeyError(name)
        if self.focused_form and self.focused_form.editing:
            self.focused_form.current.cancel()
        self.current_view = name
        view = self.views[name]
        self.focused_form = view if isinstance(view, Form) else None

    def back_to_menu(self) -> None:
        if self.focused_form and self.focused_form.editing:
            self.focused_form.current.cancel()
        self.focused_form = None
        self.current_view = None

    def set_form(self, form: Optional[Form]) -> None:
        """Give a form keyboard focus without changing its selection."""
        if self.focused_form is not form and self.focused_form and self.focused_form.editing:
            self.focused_form.current.cancel()
        self.focused_form = form

    def dispatch_key(self, name: str, key: int) -> bool:
        """Route focus before subclass/global keys; True requests exit.

        Use this same entry point in pure interaction tests and the curses loop.
        Ctrl-C always reaches the application's on_key, even while editing.
        """
        if key != 3 and self.focused_form and self.focused_form.on_key(name, key):
            return False
        return self.on_key(name, key)

    # ---- 子类接口 ----
    def hints(self) -> List[str]:
        """状态行的按键提示。只写真正实现了的键，别写给用户看的花架子。"""
        return ["q quit"]

    def poll(self) -> None:
        """每轮循环调用一次（约 1/poll_ms 秒一次）。真工具在这里读总线。

        注意：build() 每轮也会被调用，但它应该只做排版，不做 I/O。
        """

    def build(self) -> Grid:  # pragma: no cover - 由子类实现
        raise NotImplementedError("TuiApp 子类必须实现 build() -> Grid")

    def on_key(self, name: str, key: int) -> bool:
        """返回 True 表示请求退出。子类覆盖时请先调用 super().on_key()。"""
        if key == 3:
            return True
        if self.views:
            if name == "esc" and self.current_view is not None:
                self.back_to_menu()
                return False
            if self.current_view is None:
                if name in ("up", "k", "down", "j"):
                    self.menu.move(-1 if name in ("up", "k") else 1)
                elif name == "enter" and self.menu.current in self.views:
                    self.show_view(self.menu.current)
            return name == "q"
        return name in ("q", "esc")

    def fit_hints(self, hints: Sequence[str], mark: str, width: int) -> str:
        """把按键提示塞进一行：整条整条地丢，绝不截断到半个键名。

        截断过的提示比没有更糟——"F5 refresh  ? he~" 会让人去找一个
        不存在的键。规则只有两步：

        1. 取能整条放下的最长前缀（hints() 的顺序即优先级，保命键写在前面）；
        2. 有剩的就追加 "  +N keys" 溢出标记，放不下就整个标记丢掉——
           宁可不说"还有几条"，也不能说出一个被截断的假条数。

        结果对 width 单调（更宽只会显示不少于更窄时显示的提示条数），
        所以窗口变大时提示不会反而变少。
        """
        if width <= 0:
            return ""
        hints = [sanitize(hint, wide=True) for hint in hints]
        mark = sanitize(mark, wide=True)
        suffix = ("   [" + mark + "]") if mark else ""
        if _display_width(suffix) >= width:
            return fit(suffix.strip(), width)
        room = width - _display_width(suffix)

        def joined(items: Sequence[str]) -> str:
            return "  ".join(items)

        # 1) 最长可行前缀。若连第一条都放不下 -> 退化成只报条数。
        kept: List[str] = []
        for hint in hints:
            if _display_width(joined(kept + [hint])) > room:
                break
            kept.append(hint)
        if not kept:
            return fit("+%d keys" % len(hints), width) if hints else ""

        # 2) 溢出标记，只在给得起的余量里加。
        rest = len(hints) - len(kept)
        while rest > 0:
            more = "  +%d key%s" % (rest, "" if rest == 1 else "s")
            if _display_width(joined(kept)) + _display_width(more) <= room:
                return joined(kept) + more + suffix
            kept.pop()
            rest += 1
            if not kept:
                return fit("+%d keys" % len(hints), width)
        return joined(kept) + suffix

    def layout(self) -> Layout:
        return Layout.compute(self.rows, self.cols, self.menu_width, self.log_rows)

    def shell_grid(self) -> Tuple[Grid, Layout]:
        """画好外框、分隔线与状态行，返回空白的 (网格, 版面) 交给子类填内容。"""
        lay = self.layout()
        grid = Grid(self.rows, self.cols, strict=(self.box != "ascii"))
        draw_box(grid, lay.outer, self.title, self.box)
        if lay.body.valid:
            if lay.menu.valid:
                draw_vsep(grid, lay.menu.right, lay.body.y, lay.body.h, self.box)
            if lay.status.valid and lay.body.bottom < lay.status.y:
                label = "log" if lay.log.valid else None
                draw_hsep(grid, lay.body.bottom, lay.body.x, lay.body.w, self.box, label)
            if lay.log.valid and lay.status.valid and lay.log.bottom < lay.status.y:
                draw_hsep(grid, lay.log.bottom, lay.log.x, lay.log.w, self.box)
        if lay.status.valid:
            mark = "paused" if not self.log.follow else ""
            hint = self.fit_hints(self.hints(), mark, max(0, lay.status.w - 2))
            grid.put(lay.status.y, lay.status.x, " " + hint)
        return grid, lay

    # ---- 尺寸 ----
    def _resize_now(self, stdscr=None) -> bool:
        """Apply a changed size once; return whether the ANSI grid needs repaint.

        resizeterm may queue KEY_RESIZE itself. Reapplying an unchanged size in
        response to that event would keep queuing events and starve input.
        curses can also resize before delivering KEY_RESIZE, so compare its
        window size as well as our cached layout before calling resizeterm.
        """
        rows, cols = resolve_size(self._forced_rows, self._forced_cols)
        changed = (rows, cols) != (self.rows, self.cols)
        needs_curses_resize = changed
        if stdscr is not None:
            try:
                needs_curses_resize = stdscr.getmaxyx() != (rows, cols)
            except Exception:
                pass
        if not changed and not needs_curses_resize:
            return False
        if curses is not None and needs_curses_resize:
            for fn_name in ("resizeterm", "resize_term"):
                fn = getattr(curses, fn_name, None)
                if fn is None:
                    continue
                try:
                    fn(rows, cols)
                    break
                except Exception:
                    pass
            try:
                curses.update_lines_cols()
            except Exception:
                pass
        self.rows, self.cols = rows, cols
        if stdscr is not None:
            for call in (lambda: stdscr.clearok(True), lambda: stdscr.timeout(self.poll_ms)):
                try:
                    call()
                except Exception:
                    pass
        return True

    def _on_winch(self, signum, frame) -> None:  # pragma: no cover - 信号路径
        # 信号处理器里只举手，真正的 resize+resizeterm 在主循环里做。
        self._winch = True

    def _install_winch(self) -> None:
        try:
            self._previous_winch = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, self._on_winch)
            self._winch_installed = True
        except Exception:
            # 非主线程装不了信号处理器。退化为主循环里轮询 TIOCGWINSZ。
            self._winch_installed = False

    def _restore_winch(self) -> None:
        if self._winch_installed:
            try:
                signal.signal(signal.SIGWINCH, self._previous_winch)
            except Exception:
                pass
            finally:
                self._winch_installed = False

    def _prepare_screen(self, stdscr) -> None:
        """Flush curses bookkeeping before writing the independent ANSI grid.

        getch() implicitly refreshes a touched window. Its initial/resize clear
        must happen before our frame, otherwise it erases an unchanged frame
        that the character diff correctly considers already painted.
        """
        for call in (lambda: stdscr.refresh(), lambda: stdscr.untouchwin()):
            try:
                call()
            except Exception:
                pass
        # resizeterm updates curses dimensions but may leave the terminal's
        # old DECSTBM margins active. Reset after refresh, before a full frame;
        # otherwise CRLF can scroll a larger fallback frame inside 24 rows.
        self._write(ESC + "[r")

    def _poll_size_change(self) -> bool:
        """没有 SIGWINCH 时的兜底：直接问 ioctl，变了才重算。"""
        try:
            rows, cols = _ioctl_size()
        except Exception:
            return False
        if rows > 0 and cols > 0 and (rows, cols) != (self.rows, self.cols):
            return True
        return False

    # ---- 运行 ----
    def _curses_usable(self) -> bool:
        if curses is None:
            return False
        try:
            if not sys.stdout.isatty():
                return False
        except Exception:
            return False
        return os.environ.get("TERM", "") not in ("", "dumb")

    def _write(self, text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            self._running = False

    def _emit_plain(self, grid: Grid) -> None:
        """不是 tty 时只画一帧，纯文本输出，方便管道与板端 ssh 无 -tt 的场景。"""
        sys.stderr.write(
            "note: stdout is not a tty (or TERM=dumb); rendered one frame at %dx%d "
            "(use --rows/--cols to override)\n" % (grid.rows, grid.cols)
        )
        for line in grid.to_lines():
            sys.stdout.write(line.rstrip() + "\n")
        sys.stdout.flush()

    def _main(self, stdscr):  # pragma: no cover - 需要真终端
        try:
            stdscr.keypad(True)
        except Exception:
            pass
        try:
            curses.curs_set(0)
        except Exception:
            pass
        self._install_winch()
        try:
            stdscr.timeout(self.poll_ms)
        except Exception:
            pass
        try:
            # With a zero ioctl size curses can start at 24x80 while our
            # explicit fallback is larger. Sync its dimensions before refresh;
            # _prepare_screen also resets the terminal's scrolling margins.
            self._resize_now(stdscr)
            self._prepare_screen(stdscr)
            self._running = True
            prev: Optional[List[str]] = None
            while self._running:
                if self._winch or (not self._winch_installed and self._poll_size_change()):
                    self._winch = False
                    if self._resize_now(stdscr):
                        self._prepare_screen(stdscr)
                        prev = None  # 尺寸变了，下一帧必须全量重绘
                self.poll()
                lines = self.build().to_lines()
                if lines != prev:
                    out = render(prev, lines)
                    if out:
                        self._write("".join(out))
                    prev = lines
                key = stdscr.getch()
                if key == -1:
                    continue
                if curses is not None and key == getattr(curses, "KEY_RESIZE", None):
                    if self._resize_now(stdscr):
                        self._prepare_screen(stdscr)
                        prev = None
                    continue
                if self.dispatch_key(key_name(key), key):
                    self._running = False
        finally:
            self._running = False
            try:
                curses.curs_set(1)
            except Exception:
                pass
            self._restore_winch()
        return 0

    def run(self) -> int:
        self.log.append("started; terminal size resolved to %dx%d" % (self.rows, self.cols))
        if not self._curses_usable():
            self._emit_plain(self.build())
            return 0
        return curses.wrapper(self._main)  # pragma: no cover - 需要真终端


# --------------------------------------------------------------------------
# 演示 / 冒烟用最小实例（也是"新工具怎么写"的样板）
# --------------------------------------------------------------------------
class _DemoApp(TuiApp):  # pragma: no cover - 演示
    """tui.py 自带的骨架演示：菜单 + 表格 + 滚动日志 + 状态行。

    数据全是占位符，不编造厂家事实：PMP4010 的对象字典与节点号还没拿到手册，
    所以节点号一列写 "--"、状态一列写 "unconfirmed"。
    """

    title = "km3 hwtui demo (skeleton)"
    menu_width = 20
    log_rows = 7

    MENU = [
        "CAN-1 (can0)",
        "CAN-2 (can1)",
        "usb-can-1 (can2)",
        "PMP4010 x4",
        "P65MAX-S",
        "video",
        "help",
    ]
    NODES = [
        ("--", "PMP4010", "lift A", "can-2", "unconfirmed"),
        ("--", "PMP4010", "lift A", "can-2", "unconfirmed"),
        ("--", "PMP4010", "lift A", "can-2", "unconfirmed"),
        ("--", "PMP4010", "lift A", "can-2", "unconfirmed"),
        ("--", "brush", "brush", "can-2", "unconfirmed"),
        ("--", "brush", "brush", "can-2", "unconfirmed"),
        ("--", "squeegee", "squeegee", "can-2", "unconfirmed"),
        ("--", "squeegee", "squeegee", "can-2", "unconfirmed"),
    ]

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.menu = Menu(self.MENU)
        self.table = Table(
            headers=("node", "type", "role", "bus", "state"),
            rows=self.NODES,
            gap=" ",
        )
        self.table.set_selected(0)
        self.log.append("demo data only; PMP4010 manual pending -> node ids unknown", "warn")
        self.log.append("kernel can2 is the USB-CAN module, NOT CAN-2 (off by one)", "warn")

    def hints(self) -> List[str]:
        return ["q quit", "Up/Dn move", "Enter select", "PgUp/PgDn log", "g follow", "F5 refresh", "? help"]

    def on_key(self, name: str, key: int) -> bool:
        if name in ("up", "k"):
            self.menu.move(-1)
            self.table.move(-1)
            return False
        if name in ("down", "j"):
            self.menu.move(1)
            self.table.move(1)
            return False
        if name == "pgup":
            self.log.page(1)
            return False
        if name == "pgdn":
            self.log.page(-1)
            return False
        if name == "g":
            self.log.follow = not self.log.follow
            if self.log.follow:
                self.log.goto_end()
            return False
        if name in ("f5", "r"):
            self.log.append("refresh requested at 500000bps (no bus access in the skeleton)", "info")
            return False
        if name == "enter":
            self.log.append("selected: %s" % (self.menu.current or "-"), "ok")
            return False
        if name == "?":
            for line in ("Up/Dn move selection", "Enter log the selected entry",
                         "PgUp/PgDn scroll the log", "g toggle follow", "F5 refresh", "q quit"):
                self.log.append(line, "info")
            return False
        return super().on_key(name, key)

    def build(self) -> Grid:
        grid, lay = self.shell_grid()
        if lay.menu.valid:
            self.menu.render(grid, lay.menu, self.box)
        if lay.content.valid:
            y = lay.content.y
            grid.put(y, lay.content.x, fit(
                "can1 (logic can-2) @500000bps - 8 CANopen nodes planned; ids pending manual", lay.content.w))
            table_rect = Rect(y + 1, lay.content.x, max(0, lay.content.h - 1), lay.content.w)
            self.table.render(grid, table_rect, self.box)
        if lay.log.valid:
            self.log.render(grid, lay.log, self.box)
        return grid


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="km3 硬件工具共用 TUI 外壳（骨架演示）")
    ap.add_argument("--rows", type=int, default=None, help="行数（仅当终端拿不到尺寸时生效，见 README）")
    ap.add_argument("--cols", type=int, default=None, help="列数（同上）")
    ap.add_argument("--ascii", action="store_true", help="用纯 ASCII 边框（CJK locale 下制表符号可能被判成双宽）")
    ap.add_argument("--once", action="store_true", help="只画一帧到 stdout，不进 curses")
    ap.add_argument("--title", default=None, help="覆盖标题")
    args = ap.parse_args(argv)

    app = _DemoApp(rows=args.rows, cols=args.cols, box="ascii" if args.ascii else None)
    if args.title:
        app.title = args.title
    if args.once:
        app._emit_plain(app.build())
        return 0
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
