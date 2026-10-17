"""Chinese UI/help interaction checks; only pure rendering and offline mocks."""
import copy
import importlib.util
from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('chinese_help_cli', HERE / 'pmp4010.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
import pmp4010_tui as ui
from tui import _display_width


class ChineseHelpTests(unittest.TestCase):
    def setUp(self):
        self.app = ui.PmpApp(tool, tool.build_parser().parse_args([]))
        self.app.initial_snapshot = False
        self.app.rows, self.app.cols = 24, 80
        self.addCleanup(mock.patch.stopall)
        for name in ('connect_bus', 'load_od', 'load_device_json', 'read_iface_actual', 'service_active'):
            mock.patch.object(tool, name, side_effect=AssertionError('unexpected I/O: ' + name)).start()

    def view(self, name):
        self.app.show_view(name)
        self.app.set_form(self.app.forms.get(name))

    def press(self, name):
        key = ord(name) if len(name) == 1 else {'enter': 10, 'esc': 27}.get(name, 0)
        return self.app.dispatch_key(name, key)

    def text(self):
        return '\n'.join(self.app.build().to_lines())

    def test_all_five_views_chinese_labels_and_terminal_column_width(self):
        self.assertEqual(self.app.VIEWS, ('Status', 'Calibrate', 'Position', 'Params', 'Raw frames'))
        self.assertEqual(self.app.menu.items, list(self.app.VIEWS))
        for rows, cols in ((0, 0), (1, 1), (24, 80), (30, 100), (40, 140)):
            for view in self.app.VIEWS:
                with self.subTest(rows=rows, cols=cols, view=view):
                    self.app.rows, self.app.cols = rows, cols
                    self.view(view)
                    lines = self.app.build().to_lines()
                    self.assertEqual(len(lines), rows)
                    self.assertTrue(all(_display_width(line) == cols for line in lines))
                    if cols >= 80:
                        self.assertIn(ui.VIEW_LABELS[view], '\n'.join(lines))

    def test_status_uses_panel_label_zh_and_preserves_protocol_facts(self):
        self.app.status_rows = [('CAN state', 'CAN 状态', 'ERROR-ACTIVE', 'mock'),
                                ('iface', '内核接口', 'mock-iface', 'mock')]
        self.view('Status')
        text = self.text()
        self.assertIn('CAN 状态', text)
        self.assertIn('内核接口', text)
        self.assertIn('ERROR-ACTIVE', text)
        self.assertIn('mock-iface', text)
        self.assertNotIn('CAN state', text)

    def test_field_names_and_argument_keys_stay_english(self):
        self.view('Params')
        form = self.app.forms['Params']
        self.assertEqual(form.fields[0].name, 'action')
        self.assertEqual(form.fields[0].label, '操作')
        self.assertIn('备份路径', self.text())
        self.assertEqual(self.app._values('Params')['action'], 'backup')

    def test_both_help_keys_restore_exact_view_focus_selection_and_screen(self):
        for opener in ('?', 'f1'):
            for closer in ('esc', 'q'):
                for view in self.app.VIEWS:
                    with self.subTest(opener=opener, closer=closer, view=view):
                        self.view(view)
                        form = self.app.focused_form
                        if form:
                            form.selected = min(2, len(form.fields)-1)
                        before = self.app.build().to_lines()
                        offset = form.offset if form else None
                        self.assertFalse(self.press(opener))
                        self.assertEqual(self.app.current_view, 'Help')
                        self.assertIsNone(self.app.focused_form)
                        self.assertIn('帮助：' + ui.VIEW_LABELS[view], self.text())
                        self.assertIn('当前视图：' + ui.VIEW_LABELS[view], self.text())
                        self.assertFalse(self.press(closer))
                        self.assertEqual(self.app.current_view, view)
                        self.assertIs(self.app.focused_form, form)
                        self.assertEqual(self.app.build().to_lines(), before)
                        if form:
                            self.assertEqual(form.offset, offset)
                            self.assertEqual(form.selected, 2)

    def test_help_from_menu_preserves_menu_selection(self):
        self.app.menu.selected = 3
        before = self.app.build().to_lines()
        self.press('?')
        self.assertIn('帮助：主菜单', self.text())
        self.press('q')
        self.assertIsNone(self.app.current_view)
        self.assertEqual(self.app.menu.selected, 3)
        self.assertEqual(self.app.build().to_lines(), before)
        self.press('enter')
        self.assertEqual(self.app.current_view, 'Params')

    def test_question_mark_is_text_and_f1_is_consumed_in_editor(self):
        self.view('Calibrate')
        form = self.app.focused_form
        form.selected = 2
        self.press('enter')
        self.press('?')
        self.press('f1')
        self.assertEqual(form.current.text, '?')
        self.assertEqual(self.app.current_view, 'Calibrate')
        self.assertTrue(form.editing)
        self.assertFalse(self.app.busy)
        self.press('esc')
        self.assertEqual(form.current.value, '')

    def test_help_content_is_data_and_view_specific(self):
        self.assertEqual(set(ui.HELP['views']), set(self.app.VIEWS))
        for view, (summary, details) in ui.HELP['views'].items():
            lines = self.app.help_lines(view)
            self.assertIn(summary, lines)
            for line in details:
                self.assertIn(line, lines)
        self.assertTrue(any('F6' in line for line in self.app.help_lines('Position')))
        self.assertFalse(any('F6' in line for line in self.app.help_lines('Status')))

    def test_help_scrolls_and_four_safety_lines_stay_visible(self):
        self.view('Position')
        self.press('?')
        first = self.text()
        self.assertGreater(self.app.help_log.line_count, 10)
        for warning in ui.HELP['safety']:
            self.assertIn(warning, first)
        self.press('pgdn')
        second = self.text()
        self.assertNotEqual(first, second)
        for warning in ui.HELP['safety']:
            self.assertIn(warning, second)
        self.press('home')
        self.assertIn('当前视图：位置运动', self.text())
        self.press('end')
        end = self.text()
        self.assertIn(ui.HELP['views']['Raw frames'][0], end)
        self.press('up')
        self.assertFalse(self.app.help_log.follow)

    def test_help_handles_zero_size_and_tiny_terminal(self):
        self.press('?')
        for rows, cols in ((0, 0), (1, 1), (3, 3), (24, 80)):
            self.app.rows, self.app.cols = rows, cols
            lines = self.app.build().to_lines()
            self.assertEqual(len(lines), rows)
            self.assertTrue(all(_display_width(line) == cols for line in lines))

    def test_help_never_executes_action_or_changes_snapshot(self):
        self.view('Position')
        self.app.snapshot = {'source': 'device'}
        with mock.patch.object(self.app, '_launch', side_effect=AssertionError('action from help')):
            self.press('?')
            for key in ('f5', 'f6', 'enter', '?', 'f1'):
                self.press(key)
            self.press('esc')
        self.assertEqual(self.app.snapshot, {'source': 'device'})

    def test_help_during_busy_task_does_not_cancel_task(self):
        self.view('Raw frames')
        self.app.worker = object()
        self.press('?')
        self.assertFalse(self.app.cancel.is_set())
        self.press('q')
        self.assertFalse(self.app.cancel.is_set())
        self.assertFalse(self.app.exit_after)
        self.assertEqual(self.app.current_view, 'Raw frames')
        self.app.worker = None

    def test_help_preserves_save_second_confirmation_form(self):
        self.view('Params')
        a = copy.copy(self.app.args)
        a.apply, a.params = True, 'save'
        backup = dict(iface='mock-iface', node=1, identity={'4': 42})
        review = ('review', 'Params', a, ('mock-iface', 500000, 1), [], backup)
        form = ui.Form([ui.field('confirm-save')])
        self.app.confirm = dict(review=review, expected='SAVE mock-iface 1 42', stage=2, form=form)
        self.app.set_form(form)
        with mock.patch.object(self.app, '_execute', side_effect=AssertionError('help executes save')):
            self.press('f1')
            self.press('f5')
            self.press('q')
        self.assertIs(self.app.focused_form, form)
        self.assertEqual(self.app.confirm['stage'], 2)
        self.assertEqual(form.fields[0].value, '')

    def test_review_finished_under_help_keeps_help_focus_isolated(self):
        self.view('Calibrate')
        self.press('?')
        a = copy.copy(self.app.args)
        a.apply = False
        review = ('review', 'Calibrate', a, ('mock-iface', 500000, 1), [], None)
        self.app.worker = mock.Mock()
        self.app.events.put(('done', 'review', review, None))
        self.app.poll()
        self.assertEqual(self.app.current_view, 'Help')
        self.assertIsNone(self.app.focused_form)
        self.press('esc')
        self.assertEqual(self.app.current_view, 'Calibrate')
        self.assertIs(self.app.focused_form, self.app.confirm['form'])

    def test_job_output_preserves_exact_chinese_error_and_counts_one_line(self):
        logs = queue.Queue(maxsize=2)
        out = ui.JobOutput(queue.Queue(), threading.Event(), logs)
        detail = '60C2:02 恢复期望-3，读回未知；请使用独立急停'
        out.bad(detail)
        self.assertEqual(logs.get_nowait(), ('log', 'err', detail))
        self.assertTrue(logs.empty())
        out.info('60C2:02 pre-read=-3')
        self.assertEqual(logs.get_nowait(), ('log', 'info', '60C2:02 写前读取=-3'))

    def test_hints_f6_only_in_views_that_implement_it(self):
        for view in self.app.VIEWS:
            self.view(view)
            self.assertEqual(any('F6' in hint for hint in self.app.hints()), view in ('Position', 'Calibrate'))

    def test_hints_remain_whole_and_width_bounded(self):
        hints = ['q 退出', '?/F1 帮助', 'Enter 编辑', 'F5 审阅']
        for width in range(1, 100):
            text = self.app.fit_hints(hints, '', width)
            self.assertLessEqual(_display_width(text), width)
            for hint in hints:
                self.assertNotIn(hint + '~', text)


if __name__ == '__main__':
    unittest.main()
