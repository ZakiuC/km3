"""Column-aware Unicode grid regressions, independent of the legacy assertions."""
import os
import random
import re
import sys
import unicodedata
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tui


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in text)


def edges(text):
    result, col = {0}, 0
    for char in text:
        col += width(char)
        result.add(col)
    return result


def terminal_cells(text):
    """A wide glyph owns a leading character and an empty continuation cell."""
    result = []
    for char in text:
        result.append(char)
        if width(char) == 2:
            result.append('')
    return result


def apply_diff(before, chunks):
    cells = terminal_cells(before)
    for chunk in chunks:
        match = re.fullmatch(r'\x1b\[1;(\d+)H([^\x1b]*)', chunk)
        if not match:
            raise AssertionError('expected a single absolute row-one write: %r' % chunk)
        col, text = int(match[1]) - 1, match[2]
        for char in text:
            size = width(char)
            # Emulate erasing any existing wide glyph touched by a write.
            for index in range(col, col + size):
                if cells[index] == '':
                    cells[index - 1] = ' '
                elif width(cells[index]) == 2 and index + 1 < len(cells):
                    cells[index + 1] = ' '
            cells[col] = char
            if size == 2:
                cells[col + 1] = ''
            col += size
    return cells


class TestUnicodeColumns(unittest.TestCase):
    def test_width_and_shape_are_terminal_columns(self):
        self.assertEqual(tui._display_width('中文AＡ─'), 8)
        self.assertEqual(tui._shape(['中文', 'a中b']), (4, 4))
        self.assertNotEqual(len('中文'), len('a中b'))

    def test_sanitize_explicit_wide_and_legacy_compatibility(self):
        self.assertEqual(tui.sanitize('电机A'), '??A')
        self.assertEqual(tui.sanitize('电机A', wide=True), '电机A')
        self.assertEqual(tui.sanitize('ＡＢ中', wide=True), 'ＡＢ中')

    def test_control_and_zero_width_injection_is_blocked_in_every_mode(self):
        unsafe = '\x00\x07\x1b\x7f\x80\x9b\u202e\u200d\u2028\ud800'
        for strict in (True, False):
            for wide in (True, False):
                self.assertEqual(tui.sanitize(unsafe, strict, wide), '.' * len(unsafe))
                self.assertEqual(tui.sanitize('e\u0301\ufe0f', strict, wide), 'e??')
        grid = tui.Grid(1, 8, strict=False)
        grid.put(0, 0, '中\x9b\u200dA')
        self.assertEqual(grid.to_lines(), ['中..A   '])

    def test_fit_chinese_and_mixed_never_splits_glyph(self):
        for text, size, expected in (
            ('中文测试', 0, ''), ('中文测试', 1, '~'),
            ('中文测试', 2, '~'), ('中文测试', 3, '中~'),
            ('中文测试', 4, '中~'), ('中文测试', 8, '中文测试'),
            ('A中B文C', 4, 'A中~'), ('中A文', 4, '中A~'),
        ):
            with self.subTest(text=text, width=size):
                self.assertEqual(tui.fit(text, size), expected)
                self.assertLessEqual(width(tui.fit(text, size)), size)
        self.assertEqual(tui.fit('中文A', 3, mark=''), '中')
        self.assertEqual(tui.fit('中文A', 3, mark='界'), '界')
        self.assertEqual(tui.fit('中文A', 1, mark='界'), '')

    def test_grid_clips_negative_x_and_both_wide_edges(self):
        cases = [(-1, '中A', ' A    '), (-2, '中A', 'A     '),
                 (-3, '中A文B', '文B   '), (5, '中', '      '),
                 (4, '中文', '    中'), (-8, '中', '      ')]
        for x, text, expected in cases:
            with self.subTest(x=x, text=text):
                grid = tui.Grid(1, 6)
                grid.put(0, x, text)
                self.assertEqual(grid.to_lines(), [expected])
                self.assertEqual(width(grid.to_lines()[0]), 6)

    def test_wide_overwrite_clears_other_half_on_either_side(self):
        for x, expected in ((1, ' X文  '), (0, 'X 文  '), (2, '中X   '), (3, '中 X  ')):
            with self.subTest(x=x):
                grid = tui.Grid(1, 6)
                grid.put(0, 0, '中文')
                grid.put(0, x, 'X')
                self.assertEqual(grid.to_lines(), [expected])
        grid.put(0, 1, '界')
        self.assertEqual(grid.to_lines(), [' 界X  '])

    def test_fill_and_hline_respect_requested_columns(self):
        grid = tui.Grid(2, 8, fill='中')
        self.assertEqual(grid.to_lines(), [' ' * 8] * 2)
        grid.hline(0, 1, 3, '中')
        grid.fill_rect(tui.Rect(1, 1, 1, 3), '文')
        self.assertEqual(grid.to_lines(), [' 中     ', ' 文     '])

    def test_diff_after_unchanged_wide_prefix_uses_column_position(self):
        self.assertEqual(tui.render(['中文'], ['中a ']), ['\x1b[1;3Ha '])
        self.assertEqual(tui.render(['中a '], ['中文']), ['\x1b[1;3H文'])

    def test_diff_start_expands_to_both_old_and_new_glyph_boundaries(self):
        self.assertEqual(tui.render(['中 '], [' 中']), ['\x1b[1;1H 中'])
        self.assertEqual(tui.render([' 中'], ['中 ']), ['\x1b[1;1H中 '])

    def test_shifted_boundary_chain_expands_until_common_boundary(self):
        before, after = '中中中中x', 'x中中中中'
        self.assertEqual(tui._changed_runs(before, after), [(0, 9)])
        self.assertEqual(tui.render([before], [after]), ['\x1b[1;1Hx中中中中'])
        self.assertEqual(apply_diff(before, tui.render([before], [after])), terminal_cells(after))

    def test_equal_terminal_width_different_character_count_is_incremental(self):
        self.assertEqual(tui.render(['中文'], ['ABCD']), ['\x1b[1;1HABCD'])
        self.assertEqual(tui.render(['ABCD'], ['中文']), ['\x1b[1;1H中文'])
        self.assertEqual(tui.render(['中文'], ['中']), ['\x1b[2J\x1b[H中'])

    def test_unicode_diff_roundtrips_and_boundary_alignment(self):
        rng = random.Random(4010)
        def line():
            text, used = '', 0
            while used < 24:
                char = rng.choice('abc 中文Ａ')
                if used + width(char) <= 24:
                    text += char
                    used += width(char)
            return text
        for index in range(800):
            before, after = line(), line()
            chunks = tui.render([before], [after])
            with self.subTest(index=index):
                self.assertEqual(apply_diff(before, chunks), terminal_cells(after))
                for start, end in tui._changed_runs(before, after):
                    self.assertIn(start, edges(before) & edges(after))
                    self.assertIn(end, edges(before) & edges(after))

    def test_field_cursor_uses_columns_but_edits_character_indices(self):
        field = tui.Field('text', '中A文B')
        field.begin_edit()
        self.assertEqual(field.cursor, 4)
        self.assertEqual(field.display(4), '文B_')
        field.on_key('left', 0)
        self.assertEqual(field.cursor, 3)
        self.assertEqual(field.display(4), 'A文_')
        field.on_key('?', ord('?'))
        self.assertEqual(field.text, '中A文?B')
        self.assertEqual(field.display(4), '文?_')
        for size in range(1, 9):
            self.assertIn('_', field.display(size))
            self.assertLessEqual(width(field.display(size)), size)

    def test_form_chinese_label_does_not_displace_value(self):
        form = tui.Form([tui.Field('node', '12345678', label='节点')])
        grid = tui.Grid(1, 12)
        form.render(grid, tui.Rect(0, 0, 1, 12))
        self.assertEqual(grid.to_lines(), ['> 节点: 123~'])

    def test_table_natural_column_widths_measure_chinese(self):
        table = tui.Table(headers=('中文', '值'), rows=[('中', 'AB')])
        self.assertEqual(table.resolve_widths(9), [4, 2])
        grid = tui.Grid(4, 12)
        table.render(grid, tui.Rect(0, 0, 4, 12), 'ascii')
        self.assertEqual(grid.to_lines()[0], '   中文 | 值')
        self.assertTrue(all(width(row) == 12 for row in grid.to_lines()))

    def test_fit_hints_counts_columns_and_retains_whole_hints(self):
        app = tui.TuiApp(rows=24, cols=80)
        hints = ['q 退出', '? 帮助', 'F5 执行']
        self.assertEqual(app.fit_hints(hints, '', 23), 'q 退出  ? 帮助  F5 执行')
        self.assertEqual(app.fit_hints(hints, '', 15), 'q 退出  +2 keys')
        previous = 0
        for size in range(45):
            value = app.fit_hints(hints, '暂停', size)
            self.assertLessEqual(width(value), size)
            shown = sum(hint in value for hint in hints)
            self.assertGreaterEqual(shown, previous)
            previous = shown

    def test_logview_wraps_without_losing_chinese_glyphs(self):
        log = tui.LogView()
        log.append('中文A测试B')
        self.assertEqual(log._wrapped(9), ['[i] 中文A', '    测试B'])
        for size in range(1, 12):
            self.assertTrue(all(width(row) <= size for row in log._wrapped(size)))
        grid = tui.Grid(2, 9)
        log.render(grid, tui.Rect(0, 0, 2, 9))
        self.assertEqual(grid.to_lines(), ['[i] 中文A', '    测试B'])

    def test_mixed_widgets_preserve_fixed_terminal_width(self):
        for size in (1, 4, 13, 40, 80):
            grid = tui.Grid(12, size)
            grid.box(tui.Rect(0, 0, 12, size), '中文标题', 'ascii')
            tui.Menu(['状态 Status', '参数 Params']).render(grid, tui.Rect(1, 1, 2, max(0, size - 2)))
            tui.Form([tui.Field('x', '中A文', label='位置')]).render(grid, tui.Rect(3, 1, 1, max(0, size - 2)))
            tui.Table(('节点', '值'), [('中', 'A文')]).render(grid, tui.Rect(4, 1, 4, max(0, size - 2)))
            log = tui.LogView()
            log.append('中英 mixed 日志')
            log.render(grid, tui.Rect(8, 1, 3, max(0, size - 2)))
            self.assertTrue(all(width(row) == size for row in grid.to_lines()), size)
            self.assertEqual(tui.render(grid.to_lines(), grid.to_lines()), [])

    def test_zero_dimensions_remain_safe(self):
        for rows, cols in ((0, 0), (0, 80), (24, 0)):
            grid = tui.Grid(rows, cols)
            grid.put(0, -1, '中文')
            self.assertEqual(tui.render(None, grid.to_lines()), [])

    def test_initial_curses_size_matches_fallback_before_first_frame(self):
        for initial_size in ((24, 80), (30, 100)):
            with self.subTest(initial_curses_size=initial_size):
                events, size = [], [initial_size]
                screen = Mock()
                screen.getmaxyx.side_effect = lambda: size[0]
                screen.getch.return_value = ord('q')
                screen.refresh.side_effect = lambda: events.append(('refresh', size[0]))
                fake_curses = Mock(KEY_RESIZE=410)

                def resize(rows, cols):
                    size[0] = (rows, cols)
                    events.append(('resize', size[0]))

                fake_curses.resizeterm.side_effect = resize
                # Zero ioctl must resolve to the requested 30x100, while
                # curses can independently start at its default 24x80.
                with patch.object(tui, 'curses', fake_curses), \
                        patch.object(tui, '_ioctl_size', return_value=(0, 0)), \
                        patch.dict(os.environ, {}, clear=True):
                    app = tui.TuiApp(rows=30, cols=100)
                    with patch.object(app, '_install_winch'), \
                            patch.object(app, '_poll_size_change', return_value=False), \
                            patch.object(app, 'build', side_effect=lambda: tui.Grid(app.rows, app.cols)), \
                            patch.object(app, '_write', side_effect=lambda data: events.append(
                                ('margins' if data == '\x1b[r' else 'ansi', size[0]))):
                        self.assertEqual(app._main(screen), 0)
                expected = [('refresh', (30, 100)), ('margins', (30, 100)), ('ansi', (30, 100))]
                if initial_size != (30, 100):
                    fake_curses.resizeterm.assert_called_once_with(30, 100)
                    expected.insert(0, ('resize', (30, 100)))
                else:
                    fake_curses.resizeterm.assert_not_called()
                self.assertEqual(events, expected)


if __name__ == '__main__':
    unittest.main()
