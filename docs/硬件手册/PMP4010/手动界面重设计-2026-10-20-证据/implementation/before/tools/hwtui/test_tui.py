#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tui.py 的主机回归。运行：

    cd /home/zakiu/km3 && python3 -m unittest discover -s tools/hwtui -p test_tui.py -v

这个文件存在的唯一理由是 render() 是纯函数：不需要 pty、不需要真终端，
就能把"改了 N 个格子到底发了多少字节"钉死。

期望值一律手写成字面量，**不用生产代码里的辅助函数生成**——
用 cursor_to() 算期望值等于拿被测代码验证被测代码，那种测试改坏了也不报错。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tui  # noqa: E402


def frame(rows, cols, ch=" "):
    """造一帧纯字符网格（不经 Grid，免得把 Grid 的 bug 混进来）。"""
    return [ch * cols for _ in range(rows)]


class TestRenderPureFunction(unittest.TestCase):
    """render(prev, next) -> list[str] 的核心契约。"""

    def test_zero_size_frame_does_not_raise(self):
        """板端首屏 0x0：stty size 返回 0 0 时不得崩，且不产生任何字节。"""
        # 这是本工具箱在板端的第一现场，必须显式钉住。
        for empty in ([], [""], ["", ""], ["", "", ""]):
            with self.subTest(frame=empty):
                self.assertEqual(tui.render(None, empty), [])
                self.assertEqual(tui.render(empty, empty), [])
                self.assertEqual(tui.render(frame(3, 4), empty), [])

    def test_zero_size_grid_renders_to_empty(self):
        """0x0 的 Grid 也必须能安全地走到 render()。"""
        grid = tui.Grid(0, 0)
        self.assertEqual(grid.shape, (0, 0))
        self.assertEqual(grid.to_lines(), [])
        self.assertEqual(tui.render(None, grid.to_lines()), [])
        # 负尺寸同样不该炸（裁到 0）
        self.assertEqual(tui.Grid(-5, -5).to_lines(), [])

    def test_grid_zero_size_put_and_box_are_noops(self):
        grid = tui.Grid(0, 0)
        grid.put(0, 0, "hello")          # 不抛
        grid.box(tui.Rect(0, 0, 10, 10))  # 不抛
        grid.hline(0, 0, 5, "-")          # 不抛
        self.assertEqual(grid.to_lines(), [])

    def test_identical_frames_emit_nothing(self):
        f = frame(4, 10)
        self.assertEqual(tui.render(f, frame(4, 10)), [])
        same = tui.render(f, list(f))
        self.assertEqual(same, [])
        self.assertEqual(sum(len(s) for s in same), 0)

    def test_prev_none_is_full_repaint(self):
        out = tui.render(None, ["ab", "cd"])
        # 清屏 + 回原点 + 首行；其后每行以 CRLF 前缀。
        self.assertEqual(out, ["\x1b[2J\x1b[Hab", "\r\ncd"])
        self.assertEqual("".join(out), "\x1b[2J\x1b[Hab\r\ncd")
        # 7 = len("\x1b[2J\x1b[H")，2 = len("\r\n")
        self.assertEqual(sum(len(s) for s in out), 7 + 2 + 2 + 2)

    def test_shape_change_forces_full_repaint(self):
        """行数变了、列数变了、行宽不齐了——都必须退化成全量重绘。"""
        cases = [
            (["abc", "def"], ["abc"]),
            (["abc"], ["abcd"]),
            (["abc", "def"], ["abcd", "defg"]),
            (["abc", "def"], ["abc", "de"]),
        ]
        for prev, nxt in cases:
            with self.subTest(prev=prev, next=nxt):
                out = tui.render(prev, nxt)
                self.assertEqual(out[0], "\x1b[2J\x1b[H" + nxt[0])
                self.assertEqual("".join(out), "\x1b[2J\x1b[H" + "\r\n".join(nxt))

    def test_three_contiguous_changed_cells_exact_bytes(self):
        """改了 3 个格子（同一行、连续）-> 精确等于 10 字节。

        "\x1b[6;11H" 是 7 字节（ESC + "[6;11H"，行 6 列 11 是 1 基），
        加 3 个字符 = 10。这个常数是手算的，不是程序算的。
        """
        prev = frame(6, 20)
        nxt = frame(6, 20)
        row = list(nxt[5])
        row[10], row[11], row[12] = "X", "Y", "Z"
        nxt[5] = "".join(row)

        out = tui.render(prev, nxt)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], "\x1b[6;11HXYZ")
        self.assertEqual(len(out[0]), 10)
        self.assertEqual(sum(len(s) for s in out), 10)

    def test_three_scattered_changed_cells_exact_bytes(self):
        """改了 3 个格子（同一行、互不相邻）-> 精确等于 22 字节。

        三条定位不是等长的：ESC + "[1;1H" 与 "[1;6H" 是 6 字节，
        而 "[1;11H" 是 7 字节（第 11 列多一位）。加 3 个字符 = 7 + 7 + 8 = 22。
        把 22 写死在这里的意义正是这个位数差：谁把定位改成相对移动，
        这里的数字会立刻不对。
        """
        prev = frame(6, 20)
        nxt = frame(6, 20)
        row = list(nxt[0])
        row[0], row[5], row[10] = "A", "B", "C"
        nxt[0] = "".join(row)

        out = tui.render(prev, nxt)
        self.assertEqual(out, ["\x1b[1;1HA", "\x1b[1;6HB", "\x1b[1;11HC"])
        self.assertEqual([len(s) for s in out], [7, 7, 8])
        self.assertEqual(sum(len(s) for s in out), 22)

    def test_two_runs_merge_only_when_adjacent(self):
        """相邻变化必须合并成一条，隔一格的必须拆成两条——省字节靠这条。"""
        prev = frame(1, 10)
        # 注意：这些字面量必须正好 10 列。差一列就是"形状变了"，
        # 走的会是全量重绘那条路，测的就不再是 diff 了（本文件第一版就栽在这）。
        adjacent = [" XX       "]   # 列 1、2 -> 一条
        spurious = [" X        "]   # 单格 -> 一条
        apart = [" X X      "]      # 列 1、3 中间隔着列 2 -> 两条

        out = tui.render(prev, adjacent)
        self.assertEqual(out, ["\x1b[1;2HXX"])
        self.assertEqual(sum(len(s) for s in out), 6 + 2)

        out = tui.render(prev, spurious)
        self.assertEqual(out, ["\x1b[1;2HX"])
        self.assertEqual(sum(len(s) for s in out), 6 + 1)

        out = tui.render(prev, apart)
        self.assertEqual(len(out), 2)
        self.assertEqual(sum(len(s) for s in out), 6 + 1 + 6 + 1)

    def test_multiple_rows_use_absolute_positioning(self):
        """每行/每个区间自带绝对定位：输出只取决于两帧之差，不取决于光标在哪。"""
        prev = frame(3, 8)
        nxt = list(prev)
        nxt[0] = "a" + nxt[0][1:]
        nxt[2] = nxt[2][:7] + "z"
        out = tui.render(prev, nxt)
        self.assertEqual(out, ["\x1b[1;1Ha", "\x1b[3;8Hz"])
        # 顺序输出与乱序渲染同一个"帧差"，字节完全一致
        self.assertEqual(tui.render(prev, nxt), tui.render(prev, nxt))

    def test_render_is_deterministic_and_pure(self):
        """同一对帧渲染两次必须逐字节相同；且不得改动入参。"""
        prev = ["abc", "def"]
        nxt = ["aXc", "dYf"]
        prev_copy, next_copy = list(prev), list(nxt)
        first = tui.render(prev, nxt)
        second = tui.render(prev, nxt)
        self.assertEqual(first, second)
        self.assertEqual(prev, prev_copy)
        self.assertEqual(nxt, next_copy)

    def test_round_trip_single_frame_is_identity(self):
        """全量重绘 + 按行还原 == 目标帧（这是渲染正确性的最小证据）。"""
        nxt = ["+---+", "|abc|", "+---+"]
        out = tui.render(None, nxt)
        joined = "".join(out)
        self.assertTrue(joined.startswith("\x1b[2J\x1b[H"))
        body = joined[len("\x1b[2J\x1b[H"):]
        self.assertEqual(body.split("\r\n"), nxt)

    def test_incremental_equals_full_repaint_result(self):
        """增量渲染积累后，终端上呈现的内容必须与全量重绘一致。

        用一台极简"终端模拟器"（只认光标定位与打印）验证，而不是断言字节数。
        """
        prev = frame(4, 12, ch=".")
        nxt = list(prev)
        nxt[1] = "..done......"   # 必须正好 12 列（同 prev 的形状），见本类第一条注释
        nxt[3] = nxt[3][:5] + "ok" + nxt[3][7:]

        screen = _Screen(4, 12)
        screen.feed(tui.render(None, prev))
        screen.feed(tui.render(prev, nxt))
        self.assertEqual(screen.lines(), nxt)


class _Screen:
    """极简终端模拟器：只实现 CSI row;col H 与可打印字符。够验证 render()。"""

    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.buf = [[" "] * cols for _ in range(rows)]
        self.cy = self.cx = 0

    def feed(self, chunks):
        for chunk in chunks:
            i = 0
            while i < len(chunk):
                if chunk[i] == "\x1b" and i + 1 < len(chunk) and chunk[i + 1] == "[":
                    # 按 CSI 语法取参数串与终止字节，而不是去找某个固定的字母：
                    # 全量重绘里先出现 "\x1b[2J"（清屏，止于 J），再是 "\x1b[H"（回原点）。
                    # 早先的版本写死找 'H'，于是把 "2J\x1b[" 当成了参数——模拟器的
                    # 漏洞冒充成生产代码的 bug，是最浪费时间的一类误报。
                    j = i + 2
                    while j < len(chunk) and not ("@" <= chunk[j] <= "~"):
                        j += 1
                    if j >= len(chunk):
                        break
                    final, params = chunk[j], chunk[i + 2:j]
                    if final == "J":
                        self.buf = [[" "] * self.cols for _ in range(self.rows)]
                        if params in ("2", "3"):
                            self.cy, self.cx = 0, 0
                    elif final == "H":
                        if params == "":
                            self.cy, self.cx = 0, 0
                        else:
                            parts = params.split(";")
                            row = int(parts[0]) if parts[0] else 1
                            col = int(parts[1]) if len(parts) > 1 and parts[1] else 1
                            self.cy, self.cx = row - 1, col - 1
                    i = j + 1
                elif chunk[i] == "\r":
                    self.cx = 0
                    i += 1
                elif chunk[i] == "\n":
                    self.cy += 1
                    i += 1
                else:
                    if 0 <= self.cy < self.rows and 0 <= self.cx < self.cols:
                        self.buf[self.cy][self.cx] = chunk[i]
                    self.cx += 1
                    i += 1

    def lines(self):
        return ["".join(r) for r in self.buf]


class TestSizeResolution(unittest.TestCase):
    """ioctl -> LINES/COLUMNS -> --rows/--cols -> 24x80。"""

    def test_fallback_when_nothing_available(self):
        rows, cols = tui.resolve_size(ioctl=lambda: (0, 0), env={})
        self.assertEqual((rows, cols), (24, 80))

    def test_ioctl_wins(self):
        rows, cols = tui.resolve_size(40, 120, ioctl=lambda: (50, 200),
                                      env={"LINES": "30", "COLUMNS": "90"})
        self.assertEqual((rows, cols), (50, 200))

    def test_board_stty_size_zero_zero_falls_through_to_env(self):
        """板端实测：ssh -tt 下 stty size 返回 0 0。此时必须往下走，不能当成合法尺寸。"""
        rows, cols = tui.resolve_size(ioctl=lambda: (0, 0), env={"LINES": "30", "COLUMNS": "90"})
        self.assertEqual((rows, cols), (30, 90))

    def test_zero_zero_falls_through_to_cli(self):
        rows, cols = tui.resolve_size(30, 90, ioctl=lambda: (0, 0), env={})
        self.assertEqual((rows, cols), (30, 90))

    def test_partial_dimensions_resolve_independently(self):
        """终端报了行、列是 0：行取 ioctl，列继续往下找。"""
        rows, cols = tui.resolve_size(10, 66, ioctl=lambda: (42, 0), env={})
        self.assertEqual(rows, 42)
        self.assertEqual(cols, 66)

    def test_bad_env_values_are_ignored(self):
        for bad in ("", "0", "-3", "abc", "  ", "24x80"):
            with self.subTest(LINES=bad):
                rows, cols = tui.resolve_size(ioctl=lambda: (0, 0),
                                              env={"LINES": bad, "COLUMNS": bad})
                self.assertEqual((rows, cols), (24, 80))

    def test_env_whitespace_is_tolerated(self):
        rows, cols = tui.resolve_size(ioctl=lambda: (0, 0), env={"LINES": " 33 ", "COLUMNS": " 132 "})
        self.assertEqual((rows, cols), (33, 132))

    def test_ioctl_exception_falls_back(self):
        def boom():
            raise OSError("no tty")
        rows, cols = tui.resolve_size(ioctl=boom, env={"LINES": "25", "COLUMNS": "81"})
        self.assertEqual((rows, cols), (25, 81))

    def test_app_constructs_at_zero_size_without_polling_tty(self):
        """构造时不做 ioctl 也要能起来（测试与 CI 无 tty）。"""
        app = tui.TuiApp(rows=0, cols=0, box="ascii")
        self.assertGreaterEqual(app.rows, 1)
        self.assertGreaterEqual(app.cols, 1)

    def test_resize_recomputes_layout(self):
        app = tui.TuiApp(rows=24, cols=80, box="ascii")
        before = app.layout()
        app._forced_rows, app._forced_cols = 10, 40
        app._resize_now(None)   # curses 不可用时应静默跳过 resizeterm
        self.assertEqual((app.rows, app.cols), (10, 40))
        after = app.layout()
        self.assertNotEqual(before.outer, after.outer)
        self.assertEqual(after.status.w, 38)


class TestLayoutAndWidgets(unittest.TestCase):
    def test_layout_degenerate_sizes(self):
        for rows, cols in ((0, 0), (0, 80), (5, 0), (3, 3), (1, 1)):
            with self.subTest(rows=rows, cols=cols):
                lay = tui.Layout.compute(rows, cols, menu_w=20, log_rows=5)
                self.assertEqual(lay.outer.h, max(0, rows))
                self.assertEqual(lay.outer.w, max(0, cols))
                for name in ("body", "menu", "content", "log", "status"):
                    self.assertFalse(getattr(lay, name).valid)

    def test_layout_normal(self):
        lay = tui.Layout.compute(24, 80, menu_w=20, log_rows=6)
        self.assertTrue(lay.menu.valid)
        self.assertTrue(lay.content.valid)
        self.assertTrue(lay.log.valid)
        self.assertTrue(lay.status.valid)
        self.assertEqual(lay.status.y, 22)               # 边框内最后一行
        self.assertEqual(lay.body.y + lay.body.h, lay.log.y - 1)  # 中间那条分隔线
        self.assertLess(lay.menu.right, lay.content.x)

    def test_app_first_frame_at_24x80_is_stable(self):
        """首屏必须画满、不抛、行宽一致——render() 的 diff 依赖等宽。"""
        app = tui._DemoApp(rows=24, cols=80, box="ascii")
        lines = app.build().to_lines()
        self.assertEqual(len(lines), 24)
        self.assertTrue(all(len(x) == 80 for x in lines), "rows must be equal width")
        text = "\n".join(lines)
        self.assertIn("+", text)
        self.assertIn("km3 hwtui demo", text)

    def test_app_renders_at_degenerate_sizes(self):
        for rows, cols in ((1, 1), (2, 10), (4, 20), (0, 0)):
            with self.subTest(rows=rows, cols=cols):
                app = tui._DemoApp(rows=rows, cols=cols, box="ascii")
                lines = app.build().to_lines()   # 不得抛
                self.assertTrue(all(len(x) == app.cols for x in lines))
                tui.render(None, lines)          # 不得抛

    def test_row_width_is_always_consistent(self):
        """宽度不一致会让 render() 每帧退化成全量重绘——这里钉住它不发生。"""
        for cols in (1, 2, 5, 13, 40, 79, 80, 81, 200):
            with self.subTest(cols=cols):
                app = tui._DemoApp(rows=24, cols=cols, box="ascii")
                lines = app.build().to_lines()
                self.assertEqual({len(x) for x in lines}, {cols} if cols > 0 else {0})

    def test_sanitize_strips_control_characters(self):
        """网格里会出现报文/文件名/环境变量里的字节：ESC 放行等于让数据改写终端。"""
        self.assertEqual(tui.sanitize("a\x1b[2Jb"), "a.[2Jb")   # ESC -> '.'，'[' 是 ASCII 保留
        self.assertEqual(tui.sanitize("ok\x07\r\n"), "ok...")
        self.assertEqual(tui.sanitize("tab\there"), "tab.here")
        self.assertNotIn("\x1b", tui.sanitize("bad\x1b[31mred"))

    def test_sanitize_replaces_wide_characters_by_default(self):
        """不做 wcwidth：非 ASCII 非制表符号一律降级，否则必然静默错位。"""
        # 注意 "电机A" -> "??A"：A 是 ASCII，保得住；宽字符才降级。
        # 期望值必须按"逐字符"写，因为生产规则就是逐字符的。
        self.assertEqual(tui.sanitize("电机A"), "??A")
        self.assertEqual(tui.sanitize("CAN-2 正常"), "CAN-2 ??")
        self.assertEqual(tui.sanitize("电机A", strict=False), "电机A")
        self.assertEqual(tui.sanitize("─│┌"), "─│┌")

    def test_fit_never_exceeds_width(self):
        for width in range(0, 8):
            with self.subTest(width=width):
                self.assertLessEqual(len(tui.fit("abcdef", width)), width)
        self.assertEqual(tui.fit("abcdef", 10), "abcdef")
        self.assertEqual(tui.fit("abcdef", 4), "abc" + tui.TRUNCATE_MARK)
        self.assertEqual(tui.fit("abcdef", 1), "~")
        self.assertEqual(tui.fit("abcdef", 0), "")

    def test_put_clips_negative_and_overflow(self):
        g = tui.Grid(2, 6)
        g.put(0, -2, "abcdef")        # 左边裁 2 -> "cdef" 落在列 0
        self.assertEqual(g.to_lines()[0], "cdef  ")
        g.put(1, 4, "XYZ")            # 右边只放得下 2 个
        self.assertEqual(g.to_lines()[1], "    XY")
        g.put(5, 0, "out of range")   # 行越界
        g.put(0, 99, "x")             # 列越界
        self.assertEqual(len(g.to_lines()), 2)

    def test_put_sanitizes_by_default(self):
        g = tui.Grid(1, 8)
        g.put(0, 0, "a\x1bb")   # 3 个字符 a . b，其余行尾补空格
        self.assertEqual(g.to_lines()[0], "a.b     ")

    def test_menu_selection_is_clamped_and_scrolls(self):
        m = tui.Menu(["a", "b", "c", "d", "e", "f"], selected=0)
        m.move(-1)
        self.assertEqual(m.selected, 0)          # 到头即停
        for _ in range(20):
            m.move(1)
        self.assertEqual(m.selected, 5)
        m.ensure_visible(3)
        self.assertGreaterEqual(m.offset, 3)
        g = tui.Grid(3, 10)
        m.render(g, tui.Rect(0, 0, 3, 10), "ascii")
        self.assertIn("> f", g.to_lines()[2])

    def test_empty_menu_is_safe(self):
        m = tui.Menu([])
        m.move(1)
        m.ensure_visible(4)
        self.assertIsNone(m.current)
        m.render(tui.Grid(2, 10), tui.Rect(0, 0, 2, 10))  # 不抛

    def test_status_hints_never_truncate_a_key_name(self):
        """提示行必须整条整条地丢，不能出现 "? he~" 这种半截键名。

        宽 80 时演示应用有 7 条提示，正好会溢出——所以这条测的是真实场景，
        不是构造出来的极端值。
        """
        app = tui._DemoApp(rows=24, cols=80, box="ascii")
        for cols in (20, 30, 41, 60, 80, 100, 200):
            with self.subTest(cols=cols):
                app.cols = cols
                lines = app.build().to_lines()
                status = lines[22][:cols]
                if "~" in status or "+" in status:
                    continue          # 溢出标记，不算截断
                for hint in app.hints():
                    self.assertNotIn(hint + "~", status)

    def test_status_hints_report_overflow_instead_of_silence(self):
        app = tui._DemoApp(rows=24, cols=80, box="ascii")
        status = app.build().to_lines()[22]
        self.assertIn("+", status)
        self.assertIn("key", status)

    def test_status_hints_all_fit_when_wide(self):
        app = tui._DemoApp(rows=24, cols=200, box="ascii")
        status = app.build().to_lines()[22]
        for hint in app.hints():
            self.assertIn(hint, status)

    def test_fit_hints_edge_cases(self):
        app = tui.TuiApp(rows=10, cols=40, box="ascii")
        self.assertEqual(app.fit_hints([], "", 10), "")
        self.assertEqual(app.fit_hints(["q quit"], "", 0), "")
        # 一条都放不下时给出条数，而不是留空白；窄到连条数都放不下就按宽度截断。
        self.assertEqual(app.fit_hints(["q quit"], "", 10), "q quit")   # 放得下就正常显示
        self.assertEqual(app.fit_hints(["q quit"], "", 3), "+1~")       # 放不下 -> 报条数
        self.assertTrue(app.fit_hints(["very long hint"], "", 4))
        self.assertEqual(app.fit_hints(["a b", "c d", "e f"], "", 7), "+3 keys")
        self.assertLessEqual(len(app.fit_hints(["a b", "c d", "e f"], "", 7)), 7)

    def test_fit_hints_is_monotonic_in_width(self):
        """窗口变宽时，显示的提示条数不得反而变少。

        这是回归测试——早先的实现会在 width 12 时显示 1 条，而在 width 10 时
        显示 0 条（因为 10 宽时溢出标记挤掉了唯一那条，代码却径直返回了空串）。
        提示变少、变多的顺序都不该由窗口宽度反过来决定。
        """
        app = tui.TuiApp(rows=10, cols=40, box="ascii")
        hints = ["a b", "c d", "e f", "g h"]
        prev = -1
        for width in range(0, 60):
            shown = app.fit_hints(hints, "", width)
            count = sum(1 for h in hints if h in shown)
            self.assertGreaterEqual(count, prev, "width=%d 反而少显示了提示" % width)
            self.assertLessEqual(len(shown), width)
            prev = count

    def test_table_columns_fit_available_width(self):
        t = tui.Table(headers=("node", "type", "role", "bus", "state"),
                      rows=[("1", "PMP4010", "lift A", "can-2", "ok")])
        for avail in (100, 60, 40, 25, 12, 5, 3, 1):
            with self.subTest(avail=avail):
                widths = t.resolve_widths(avail)
                total = sum(widths) + t._sep_width(len(widths), "unicode")
                self.assertLessEqual(total, max(avail, len(widths) * 2 + 3))
        g = tui.Grid(6, 40)
        t.render(g, tui.Rect(0, 0, 6, 40), "ascii")
        lines = g.to_lines()
        self.assertTrue(all(len(x) == 40 for x in lines))

    def test_table_renders_selection_marker(self):
        t = tui.Table(headers=("a",), rows=[("x",), ("y",)])
        t.set_selected(1)
        g = tui.Grid(4, 20)
        t.render(g, tui.Rect(0, 0, 4, 20), "ascii")
        self.assertIn("> y", g.to_lines()[3])
        self.assertNotIn("> x", g.to_lines()[2])

    def test_empty_table_is_safe(self):
        t = tui.Table(headers=("a", "b"), rows=[])
        g = tui.Grid(4, 30)
        t.render(g, tui.Rect(0, 0, 4, 30), "ascii")
        self.assertIsNone(t.current)
        self.assertTrue(all(len(x) == 30 for x in g.to_lines()))

    def test_table_truncates_overlong_cell(self):
        t = tui.Table(headers=("k",), rows=[("v" * 200,)])
        g = tui.Grid(4, 20)
        t.render(g, tui.Rect(0, 0, 4, 20), "ascii")
        self.assertTrue(all(len(x) == 20 for x in g.to_lines()))

    def test_logview_wraps_and_follows_tail(self):
        log = tui.LogView(max_lines=100)
        for i in range(50):
            log.append("entry %02d" % i)
        g = tui.Grid(4, 20)
        log.render(g, tui.Rect(0, 0, 4, 20))
        self.assertIn("entry 49", "\n".join(g.to_lines()))   # follow 时钉在底部
        log.scroll(1)
        self.assertFalse(log.follow)
        g2 = tui.Grid(4, 20)
        log.render(g2, tui.Rect(0, 0, 4, 20))
        self.assertNotIn("entry 49", "\n".join(g2.to_lines()))
        log.goto_end()
        self.assertTrue(log.follow)

    def test_logview_wraps_long_lines_by_cells(self):
        log = tui.LogView()
        log.append("x" * 100)
        g = tui.Grid(10, 20)
        log.render(g, tui.Rect(0, 0, 10, 20))
        self.assertTrue(all(len(x) == 20 for x in g.to_lines()))
        self.assertGreater(log.line_count, 1)   # 折行了，不是截断

    def test_logview_ring_buffer_is_bounded(self):
        """板端根分区小 → 日志窗必须有上限，不能是无限 list。"""
        log = tui.LogView(max_lines=10)
        for i in range(100):
            log.append("line %d" % i)
        g = tui.Grid(5, 30)
        log.render(g, tui.Rect(0, 0, 5, 30))
        self.assertLessEqual(log.line_count, 10)

    def test_logview_zero_size_is_safe(self):
        log = tui.LogView()
        log.append("hello")
        log.render(tui.Grid(0, 0), tui.Rect(0, 0, 0, 0))
        log.render(tui.Grid(5, 30), tui.Rect(0, 0, 0, 0))
        log.append("\x1b[2Jevil")
        g = tui.Grid(5, 30)
        log.render(g, tui.Rect(0, 0, 5, 30))
        self.assertNotIn("\x1b", "\n".join(g.to_lines()))


class TestKeyDecoding(unittest.TestCase):
    def test_basic_keys(self):
        self.assertEqual(tui.key_name(ord("q")), "q")
        self.assertEqual(tui.key_name(10), "enter")
        self.assertEqual(tui.key_name(13), "enter")
        self.assertEqual(tui.key_name(27), "esc")
        self.assertEqual(tui.key_name(9), "tab")
        self.assertEqual(tui.key_name(127), "backspace")

    def test_control_keys_are_not_letters(self):
        for key in (1, 2, 3, 4, 5, 6, 7, 11, 12):
            self.assertEqual(tui.key_name(key), "")

    def test_special_keys_when_curses_available(self):
        if tui.curses is None:
            self.skipTest("curses 不可用")
        import curses
        for name, attr in (("up", "KEY_UP"), ("down", "KEY_DOWN"), ("pgup", "KEY_PPAGE"),
                           ("resize", "KEY_RESIZE")):
            self.assertEqual(tui.key_name(getattr(curses, attr)), name)

    def test_unknown_key_returns_empty(self):
        self.assertEqual(tui.key_name(None), "")
        self.assertEqual(tui.key_name(999999), "")

    def test_app_quit_keys(self):
        app = tui.TuiApp(rows=10, cols=40, box="ascii")
        self.assertTrue(app.on_key("q", ord("q")))
        self.assertTrue(app.on_key("esc", 27))
        self.assertFalse(app.on_key("z", ord("z")))


class TestDiscoverExistence(unittest.TestCase):
    """discover.py 是独立进程；这里只保证兄弟模块能被 import（路径接线没写错）。"""

    def test_discover_module_importable(self):
        import discover
        self.assertTrue(hasattr(discover, "discover"))
        self.assertTrue(hasattr(discover, "main"))
        self.assertTrue(hasattr(discover, "repo_root"))

    def test_discover_returns_list_without_drivers_dir(self):
        import discover
        found = discover.discover(roots=[os.path.join(os.sep, "nonexistent-km3-drivers")])
        self.assertEqual(list(found), [])

    def test_discover_parses_a_synthetic_driver_tool(self):
        """造一棵临时 driver 树，验证 glob 形状与 docstring 摘录。

        形状是 device/drivers/<name>/tools/<tool>.py —— 新硬件加目录即可，
        不需要新的包装脚本。
        """
        import tempfile

        import discover
        with tempfile.TemporaryDirectory() as tmp:
            tool_dir = os.path.join(tmp, "pmp4010", "tools")
            os.makedirs(tool_dir)
            with open(os.path.join(tool_dir, "scan.py"), "w", encoding="utf-8") as fh:
                fh.write('"""PMP4010 总线扫描。"""\n\ndef main():\n    return 0\n')
            with open(os.path.join(tool_dir, "_helper.py"), "w", encoding="utf-8") as fh:
                fh.write("# 下划线开头：内部模块，不列出\n")
            with open(os.path.join(tmp, "pmp4010", "README.md"), "w", encoding="utf-8") as fh:
                fh.write("not a tool\n")
            found = discover.discover(roots=[tmp])
            names = [t.name for t in found]
            self.assertEqual(names, ["scan.py"])
            entry = found[0]
            self.assertEqual(entry.driver, "pmp4010")
            self.assertIn("PMP4010", entry.summary)
            self.assertTrue(entry.path.endswith(os.path.join("pmp4010", "tools", "scan.py")))

    def test_underscore_dirs_are_not_hardware(self):
        """device/drivers/_template 是脚手架，不是硬件；默认不得列进"硬件工具"。"""
        import tempfile

        import discover
        with tempfile.TemporaryDirectory() as tmp:
            for model, tool in (("pmp4010", "pmp4010.py"), ("_template", "_model_.py")):
                d = os.path.join(tmp, model, "tools")
                os.makedirs(d)
                with open(os.path.join(d, tool), "w", encoding="utf-8") as fh:
                    fh.write('"""x."""\n')
            self.assertEqual([t.driver for t in discover.discover([tmp])], ["pmp4010"])
            both = discover.discover([tmp], include_private=True)
            self.assertEqual(sorted(t.driver for t in both), ["_template", "pmp4010"])

    def test_summary_truncation_marks_itself(self):
        """摘要过长要带省略号，让人看出"这是被截断的"，而不是像没写摘要。"""
        import discover
        self.assertEqual(discover._clip("short", 58), "short")
        clipped = discover._clip("x" * 100, 10)
        self.assertEqual(len(clipped), 10)
        self.assertTrue(clipped.endswith("\u2026"))
        self.assertEqual(discover._clip("anything", 0), "")

    def test_table_lists_multibyte_summary_without_mangling(self):
        """中文摘要（PMP4010 那条就是）必须完整出现在表里——它不该被砍成空。"""
        import discover
        tool = discover.Tool(
            driver="pmp4010", name="pmp4010.py", path="/tmp/pmp4010.py",
            summary="PMP4010（机身抬降电机 A）板载调试工具 —— curses TUI 与一次性子命令。",
            rel="device/drivers/pmp4010/tools/pmp4010.py", has_main=False)
        text = discover.format_table([tool], "device/drivers")
        self.assertIn("PMP4010（机身抬降电机 A）", text)
        self.assertIn("pmp4010", text)

    def test_discover_detects_entry_point_shapes(self):
        """has_main 判据必须认全三种入口写法。

        尤其是模块级 argparse —— 那是最常见的一类，早先的实现只查
        `__main__` 与 `def main(`，会把真实工具误标成"没入口"。
        反过来，注释与 docstring 里的字样不能被当成入口。
        """
        import tempfile

        import discover
        cases = {
            "guard": ('def f():\n    return 1\n\nif __name__ == "__main__":\n    f()\n', True),
            "module_argparse": ('import argparse\nap = argparse.ArgumentParser()\nap.parse_args()\n', True),
            "top_level_run": ('def run():\n    pass\nrun()\n', True),
            "def_main_only": ('def main():\n    return 0\n', False),
            "none": ('X = 1\n', False),
            "comment_only": ('# def main( in a comment\nX = 1\n', False),
            "docstring_only": ('"""usage: --run <tool>"""\nX = 1\n', False),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, (body, want) in cases.items():
                with self.subTest(case=name):
                    path = os.path.join(tmp, name + ".py")
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(body)
                    self.assertEqual(discover._has_main(path), want)

    def test_discover_survives_unreadable_or_broken_tool(self):
        """坏文件（非法 UTF-8 / 语法错）不得让 --list 整个挂掉。"""
        import tempfile

        import discover
        with tempfile.TemporaryDirectory() as tmp:
            tool_dir = os.path.join(tmp, "weird", "tools")
            os.makedirs(tool_dir)
            with open(os.path.join(tool_dir, "broken.py"), "wb") as fh:
                fh.write(b"\xff\xfe not utf-8 at all\n")
            with open(os.path.join(tool_dir, "syntaxerr.py"), "w", encoding="utf-8") as fh:
                fh.write("def (((:\n")
            with open(os.path.join(tool_dir, "empty.py"), "w", encoding="utf-8") as fh:
                fh.write("")
            found = discover.discover(roots=[tmp])
            self.assertEqual(sorted(t.name for t in found),
                             ["broken.py", "empty.py", "syntaxerr.py"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
