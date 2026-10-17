"""Offline interaction/safety regressions: no SocketCAN, no board access."""
import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('tui_cli_test', HERE / 'pmp4010.py')
tool = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tool
spec.loader.exec_module(tool)
assert tool.std_layer_available()
import pmp4010_tui as ui
import pmp4010_position as position
import pmp4010_params as params
import mockbus


class TuiTests(unittest.TestCase):
    def setUp(self):
        self.args = tool.build_parser().parse_args([])
        self.app = ui.PmpApp(tool, self.args)
        self.app.initial_snapshot = False
        self.app.motion_specs = position.specifications(tool.load_od())
        self.app.context = ('mock-iface', 500000)
        self.cfg = {'bus': {'can': [{'logical': tool.LOGICAL_BUS, 'iface': 'mock-iface', 'bitrate': 500000}]},
                    'network': {'sync_period_us': 4000}}
        self.tmp = tempfile.TemporaryDirectory(prefix='pmp4010-tui-test-')
        self.addCleanup(self.tmp.cleanup)
        self.settings = dict(target='10', velocity='100', accel='100', decel='100',
            **{'soft-min': '-30', 'soft-max': '30', 'max-displacement': '30',
               'motion-log': str(Path(self.tmp.name) / 'motion.jsonl'), 'node': '1',
               'offline-calibration': '36000 1 3 1', 'start-position': '0',
               'offline-limits': '1000 100 1000 1000', 'od-bypass': 'offline mock acceptance'})
        self.set_values('Position', **self.settings)

    def set_values(self, view, **values):
        for f in self.app.forms[view].fields:
            if f.name in values:
                f.value = str(values[f.name])

    def view(self, name):
        self.app.show_view(name)
        self.app.set_form(self.app.forms.get(name))

    def finish(self):
        deadline = time.monotonic() + 3
        while self.app.busy and time.monotonic() < deadline:
            self.app.worker.join(.01)
            self.app.poll()
        self.assertFalse(self.app.busy, 'worker did not finish')

    def test_all_views_build_without_io_at_three_sizes(self):
        with mock.patch.object(tool, 'load_od', side_effect=AssertionError('build IO')), \
             mock.patch.object(tool, 'connect_bus', side_effect=AssertionError('build bus IO')), \
             mock.patch.object(tool, 'load_device_json', side_effect=AssertionError('build config IO')):
            for rows, cols in ((0, 0), (24, 80), (40, 140)):
                self.app.rows, self.app.cols = rows, cols
                for name in self.app.VIEWS:
                    self.view(name)
                    lines = self.app.build().to_lines()
                    self.assertEqual(len(lines), rows)
                    self.assertTrue(all(ui._display_width(line) == cols for line in lines))

    def test_q_j_slash_are_field_contents_and_enter_never_runs(self):
        self.view('Params')
        self.app.forms['Params'].selected = 2
        with mock.patch.object(tool, 'cmd_params', side_effect=AssertionError('Enter ran action')):
            self.assertFalse(self.app.dispatch_key('enter', 10))
            for char in 'qj/': self.assertFalse(self.app.dispatch_key(char, ord(char)))
            self.assertEqual(self.app.forms['Params'].current.text, 'qj/')
            self.app.dispatch_key('enter', 10)
            self.assertEqual(self.app.forms['Params'].current.value, 'qj/')
            self.assertFalse(self.app.busy)

    def test_escape_discards_draft_then_returns_menu_selection(self):
        self.app.menu.selected = 2
        self.app.dispatch_key('enter', 10)
        form = self.app.forms['Position']
        form.selected = 8
        original = form.current.value
        self.app.dispatch_key('enter', 10)
        self.app.dispatch_key('q', ord('q'))
        self.app.dispatch_key('esc', 27)
        self.assertEqual(form.current.value, original)
        self.assertEqual(self.app.current_view, 'Position')
        self.app.dispatch_key('esc', 27)
        self.assertIsNone(self.app.current_view)
        self.assertEqual(self.app.menu.selected, 2)
        self.app.dispatch_key('enter', 10)
        self.assertEqual(form.selected, 8)

    def test_live_draft_budget_and_signed_excess(self):
        f = next(f for f in self.app.forms['Position'].fields if f.name == 'max-displacement')
        f.begin_edit()
        f._draft = '40'
        text = '\n'.join(self.app.preview_lines())
        self.assertIn('位移预算=1.2度；剩余=-0.2度 已超限', text)
        self.assertIn('已阻止：', text)
        self.assertEqual(f.value, '30')

    def test_partial_target_keeps_live_budget(self):
        f = self.app.forms['Position'].fields[0]
        f.begin_edit(); f._draft = '-'
        text = '\n'.join(self.app.preview_lines())
        self.assertIn('位移预算=0.9度', text)
        self.assertIn('绝对目标 U：草稿未填完整或无效', text)
        self.assertIn('已阻止：', text)

    def test_preview_reuses_all_shared_checks_and_marks_bad_velocity(self):
        with mock.patch.object(position, 'validate', wraps=position.validate) as valid, \
             mock.patch.object(position, 'check_start', wraps=position.check_start) as start, \
             mock.patch.object(position, 'check_envelope', wraps=position.check_envelope) as envelope:
            self.assertIn('输入检查通过', '\n'.join(self.app.preview_lines()))
            self.assertTrue(valid.called and start.called and envelope.called)
        self.set_values('Position', velocity='0')
        self.assertIn('已阻止：', '\n'.join(self.app.preview_lines()))

    def test_negative_budget_and_bad_travel_block(self):
        for values in ({'max-displacement': '-1'}, {'target': '0'}, {'soft-min': '40'}, {'soft-max': '1'}):
            self.set_values('Position', **self.settings)
            self.set_values('Position', **values)
            self.assertIn('已阻止：', '\n'.join(self.app.preview_lines()))

    def test_actual_snapshot_bound_to_exact_node_and_source(self):
        cal = position.calibration([36000, 1, 3, 1]); cal['start_position'] = 0
        self.set_values('Position', apply='yes')
        self.app.snapshot = dict(calibration=cal, source='device', context=('mock-iface', 500000, 1))
        self.assertIn('设备快照', '\n'.join(self.app.preview_lines()))
        self.set_values('Position', node='2')
        self.assertIn('无有效设备快照', '\n'.join(self.app.preview_lines()))
        self.set_values('Position', node='1')
        self.app.snapshot['source'] = 'offline_simulation'
        self.assertIn('无有效设备快照', '\n'.join(self.app.preview_lines()))

    def test_editing_target_binding_discards_snapshot_even_if_draft_cancelled(self):
        self.view('Position')
        form = self.app.forms['Position']
        form.selected = next(i for i, f in enumerate(form.fields) if f.name == 'node')
        self.app.snapshot = {'source': 'device'}
        self.app.dispatch_key('enter', 10)
        self.app.dispatch_key('2', ord('2'))
        self.assertIsNone(self.app.snapshot)
        self.app.dispatch_key('esc', 27)
        self.assertEqual(form.current.value, '1')
        self.assertIsNone(self.app.snapshot)

    def test_f6_snapshot_does_not_require_position_target(self):
        self.view('Position')
        self.set_values('Position', target='', velocity='', apply='yes')
        with mock.patch.object(self.app, '_launch') as launch:
            self.app._snapshot()
            launch.assert_called_once()
            result = launch.call_args.args[1]
            with mock.patch.object(self.app, '_prepare', return_value='review') as prepare:
                result(None)
            a = prepare.call_args.args[1]
            self.assertTrue(a.calibrate and a.apply)
            self.assertFalse(a.position)

    def test_full_offline_position_uses_cli_and_jsonl_and_invalidates_start(self):
        self.view('Position')
        out = ui.JobOutput(self.app.events, threading.Event(), self.app.logs)
        with mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock-config')), \
             mock.patch.object(tool, 'connect_bus', side_effect=AssertionError('real bus')):
            review = self.app._prepare('Position', self.app._args('Position'), out)
            self.app.pending = review
            with mock.patch.object(tool, 'cmd_position', wraps=tool.cmd_position) as cli:
                self.app._execute(review)
                self.finish()
                cli.assert_called_once()
        events = [__import__('json').loads(line) for line in Path(self.settings['motion-log']).read_text().splitlines()]
        self.assertEqual(events[-1]['event'], 'result')
        self.assertTrue(events[-1]['disabled_confirmed'])
        self.assertEqual(events[-1]['source'], 'offline_simulation')
        self.assertIn('离线模拟失能=True', self.app.recovery)
        self.assertIsNone(self.app.snapshot)

    def test_od_gate_blocks_tui_review_with_no_bus(self):
        self.set_values('Position', **{'od-bypass': ''})
        with mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock')), \
             mock.patch.object(tool, 'connect_bus', side_effect=AssertionError('opened')):
            with self.assertRaisesRegex(ValueError, 'OD 闸门'):
                self.app._prepare('Position', self.app._args('Position'), ui.JobOutput(queue.Queue(), threading.Event()))

    def test_confirmation_mismatch_never_executes(self):
        a = self.app._args('Position')
        review = ('review', 'Position', a, ('mock-iface', 500000, 1), ['target=10'], None)
        self.app.confirm = dict(review=review, expected='RUN mock-iface 1 10', stage=1, form=ui.Form([ui.field('confirm', 'wrong')]))
        with mock.patch.object(self.app, '_execute') as execute:
            self.app._confirm_key('f5')
            execute.assert_not_called()

    def test_save_requires_two_distinct_f5_confirmations(self):
        a = copy.copy(self.args); a.params = 'save'; a.apply = True
        backup = dict(iface='mock-iface', node=1, identity={'4': 42})
        review = ('review', 'Params', a, ('mock-iface', 500000, 1), ['1010:01 magic'], backup)
        self.app.confirm = dict(review=review, expected='PERSIST mock-iface 1', stage=1,
            form=ui.Form([ui.field('confirm', 'PERSIST mock-iface 1')]))
        with mock.patch.object(self.app, '_execute') as execute:
            self.app._confirm_key('f5')
            self.assertEqual(self.app.confirm['stage'], 2)
            execute.assert_not_called()
            self.app.confirm['form'].fields[0].value = 'SAVE mock-iface 1 42'
            self.app._confirm_key('f5')
            execute.assert_called_once_with(review)
            self.assertEqual(a.confirm_save, params.confirmation(backup))

    def test_log_scroll_works_in_confirmation(self):
        self.view('Params')
        self.app.dispatch_key('pgup', 0)
        self.assertFalse(self.app.log.follow)
        self.app.dispatch_key('g', ord('g'))
        self.assertTrue(self.app.log.follow)

    def test_busy_disallows_enter_and_q_requests_cancel_without_exit(self):
        release = threading.Event()
        self.view('Params')
        self.app._launch('test wait', lambda out: release.wait(1))
        self.app.dispatch_key('enter', 10)
        self.assertFalse(self.app.forms['Params'].editing)
        self.assertFalse(self.app.dispatch_key('q', ord('q')))
        self.assertTrue(self.app.cancel.is_set())
        release.set(); self.finish()

    def test_bounded_raw_logs_do_not_delay_control_result(self):
        out = ui.JobOutput(self.app.events, threading.Event(), self.app.logs)
        for _ in range(5000): out.info('raw mock frame')
        self.assertEqual(self.app.logs.qsize(), 2000)
        out.event('result', dict(disabled_confirmed=False))
        self.app.poll()
        self.assertIn('设备状态未知', self.app.recovery)
        self.assertEqual(self.app.logs.qsize(), 1872)

    def test_readonly_calibration_results_visible(self):
        self.view('Calibrate')
        self.app.snapshot = dict(source='device', context=('mock-iface', 500000, 1),
            calibration=position.calibration([36000, 1, 3, 1]))
        text = '\n'.join(self.app.build().to_lines())
        self.assertIn('608F 每转计数 inc/rev=36000 / 1', text)
        self.assertIn('电机轴 1度 = 33.3333333 用户单位', text)

    def test_target_config_drift_is_refused_inside_shared_position_command(self):
        a = self.app._args('Position'); a.apply = True
        a.offline_calibration = a.offline_limits = a.start_position = None
        a._tui_expected_target = ('old-iface', 500000, 1)
        out = ui.JobOutput(queue.Queue(), threading.Event())
        with mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock')), \
             mock.patch.object(tool, 'require_rt_ack', return_value=True), \
             mock.patch.object(tool, 'connect_bus', side_effect=AssertionError('opened')):
            with self.assertRaisesRegex(ValueError, 'Target changed'):
                tool.cmd_position(a, out)

    def test_od_drift_refused_inside_shared_position_command(self):
        a = self.app._args('Position'); a._tui_expected_od = {}
        with self.assertRaisesRegex(ValueError, 'OD changed'):
            tool.cmd_position(a, ui.JobOutput(queue.Queue(), threading.Event()))

    def test_sigint_prints_final_result_after_worker_cleanup(self):
        def worker(out):
            while not out.cancel_event.is_set(): time.sleep(.001)
            out.event('result', dict(disabled_confirmed=False))
        self.app._launch('mock operation', worker)
        with mock.patch.object(ui.TuiApp, 'run', side_effect=KeyboardInterrupt()), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.app.run(), 130)
        self.assertFalse(self.app.busy)
        self.assertIn('设备状态未知', output.getvalue())

    def test_params_offline_plan_does_not_claim_readback(self):
        a = copy.copy(self.args); a.params = 'interpolation'; a.apply = False
        self.app.active_review = ('review', 'Params', a, None, [], None)
        self.app._launch('Params running', lambda out: 0)
        self.finish()
        self.assertIn('零总线读写', self.app.recovery)
        self.assertNotIn('读回已确认', self.app.recovery)

    def test_quarantine_latches_physical_iface_node_across_bitrate_changes(self):
        a = copy.copy(self.args); a.apply = True
        self.app.active_review = ('review', 'Calibrate', a, ('mock-iface', 500000, 1), [], None)
        self.app.events.put(('event', 'session_state', {'quarantined': True, 'source': 'device'}))
        self.app.poll()
        self.assertIn(('mock-iface', 1), self.app.blocked_targets)
        self.assertIn('必须外部恢复', self.app.recovery)
        cfg = copy.deepcopy(self.cfg); cfg['bus']['can'][0]['bitrate'] = 250000
        a.node = 1
        with mock.patch.object(tool, 'load_device_json', return_value=(cfg, 'mock')), \
             mock.patch.object(tool, 'connect_bus', side_effect=AssertionError('quarantine bypass')):
            with self.assertRaisesRegex(ValueError, '必须外部恢复'):
                self.app._prepare('Calibrate', a, ui.JobOutput(queue.Queue(), threading.Event()))
        self.app.events.put(('event', 'session_state', {'quarantined': True, 'source': 'offline_simulation'}))
        self.app.active_review = ('review', 'Calibrate', a, ('another-iface', 500000, 2), [], None)
        self.app.poll()
        self.assertNotIn(('another-iface', 2), self.app.blocked_targets)

    def test_params_plan_drift_refused_before_any_bus_open(self):
        a = copy.copy(self.args); a.params = 'interpolation'; a.node = 1
        a.od_gaps_bypass = 'offline plan'; a._tui_expected_writes = [('bogus', 1)]
        with mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock')), \
             mock.patch.object(tool, 'connect_bus', side_effect=AssertionError('opened')):
            with self.assertRaisesRegex(ValueError, 'parameter plan changed'):
                tool.cmd_params(a, ui.JobOutput(queue.Queue(), threading.Event()))

    def test_failed_recovery_keeps_failed_object_and_expected_values_in_log(self):
        self.app.events.put(('event', 'parameter_recovery', {'recovery': 'unknown',
            'failures': ['60C2:02 恢复期望-3，读回未知']}))
        self.app.poll()
        self.assertIn('状态未知', self.app.recovery)
        self.assertIn('60C2:02', self.app.progress)
        self.assertIn('-3', self.app.progress)
        self.assertIn('恢复期望-3，读回未知', self.app.progress)

    def test_raw_listener_uses_only_recv_and_closes_on_cancel(self):
        bus = mockbus.MockBus([], loopback=False)
        original = bus.recv
        calls = []
        def recv(timeout):
            calls.append(timeout)
            if len(calls) == 3: self.app.cancel.set()
            return original(timeout)
        with mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock')), \
             mock.patch.object(tool, 'connect_bus', return_value=bus), \
             mock.patch.object(bus, 'recv', side_effect=recv):
            result = self.app._raw(ui.JobOutput(queue.Queue(), self.app.cancel))
        self.assertEqual(result, ('raw', 0))
        self.assertEqual(bus.sent, [])
        self.assertTrue(bus.closed)

    def test_shared_read_commands_emit_quarantine_and_close_mock_bus(self):
        actual = dict(state='UP', bitrate=500000, can_state='ERROR-ACTIVE')
        for action in ('calibrate', 'backup'):
            with self.subTest(action=action):
                a = copy.copy(self.args); a.node = 1; a.timeout = .001
                a.od_gaps_bypass = 'offline mocked read test'
                a.calibrate = action == 'calibrate'; a.position = False
                a.apply = a.calibrate
                a.params = 'backup'; a.backup = str(Path(self.tmp.name) / 'unused-backup.json')
                bus = mockbus.MockBus([], loopback=False)
                out = ui.JobOutput(queue.Queue(), threading.Event())
                with mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock')), \
                     mock.patch.object(tool, 'require_rt_ack', return_value=True), \
                     mock.patch.object(tool, 'read_iface_actual', return_value=actual), \
                     mock.patch.object(tool, 'connect_bus', return_value=bus):
                    with self.assertRaises(Exception):
                        (tool.cmd_position if a.calibrate else tool.cmd_params)(a, out)
                self.assertTrue(bus.closed)
                self.assertTrue(all(frame.data[0] == 64 for frame in bus.sent))
                self.assertIn(('event', 'session_state', {'quarantined': True, 'source': 'device'}), list(out.events.queue))


class CancellationTests(unittest.TestCase):
    def parameter_client(self):
        od = tool.load_od(); specs = params.specifications(od)
        values = {}
        for key, s in specs.items():
            value = 1 if s['role'] == 'command' else s['ranges'][0][0]
            values[s['index_value'], s['sub']] = mockbus.OdEntry(params.encode(s, value, s['role'] != 'command'), writable=True)
        identity = next(e for e in od['entries'] if e['key'] == 'identity_object')
        for sub in identity['identity_subindices']:
            values[int(identity['index'], 0), sub] = mockbus.OdEntry((sub*100).to_bytes(4, 'little'))
        status = next(e for e in od['entries'] if e['key'] == 'statusword')
        values[int(status['index'], 0), status['sub']] = mockbus.OdEntry(tool._canopen.CIA402_DISABLED.to_bytes(2, 'little'))
        bus = mockbus.MockBus([mockbus.MockNode(1, od=values)], loopback=False)
        client = tool._canopen.SdoClient(bus, 1, timeout=.01)
        backup = params.capture(client, od, specs, 'mock-iface', 500000, 1)
        bus.sent.clear()
        return od, specs, client, backup, bus

    def test_cancel_after_final_preread_before_first_download(self):
        od, specs, client, backup, bus = self.parameter_client()
        writes = params.plan('interpolation', specs, period_us=4000)
        out = ui.JobOutput(queue.Queue(), threading.Event())
        original = client.read
        last = specs[writes[-1][0]]
        def read(index, sub):
            raw = original(index, sub)
            if (index, sub) == (last['index_value'], last['sub']): out.cancel_event.set()
            return raw
        with mock.patch.object(client, 'read', side_effect=read):
            with self.assertRaises(KeyboardInterrupt):
                params.execute(client, od, specs, backup, writes, 'interpolation', out)
        self.assertTrue(all(frame.data[0] == 64 for frame in bus.sent))
        events = list(out.events.queue)
        self.assertIn(('event', 'parameter_recovery', {'recovery': 'no_writes'}), events)
        bus.close()

    def test_cancel_after_first_download_restores_with_same_client(self):
        od, specs, client, backup, bus = self.parameter_client()
        writes = params.plan('interpolation', specs, period_us=4000)
        out = ui.JobOutput(queue.Queue(), threading.Event())
        first = specs[writes[0][0]]; before = params.read_value(client, first)
        original = client.write
        def write(index, sub, value):
            result = original(index, sub, value)
            out.cancel_event.set()
            return result
        with mock.patch.object(client, 'write', side_effect=write):
            with self.assertRaises(KeyboardInterrupt):
                params.execute(client, od, specs, backup, writes, 'interpolation', out)
        self.assertEqual(params.read_value(client, first), before)
        self.assertIn(('event', 'parameter_recovery', {'recovery': 'restored_confirmed'}), list(out.events.queue))
        bus.close()

    def test_position_cancel_does_not_cancel_cleanup(self):
        args = tool.build_parser().parse_args(['--position', '--node', '1', '--target', '10', '--velocity', '100',
            '--acceleration', '100', '--deceleration', '100', '--soft-min', '-30', '--soft-max', '30',
            '--max-displacement', '30', '--offline-calibration', '36000', '1', '3', '1',
            '--offline-limits', '1000', '100', '1000', '1000', '--start-position', '0'])
        specs = position.specifications(tool.load_od())
        bus = position.offline_bus(tool._canopen, specs, args)
        client = tool._canopen.SdoClient(bus, 1, timeout=.01)
        out = ui.JobOutput(queue.Queue(), threading.Event())
        session = tool.MotionSession(client, out, .05)
        session.cancel_event = out.cancel_event
        class Journal:
            events = []
            def event(self, name, **data):
                self.events.append((name, data))
                if name == 'before': out.cancel_event.set()
        journal = Journal()
        with self.assertRaises(KeyboardInterrupt): position.run(session, args, specs, journal)
        self.assertIsNone(session.cancel_event)
        result = next(data for name, data in journal.events if name == 'result')
        self.assertTrue(result['disabled_confirmed'])
        self.assertFalse(result['completed'])
        self.assertIs(session.client, client)
        bus.close()


if __name__ == '__main__': unittest.main()
