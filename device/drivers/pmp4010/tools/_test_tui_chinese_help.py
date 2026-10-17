"""Chinese bench rendering/help; these supersede the removed CLI-form views."""
from contextlib import ExitStack
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest import mock

from _test_bench_support import BenchNode, environment, mockbus, options, tool
import pmp4010_tui as ui
from tui import _display_width


class ChineseHelpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bench-help-')
        self.addCleanup(self.tmp.cleanup)
        self.bus = mockbus.MockBus([BenchNode()], loopback=False)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for patch in environment(self.bus):
            self.stack.enter_context(patch)
        self.app = ui.PmpApp(tool, options(Path(self.tmp.name) / 'motion.jsonl'))
        self.addCleanup(self.app.controller.close)
        self.app.controller.rt_ack = True
        self.app.controller.od_reason = 'isolated MockBus test'
        self.app.controller.settings.update(soft_min=-2000, soft_max=500, max_displacement=364)
        self.app.controller.scan(ui.JobOutput(queue.Queue(), threading.Event()))
        self.app.controller.refresh(ui.JobOutput(queue.Queue(), threading.Event()))
        self.app.current_view = 'overview'
        self.app.initial_snapshot = False

    def press(self, name):
        key = ord(name) if len(name) == 1 else {'enter': 10, 'esc': 27}.get(name, 0)
        return self.app.dispatch_key(name, key)

    def text(self):
        return '\n'.join(self.app.build().to_lines())

    def test_bench_views_column_width_at_zero_tiny_and_standard_sizes(self):
        for rows, cols in ((0, 0), (1, 1), (24, 80), (30, 120), (40, 140)):
            self.app.rows, self.app.cols = rows, cols
            for view in ('overview', 'detail', 'action', 'rt', 'od', 'setting'):
                with self.subTest(rows=rows, cols=cols, view=view):
                    if view == 'action':
                        self.app.begin_action('absolute')
                    else:
                        self.app.current_view = view
                    lines = self.app.build().to_lines()
                    self.assertEqual(len(lines), rows)
                    self.assertTrue(all(_display_width(line) == cols for line in lines))

    def test_overview_exactly_four_slots_and_protocol_facts(self):
        text = self.text()
        self.assertEqual(text.count('离线'), 3)
        self.assertIn('PMP4010 测试台', text)
        self.assertIn('mock-bench', text)
        self.assertIn('ERROR-ACTIVE', text)
        self.assertIn('-1464', text)
        self.assertNotIn('bit15', text)

    def test_detail_uses_live_velocity_selector_unit(self):
        self.app.current_view = 'detail'
        self.assertIn('rpm', self.text())
        self.bus.node(1).set_value(0x2006, 1, 0x12)
        self.app.controller.refresh(ui.JobOutput(queue.Queue(), threading.Event()))
        self.assertIn('用户单位', self.text())

    def test_both_help_keys_restore_exact_view_and_selection(self):
        for view in ('overview', 'detail'):
            for opener in ('?', 'f1'):
                for closer in ('esc', 'q'):
                    with self.subTest(view=view, opener=opener, closer=closer):
                        self.app.current_view = view
                        self.app.selected = 2
                        before = self.text()
                        self.assertFalse(self.press(opener))
                        self.assertEqual(self.app.current_view.lower(), 'help')
                        self.assertIn('帮助', self.text())
                        self.assertFalse(self.press(closer))
                        self.assertEqual(self.app.current_view, view)
                        self.assertEqual(self.app.selected, 2)
                        self.assertEqual(self.text(), before)

    def test_help_is_data_and_has_actual_action_keys(self):
        self.assertIsInstance(ui.HELP, (dict, tuple, list))
        self.press('?')
        text = self.text()
        for marker in ('F5', '1度', '独立急停'):
            self.assertIn(marker, text)

    def test_help_does_not_execute_and_returns_to_action_draft(self):
        self.app.current_view = 'detail'
        self.app.begin_action('absolute')
        field = self.app.input_field
        if field.editing:
            self.press('enter')
        with mock.patch.object(self.app, 'review_action', side_effect=AssertionError('help reviews action')):
            self.press('f1')
            self.assertEqual(self.app.current_view.lower(), 'help')
            for key in ('f5', 'enter'):
                self.press(key)
            self.press('esc')
        self.assertEqual(self.app.current_view, 'action')
        self.assertIs(self.app.input_field, field)

    def test_editing_question_mark_is_text_not_help(self):
        self.app.current_view = 'od'
        # OD acknowledgment is text, unlike a numeric target input.
        self.app.begin_action('absolute')
        field = self.app.input_field
        if not field.editing:
            field.begin_edit()
        self.press('?')
        self.assertEqual(self.app.current_view, 'action')
        self.assertTrue(field.editing)
        self.assertIn('?', field.text)

    def test_build_does_no_transport_or_config_io(self):
        with mock.patch.object(tool, 'load_od', side_effect=AssertionError('render OD IO')), \
             mock.patch.object(tool, 'load_device_json', side_effect=AssertionError('render config IO')), \
             mock.patch.object(self.bus, 'send', side_effect=AssertionError('render bus IO')):
            for view in ('overview', 'detail'):
                self.app.current_view = view
                self.text()
            self.app.begin_action('absolute')
            self.app.input_field._draft = '-1414'
            self.text()
            self.app.review_action()
            self.assertEqual(self.app.current_view, 'confirm')
            self.text()

    def test_log_output_keeps_exact_chinese_error_and_is_bounded(self):
        logs = queue.Queue(maxsize=2)
        out = ui.JobOutput(queue.Queue(), threading.Event(), logs)
        detail = '60C2:02 恢复期望-3，读回未知；请使用独立急停'
        out.bad(detail)
        self.assertEqual(logs.get_nowait(), ('log', 'err', detail))
        for _ in range(10):
            out.info('模拟帧')
        self.assertEqual(logs.qsize(), 2)
        out.event('result', {'disabled_confirmed': False})
        self.assertFalse(out.events.empty())

    def test_hints_remain_whole_and_width_bounded(self):
        hints = ['q 退出', '?/F1 帮助', 'Enter 编辑', 'F5 审阅']
        for width in range(1, 100):
            text = self.app.fit_hints(hints, '', width)
            self.assertLessEqual(_display_width(text), width)
            for hint in hints:
                self.assertNotIn(hint + '~', text)


if __name__ == '__main__':
    unittest.main()
