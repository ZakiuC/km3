"""PMP4010 bench acceptance: standard MockBus only; no CAN interface exists."""
from contextlib import ExitStack, redirect_stdout
import copy
import json
import io
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest import mock

from _test_bench_support import BenchNode, DelayedBus, environment, mockbus, options, position, requests, tool
import pmp4010_tui as ui
from pmp4010_bench import BenchController


class BenchFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pmp4010-bench-test-')
        self.addCleanup(self.tmp.cleanup)
        self.node = BenchNode()
        self.bus = DelayedBus([self.node])
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for patch in environment(self.bus):
            self.stack.enter_context(patch)
        self.args = options(Path(self.tmp.name) / 'motion.jsonl')
        self.ctl = BenchController(tool, self.args)
        self.addCleanup(self.ctl.close)
        self.out = ui.JobOutput(queue.Queue(), threading.Event())

    def online(self):
        self.ctl.rt_ack = True
        self.ctl.scan(self.out)
        self.ctl.refresh(self.out)
        return self.ctl.rows[0]

    def authorized(self):
        self.online()
        self.ctl.od_reason = 'isolated MockBus acceptance'
        self.ctl.settings.update(soft_min=-2000, soft_max=500, max_displacement=364)

    def review(self, action='absolute', value=-1414):
        return self.ctl.review(action, 1, value, self.out)

    def enabled_pp_start(self):
        # Exercise the retained ordinary-enabled runner explicitly. Strict
        # disabled starts now have their own sealed transaction tests.
        self.node.set_value(0x6041, 0x427)
        self.ctl.refresh(self.out)



class BenchTests(BenchFixture):
    def test_constructor_no_io_and_four_slots(self):
        tool.connect_bus.assert_not_called()
        tool._std_open_active.assert_not_called()
        tool.load_device_json.assert_not_called()
        self.assertEqual(len(self.ctl.rows), 4)
        self.assertFalse(any(row['online'] for row in self.ctl.rows))

    def test_startup_rt_ack_is_required_before_scan(self):
        with self.assertRaises(ValueError):
            self.ctl.scan(self.out)
        self.assertEqual(self.bus.sent, [])

    def test_scan_four_slots_one_online_three_offline_upload_only(self):
        row = self.online()
        rows = self.ctl.rows
        self.assertEqual([r['node'] for r in rows], [1, None, None, None])
        self.assertEqual([r['online'] for r in rows], [True, False, False, False])
        self.assertEqual(row['values']['actual_position'], -1464)
        self.assertEqual(row['values']['actual_velocity'], 0)
        self.assertTrue(all(f.arbitration_id != 0 for f in self.bus.sent))
        self.assertFalse(requests(self.bus, writing=True))
        print('BENCH scan: four slots; node 1 online; three unassigned slots offline; SDO uploads only; NMT=0')

    def test_velocity_unit_selector_is_read_and_preserved(self):
        self.assertEqual(self.online()['values']['velocity_feedback_unit'], 0)
        self.node.set_value(0x2006, 1, 0x12)
        self.ctl.refresh(self.out)
        self.assertEqual(self.ctl.rows[0]['values']['velocity_feedback_unit'], 1)
        self.assertIn((0x2006, 0x12, 0), requests(self.bus, writing=False))

    def test_refresh_loss_preserves_all_last_values_and_calibration(self):
        before = self.online()
        self.node.drop_reads.add((0x6041, 0))
        self.ctl.refresh(self.out)
        after = self.ctl.rows[0]
        self.assertEqual(after['values'], before['values'])
        self.assertEqual(after['calibration'], before['calibration'])
        self.assertTrue(after['lost'])
        self.assertTrue(after['quarantined'])

    def test_quarantine_is_sticky_no_retry_or_client_recreation(self):
        self.online()
        self.node.drop_reads.add((0x6041, 0))
        self.ctl.refresh(self.out)
        count = len(self.bus.sent)
        self.node.drop_reads.clear()
        self.ctl.refresh(self.out)
        self.ctl.scan(self.out)
        self.assertEqual(len(self.bus.sent), count)
        self.assertTrue(self.ctl.rows[0]['quarantined'])
        with self.assertRaises(ValueError):
            self.review()
        self.assertEqual(len(self.bus.sent), count)
        print('BENCH quarantine: timeout latched; rescan/refresh/action cannot recreate client or send another request')

    def test_motion_requires_od_ack_and_operator_budget(self):
        self.online()
        self.ctl.settings.update(soft_min=-2000, soft_max=500, max_displacement=364)
        with self.assertRaises(ValueError):
            self.review()
        self.ctl.od_reason = 'local MockBus'
        self.ctl.settings['max_displacement'] = None
        with self.assertRaises(ValueError):
            self.review()
        self.assertFalse(requests(self.bus, writing=True))

    def test_review_reuses_all_shared_position_guards(self):
        self.authorized()
        with mock.patch.object(position, 'validate', wraps=position.validate) as valid, \
             mock.patch.object(position, 'check_start', wraps=position.check_start) as start, \
             mock.patch.object(position, 'check_envelope', wraps=position.check_envelope) as envelope, \
             mock.patch.object(tool, 'refuse_motion', wraps=tool.refuse_motion) as od:
            review = self.review()
        self.assertTrue(valid.called and start.called and envelope.called and od.called)
        self.assertEqual(review['target'], -1414)
        self.assertEqual(review['start'], -1464)
        self.assertIn('mock-bench', review['token'])
        self.assertIn('1', review['token'])
        self.assertIn('-1414', review['token'])
        self.assertFalse(requests(self.bus, writing=True))

    def test_manual_budget_over_one_degree_is_blocked(self):
        self.authorized()
        self.ctl.settings['max_displacement'] = 365
        with self.assertRaisesRegex(ValueError, '1度'):
            self.review()
        self.assertFalse(requests(self.bus, writing=True))

    def test_oversize_target_and_soft_envelope_are_blocked(self):
        self.authorized()
        for target in (-1464, -1099, -2001, 501):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.review(value=target)
        self.assertFalse(requests(self.bus, writing=True))

    def test_relative_uses_absolute_target_and_rejects_moving_velocity(self):
        self.authorized()
        review = self.review('relative', 50)
        self.assertEqual(review['target'], -1414)
        self.node.set_value(0x606c, 1)
        self.ctl.refresh(self.out)
        with self.assertRaises(ValueError):
            self.review('relative', 50)
        self.assertFalse(requests(self.bus, writing=True))
        print('BENCH relative: -1464 + 50 = absolute -1414; nonzero actual velocity rejected before write')

    def test_relative_rejects_not_target_reached(self):
        self.authorized()
        self.node.set_value(0x6041, 0x27)
        self.ctl.refresh(self.out)
        with self.assertRaises(ValueError):
            self.review('relative', 50)
        self.assertFalse(requests(self.bus, writing=True))

    def test_review_start_drift_refused_before_target_write(self):
        self.authorized()
        review = self.review('relative', 50)
        self.node.set_value(0x6064, -1463)
        with self.assertRaises(ValueError):
            self.ctl.execute(review, self.out)
        self.assertFalse(any(index == 0x607a for index, _, _ in requests(self.bus, True)))

    def test_review_target_context_drift_refused_before_writes(self):
        self.authorized()
        review = self.review()
        cfg = {'bus': {'can': [{'logical': tool.LOGICAL_BUS, 'iface': 'different-mock', 'bitrate': 1000000}]}}
        with mock.patch.object(tool, 'load_device_json', return_value=(cfg, 'mock-config')):
            with self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_review_od_drift_refused_before_writes(self):
        self.authorized()
        review = self.review()
        od = copy.deepcopy(tool.load_od()); od['bench_test_drift'] = True
        with mock.patch.object(tool, 'load_od', return_value=od):
            with self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_position_transaction_shared_runner_cleanup_and_absolute_wire(self):
        self.authorized()
        self.enabled_pp_start()
        review = self.review('relative', 50)
        with mock.patch.object(position, 'run', wraps=position.run) as run:
            self.ctl.execute(review, self.out)
        run.assert_called_once()
        target_bytes = [f.data[4:8] for f in self.bus.sent if f.data[:4] == b'\x23\x7a\x60\x00']
        self.assertEqual(target_bytes, [(-1414).to_bytes(4, 'little', signed=True)])
        words = [value for index, sub, value in requests(self.bus, True) if index == 0x6040]
        self.assertEqual(words[-2:], [2, 0])
        self.assertEqual(self.node.value(0x6064, signed=True), -1414)
        self.assertFalse(any(index == 0x60c1 for index, _, _ in requests(self.bus, True)))
        self.assertTrue(all(f.arbitration_id != 0 for f in self.bus.sent))

    def test_zero_method35_and_explicit_0f_to_1f_edge(self):
        self.authorized()
        review = self.review('zero', None)
        self.ctl.execute(review, self.out)
        writes = requests(self.bus, True)
        self.assertIn((0x6098, 0, 35), writes)
        words = [value for index, sub, value in writes if index == 0x6040]
        trigger = words.index(0x1f)
        self.assertEqual(words[trigger-1], 0x0f)
        self.assertEqual(words[-2:], [2, 0])
        self.assertFalse(any(index == 0x607c for index, _, _ in writes))
        self.assertEqual(self.node.value(0x6064, signed=True), 0)
        self.assertIsNone(self.ctl.settings['max_displacement'])
        print('BENCH zero: 6098:00=35; controlword edge 0x000F -> 0x001F; 607C writes=0; cleanup [0x0002,0x0000]')

    def test_lost_write_ack_does_not_claim_disabled_and_latches_quarantine(self):
        self.authorized()
        self.enabled_pp_start()
        review = self.review()
        self.node.drop_write_ack.add((0x6040, 0))
        with self.assertRaises(Exception):
            self.ctl.execute(review, self.out)
        self.assertTrue(self.ctl.rows[0]['quarantined'])
        results = [entry[2] for entry in list(self.out.events.queue) if entry[:2] == ('event', 'result')]
        self.assertTrue(results)
        self.assertFalse(results[-1]['disabled_confirmed'])
        self.assertTrue(results[-1]['independent_estop_required'])
        print('BENCH lost write ACK: same quarantined client; disabled_confirmed=false; independent_estop_required=true')

    def test_start_drift_during_setup_is_refused_before_607a_download(self):
        self.authorized()
        self.enabled_pp_start()
        review = self.review('relative', 50)
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if frame.data[:4] == b'\x2f\x60\x60\x00':
                self.node.set_value(0x6064, -1463)
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send):
            with self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
        self.assertFalse(any(index == 0x607a for index, _, _ in requests(self.bus, True)))
        words = [value for index, sub, value in requests(self.bus, True) if index == 0x6040]
        self.assertEqual(words[-2:], [2, 0])
        print('BENCH setup race: start drift after mode write; target 607A never downloaded; cleanup confirmed')

    def test_cancel_during_transaction_confirms_disabled_with_same_client(self):
        self.authorized()
        self.enabled_pp_start()
        review = self.review()
        event = self.out.event
        def observe(name, data):
            event(name, data)
            if name == 'before':
                self.out.cancel_event.set()
        self.out.event = observe
        with self.assertRaises(KeyboardInterrupt):
            self.ctl.execute(review, self.out)
        results = [entry[2] for entry in list(self.out.events.queue) if entry[:2] == ('event', 'result')]
        self.assertTrue(results[-1]['disabled_confirmed'])
        self.assertFalse(results[-1]['completed'])
        self.assertFalse(self.ctl.rows[0]['quarantined'])
        print('BENCH cancellation: disabled_confirmed=true; completed=false; cleanup ignores transaction cancellation')

    def test_stale_snapshot_rejects_relative_before_writes(self):
        self.authorized()
        row = self.ctl.row(1)
        row['updated'] = time.monotonic() - self.ctl.freshness - .1
        self.ctl._put(row)
        with self.assertRaises(ValueError):
            self.review('relative', 50)
        self.assertFalse(requests(self.bus, True))

    def test_zero_refuses_nonzero_origin_offset_and_never_writes_607c(self):
        self.authorized()
        self.node.set_value(0x607c, 20)
        self.ctl.refresh(self.out)
        with self.assertRaisesRegex(ValueError, '607C'):
            self.review('zero', None)
        self.assertFalse(requests(self.bus, True))

    def test_fault_refused_without_auto_reset(self):
        self.authorized()
        self.node.set_value(0x6041, 0x448)
        self.ctl.refresh(self.out)
        with self.assertRaises(ValueError):
            self.review()
        self.assertFalse(requests(self.bus, True))

    def test_speed_modes_review_zero_target_and_enable_without_nonzero_action(self):
        self.authorized()
        for mode in (3, 9):
            self.node.set_value(0x6060, mode)
            self.node.set_value(0x6061, mode)
            self.node.set_value(0x6041, 0x440)
            self.ctl.refresh(self.out)
            self.bus.sent.clear()
            with self.subTest(mode=mode):
                review = self.review('enable', None)
                self.assertEqual(review['writes'][0]['key'], 'target_velocity')
                self.assertEqual(review['writes'][0]['value'], 0)
                self.assertFalse(requests(self.bus, True))
                self.ctl.execute(review, self.out)
                writes = requests(self.bus, True)
                self.assertEqual(writes[0], (0x60ff, 0, 0))
                self.assertEqual(self.node.value(0x60ff, signed=True), 0)
        for action in ('velocity', 'pv', 'csv'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.review(action, 10)

    def test_csv_zero_target_enable_rejects_residual_offset_without_clearing_it(self):
        self.authorized()
        self.node.set_value(0x6060, 9)
        self.node.set_value(0x6061, 9)
        self.node.set_value(0x60b1, -1)
        self.ctl.refresh(self.out)
        review = self.review('enable', None)
        self.bus.sent.clear()
        with self.assertRaisesRegex(tool.MotionError, '60B1h'):
            self.ctl.execute(review, self.out)
        self.assertEqual(self.node.value(0x60b1, signed=True), -1)
        self.assertFalse(requests(self.bus, True))

    def test_csv_offset_read_timeout_stays_quarantined_without_enable_or_retry(self):
        self.authorized()
        self.node.set_value(0x6060, 9)
        self.node.set_value(0x6061, 9)
        self.ctl.refresh(self.out)
        review = self.review('enable', None)
        self.node.drop_reads.add((0x60b1, 0))
        self.bus.sent.clear()
        with self.assertRaises(tool.MotionError):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))
        before = len(self.bus.sent)
        self.node.drop_reads.clear()
        self.ctl.refresh(self.out)
        with self.assertRaises(ValueError):
            self.review('enable', None)
        self.assertEqual(len(self.bus.sent), before)

    def test_speed_enable_review_rejects_mode_drift_and_tampered_target(self):
        self.authorized()
        self.node.set_value(0x6060, 3)
        self.node.set_value(0x6061, 3)
        self.ctl.refresh(self.out)
        review = self.review('enable', None)
        self.node.set_value(0x6061, 9)
        self.bus.sent.clear()
        with self.assertRaisesRegex(ValueError, '模式'):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))
        self.node.set_value(0x6061, 3)
        self.ctl.refresh(self.out)
        review = self.review('enable', None)
        review['writes'][0]['value'] = 1
        self.bus.sent.clear()
        with self.assertRaisesRegex(ValueError, '60FFh=0'):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_telemetry_and_action_clients_are_separate_on_one_transport(self):
        self.authorized()
        self.enabled_pp_start()
        telemetry = self.ctl.refresh_clients[1]
        clients = []
        original = tool._canopen.SdoClient
        def client(*args, **kwargs):
            result = original(*args, **kwargs)
            clients.append(result)
            return result
        review = self.review()
        with mock.patch.object(tool._canopen, 'SdoClient', side_effect=client):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(clients), 1)
        self.assertIsNot(clients[0], telemetry)
        self.assertIs(clients[0].bus, telemetry.bus)
        tool._std_open_active.assert_called_once()

    def test_refresh_yields_while_motion_owns_transport_lock(self):
        self.authorized()
        count = len(self.bus.sent)
        with self.ctl.lock:
            self.assertFalse(self.ctl.refresh(self.out))
        self.assertEqual(len(self.bus.sent), count)

    def test_enable_postflight_envelope_failure_cleans_up_same_client(self):
        self.authorized()
        review = self.review('enable', None)
        original = self.bus.send
        state = {'enabled': False, 'reads': 0}
        def send(frame):
            if frame.data[:4] == b'\x2b\x40\x60\x00' and int.from_bytes(frame.data[4:6], 'little') == 15:
                state['enabled'] = True
            if state['enabled'] and frame.data[:4] == b'\x40\x41\x60\x00':
                state['reads'] += 1
                if state['reads'] == 2:
                    self.node.set_value(0x6064, 1000)
            return original(frame)
        with mock.patch.object(self.bus, 'send', side_effect=send):
            with self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
        words = [value for index, _, value in requests(self.bus, True) if index == 0x6040]
        self.assertEqual(words[-2:], [2, 0])
        self.assertEqual(self.node.value(0x6041) & 0x4f, 0x40)
        self.assertFalse(self.ctl.rows[0]['quarantined'])
        print('BENCH enable postflight: out-of-envelope readback causes same-client quick-stop + disable')

    def test_enable_postflight_timeout_reports_unknown_without_replacement_client(self):
        self.authorized()
        review = self.review('enable', None)
        original_send = self.bus.send
        original_client = tool._canopen.SdoClient
        state = {'enabled': False, 'reads': 0}
        clients = []
        def client(*args, **kwargs):
            result = original_client(*args, **kwargs)
            clients.append(result)
            return result
        def send(frame):
            if frame.data[:4] == b'\x2b\x40\x60\x00' and int.from_bytes(frame.data[4:6], 'little') == 15:
                state['enabled'] = True
            if state['enabled'] and frame.data[:4] == b'\x40\x41\x60\x00':
                state['reads'] += 1
                if state['reads'] == 2:
                    self.node.drop_reads.add((0x6041, 0))
            return original_send(frame)
        with mock.patch.object(self.bus, 'send', side_effect=send), \
             mock.patch.object(tool._canopen, 'SdoClient', side_effect=client):
            with self.assertRaises(Exception):
                self.ctl.execute(review, self.out)
        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].quarantined)
        self.assertTrue(self.ctl.rows[0]['quarantined'])
        result = [entry[2] for entry in list(self.out.events.queue) if entry[:2] == ('event', 'bench_result')][-1]
        self.assertFalse(result['disabled_confirmed'])
        self.assertTrue(result['state_unknown'])
        self.assertIsNone(result['readback'])
        print('BENCH enable postflight timeout: disabled_confirmed=false; state_unknown=true; replacement clients=0')

    def test_zero_coordinate_reset_accepts_old_bounds_excluding_zero(self):
        self.authorized()
        self.ctl.settings.update(soft_min=-1500, soft_max=-1400, max_displacement=50)
        review = self.review('zero', None)
        self.ctl.execute(review, self.out)
        self.assertEqual(self.node.value(0x6064, signed=True), 0)
        self.assertEqual(self.ctl.settings, dict(soft_min=None, soft_max=None, max_displacement=None))
        writes = requests(self.bus, True)
        self.assertFalse(any(index in (0x607a, 0x607c) for index, _, _ in writes))
        self.assertFalse(any('剩余 -' in line for line in review['lines']))

    def test_disable_and_stop_cancel_after_download_still_confirm_cleanup(self):
        self.authorized()
        original = self.bus.send
        for action, word in (('disable', 0), ('stop', 2)):
            with self.subTest(action=action):
                self.out.cancel_event.clear()
                self.ctl.refresh(self.out)
                review = self.review(action, None)
                self.bus.sent.clear()
                self.out.cancel_event.clear()
                cancelled = [False]
                def send(frame):
                    result = original(frame)
                    if (not cancelled[0] and frame.data[:4] == b'\x2b\x40\x60\x00'
                            and int.from_bytes(frame.data[4:6], 'little') == word):
                        cancelled[0] = True
                        self.out.cancel_event.set()
                    return result
                with mock.patch.object(self.bus, 'send', side_effect=send):
                    with self.assertRaises(KeyboardInterrupt):
                        self.ctl.execute(review, self.out)
                words = [value for index, _, value in requests(self.bus, True) if index == 0x6040]
                self.assertEqual(words[-2:], [2, 0])
                self.assertEqual(self.node.value(0x6041) & 0x4f, 0x40)
                self.assertFalse(self.ctl.rows[0]['quarantined'])


class BenchInteractionTests(BenchFixture):
    def app(self):
        self.authorized()
        app = ui.PmpApp(tool, self.args)
        app.controller = self.ctl
        app.initial_snapshot = False
        app.next_refresh = float('inf')
        app.current_view = 'detail'
        return app

    @staticmethod
    def press(app, name):
        return app.dispatch_key(name, ord(name) if len(name) == 1 else {'enter': 10, 'esc': 27}.get(name, 0))

    def test_alarm_meanings_visible_in_80x24_detail_and_four_seat_overview(self):
        from pmp4010_faults import FAULT_DESCRIPTIONS, format_fault
        app = self.app()
        app.rows, app.cols = 24, 80
        row = self.ctl.row(1)
        # Exercise every label: longest Flash/STO messages must fit as well.
        for code in [None, 0, 0xA123, *FAULT_DESCRIPTIONS]:
            row['values']['error_code_object'] = code
            self.ctl._put(row)
            expected = '报警 ' + format_fault(code)
            app.current_view = 'detail'
            self.assertIn(expected, '\n'.join(app.build().to_lines()))
            app.current_view = 'overview'
            self.assertIn(format_fault(code), '\n'.join(app.build().to_lines()))
        row['values']['error_code_object'] = 0x2312
        for node in range(1,5):
            self.ctl._put(dict(copy.deepcopy(row), node=node))
        text = '\n'.join(app.build().to_lines())
        self.assertEqual(text.count('0x2312（电机堵转）'), 4)
        app.cols = 160
        row['values'].update(actual_position=-2147483648, actual_velocity=123456789, modes_of_operation_display=8)
        self.ctl._put(row)
        text = '\n'.join(app.build().to_lines())
        self.assertIn('-2147483648U / 123456789 rpm / CSP', text)

    def test_delayed_mockbus_sdo_does_not_block_key_help_or_build(self):
        app = self.app()
        self.bus.delay_key = (0x6041, 0)
        try:
            app._start_refresh()
            self.assertTrue(self.bus.entered.wait(.5))
            worker = app.refresh_worker
            self.assertIsNotNone(worker)
            started = time.monotonic()
            self.press(app, '?')
            text = '\n'.join(app.build().to_lines())
            latency = time.monotonic() - started
            self.assertEqual(app.current_view, 'help')
            self.assertIn('帮助', text)
            self.assertTrue(worker.is_alive())
            self.assertLess(latency, .1)
            # Hold beyond the real SDO deadline, then let it time out.
            time.sleep(.03)
        finally:
            self.bus.release.set()
            if app.refresh_worker:
                app.refresh_worker.join(1)
                app.poll()
        self.assertIsNone(app.refresh_worker)
        self.press(app, 'esc')
        self.assertIn('已隔离', '\n'.join(app.build().to_lines()))
        self.assertIn('-1464', '\n'.join(app.build().to_lines()))
        print('BENCH nonblocking: help key + rendered screen {:.6f}s; worker still blocked past SDO timeout; quarantine visible'.format(latency))

    def test_review_then_exact_confirmation_required_before_execute(self):
        app = self.app()
        app.begin_action('absolute')
        app.input_field._draft = '-1414'
        with mock.patch.object(self.ctl, 'execute', return_value=0) as execute:
            self.press(app, 'enter')
            self.assertEqual(app.current_view, 'action')
            execute.assert_not_called()
            self.press(app, 'f5')
            self.assertEqual(app.current_view, 'confirm')
            execute.assert_not_called()
            self.assertEqual(app.pending['target'], -1414)
            self.assertEqual(app.confirm['expected'], 'ABSOLUTE mock-bench 1 -1414')
            self.assertEqual(app.input_field.value, '')
            app.input_field._draft = 'ABSOLUTE another-interface 1 -1414'
            self.press(app, 'f5')
            execute.assert_not_called()
            self.assertEqual(app.current_view, 'confirm')
            app.input_field.value = app.pending['token']
            self.press(app, 'f5')
            app.worker.join(1)
            app.poll()
            execute.assert_called_once()
        self.assertFalse(requests(self.bus, True))

    def test_action_edit_consumes_q_question_mark_and_escape_cancels(self):
        app = self.app()
        app.begin_action('absolute')
        for key in ('q', '?', 'j', '/'):
            self.assertFalse(self.press(app, key))
        self.assertEqual(app.current_view, 'action')
        self.assertTrue(app.input_field.text.endswith('q?j/'))
        self.press(app, 'esc')
        self.assertFalse(app.input_field.editing)
        self.assertEqual(app.input_field.value, '-1464')
        self.press(app, 'esc')
        self.assertEqual(app.current_view, 'detail')
        self.assertFalse(requests(self.bus, True))

    def test_od_dialog_exact_session_ack_then_budget_remains_required(self):
        app = self.app()
        self.ctl.od_reason = None
        self.ctl.settings = dict(soft_min=None, soft_max=None, max_displacement=None)
        app.begin_action('relative')
        self.assertEqual(app.current_view, 'od')
        app.input_field._draft = 'yes'
        self.press(app, 'f5')
        self.assertEqual(app.current_view, 'od')
        self.assertIsNone(self.ctl.od_reason)
        app.input_field.value = 'OD mock-bench 1'
        self.press(app, 'f5')
        self.assertEqual(app.current_view, 'setting')
        for value in ('-2000', '500', '364'):
            self.assertEqual(app.input_field.value, '')
            app.input_field._draft = value
            self.press(app, 'f5')
        self.assertEqual(app.current_view, 'action')
        self.assertEqual(self.ctl.settings['max_displacement'], 364)
        self.assertFalse(requests(self.bus, True))

    def test_busy_q_waits_for_worker_cleanup(self):
        app = self.app()
        release = threading.Event()
        app._launch('execute', lambda out: release.wait(1))
        self.assertFalse(self.press(app, 'q'))
        self.assertTrue(app.cancel.is_set())
        self.assertTrue(app.exit_after)
        self.assertFalse(self.bus.closed)
        release.set()
        app.worker.join(1)
        app.poll()
        self.assertIsNone(app.worker)
        self.assertTrue(self.bus.closed)

    def test_actual_keyboardinterrupt_drains_and_reports_worker_cleanup(self):
        for disabled in (True, False):
            with self.subTest(disabled=disabled):
                app = self.app()
                def joined():
                    app.events.put(('event', 'bench_result', {'disabled_confirmed': disabled,
                        'mechanical_stop_confirmed': False}))
                app.worker = mock.Mock()
                app.worker.join.side_effect = joined
                output = io.StringIO()
                with mock.patch.object(ui.TuiApp, 'run', side_effect=KeyboardInterrupt()), redirect_stdout(output):
                    try:
                        result = app.run()
                    except KeyboardInterrupt:
                        result = 130
                self.assertEqual(result, 130)
                self.assertIn('失能', output.getvalue())
                self.assertIn(str(disabled), output.getvalue())
                self.assertIn('未知', output.getvalue())


    def test_interrupt_before_startup_does_not_launch_initial_scan(self):
        app = ui.PmpApp(tool, self.args)
        app.controller.rt_ack = True
        app.initial_snapshot = True
        with mock.patch.object(app, '_start_scan', side_effect=AssertionError('shutdown initiated scan')), \
             mock.patch.object(ui.TuiApp, 'run', side_effect=KeyboardInterrupt()), redirect_stdout(io.StringIO()):
            try:
                result = app.run()
            except KeyboardInterrupt:
                result = 130
        self.assertEqual(result, 130)


class BenchRoundTwoTests(BenchFixture):
    def enable(self):
        self.authorized()
        self.ctl.execute(self.review('enable', None), self.out)

    def test_exit_shutdown_precedes_close_uses_original_client_and_only_controlword(self):
        self.enable()
        original = self.ctl.enabled_sessions[1].client
        self.bus.sent.clear()
        close = self.bus.close
        snapshots = []
        def closed():
            snapshots.append((self.node.value(0x6040), self.node.value(0x6041), list(requests(self.bus))))
            close()
        with mock.patch.object(self.bus, 'close', side_effect=closed), \
             mock.patch.object(tool._canopen, 'SdoClient', side_effect=AssertionError('replacement client')):
            result = self.ctl.close(self.out)
        self.assertTrue(result[0]['disabled_confirmed'])
        self.assertIs(self.ctl.enabled_sessions[1].client, original)
        self.assertEqual(snapshots[0][:2], (6, 0x421))
        self.assertEqual(requests(self.bus, True), [(0x6040, 0, 6)])
        self.assertIn((0x6041, 0, 0), snapshots[0][2])
        self.assertIn('失能已确认', '\n'.join(self.ctl.shutdown_lines))
        count = len(self.bus.sent)
        self.ctl.close(self.out)
        self.assertEqual(len(self.bus.sent), count)

    def test_exit_remembers_enable_after_later_successful_disable(self):
        self.enable()
        self.ctl.refresh(self.out)
        self.ctl.execute(self.review('disable', None), self.out)
        self.assertFalse(self.node.value(0x6041) & 4)
        self.bus.sent.clear()
        self.ctl.close(self.out)
        self.assertEqual(requests(self.bus, True), [(0x6040, 0, 6)])

    def test_exit_readback_timeout_reports_exact_unknown_then_closes_without_retry(self):
        self.enable()
        self.node.drop_reads.add((0x6041, 0))
        self.bus.sent.clear()
        result = self.ctl.close(self.out)
        self.assertFalse(result[0]['disabled_confirmed'])
        self.assertEqual(requests(self.bus, True), [(0x6040, 0, 6)])
        self.assertEqual(sum(index == 0x6041 for index, _, _ in requests(self.bus, False)), 1)
        self.assertIn('节点 1 失能未确认，状态未知，须独立急停', self.ctl.shutdown_lines)
        self.assertTrue(self.bus.closed)

    def test_exit_quarantined_enable_ack_loss_never_sends_or_recreates_client(self):
        self.authorized()
        self.node.drop_write_ack.add((0x6040, 0))
        with self.assertRaises(Exception):
            self.ctl.execute(self.review('enable', None), self.out)
        count = len(self.bus.sent)
        with mock.patch.object(tool._canopen, 'SdoClient', side_effect=AssertionError('replacement client')):
            self.ctl.close(self.out)
        self.assertEqual(len(self.bus.sent), count)
        self.assertIn('节点 1 失能未确认，状态未知，须独立急停', self.ctl.shutdown_lines)
        self.assertTrue(self.bus.closed)

    def test_exit_tracks_pp_and_home_sessions_even_after_normal_cleanup(self):
        self.authorized()
        for action, value in [('relative', 50), ('zero', None)]:
            with self.subTest(action=action):
                if action == 'relative':
                    self.enabled_pp_start()
                self.ctl.refresh(self.out)
                self.ctl.execute(self.review(action, value), self.out)
                self.assertIn(1, self.ctl.enabled_sessions)
        self.bus.sent.clear()
        self.ctl.close(self.out)
        self.assertEqual(requests(self.bus, True), [(0x6040, 0, 6)])

    def test_exit_rejects_non_cia402_selector_without_controlword_download(self):
        self.enable()
        self.node.set_value(0x2002, 1, 1)
        self.bus.sent.clear()
        self.ctl.close(self.out)
        self.assertFalse(requests(self.bus, True))
        self.assertIn('节点 1 失能未确认，状态未知，须独立急停', self.ctl.shutdown_lines)

    def test_exit_enabled_status_stuck_is_unknown_bounded_and_closes(self):
        self.enable()
        original = self.node.handle
        def handle(frame, now):
            original(frame, now)
            if frame.data[:4] == b'\x2b\x40\x60\x00':
                self.node.set_value(0x6041, 0x427)
        self.node.handle = handle
        started = time.monotonic()
        self.ctl.close(self.out)
        self.assertLess(time.monotonic() - started, .5)
        self.assertFalse(self.ctl.shutdown_results[0]['disabled_confirmed'])
        self.assertTrue(self.bus.closed)

    def test_exit_telemetry_quarantine_blocks_healthy_original_action_client(self):
        self.enable()
        self.node.drop_reads.add((0x6041, 0))
        self.ctl.refresh(self.out)
        self.assertFalse(self.ctl.enabled_sessions[1].client.quarantined)
        count = len(self.bus.sent)
        self.ctl.close(self.out)
        self.assertEqual(len(self.bus.sent), count)
        self.assertFalse(self.ctl.shutdown_results[0]['disabled_confirmed'])

    def test_exit_failure_on_one_node_does_not_prevent_other_node_shutdown(self):
        self.bus.nodes.append(BenchNode(2))
        self.authorized()
        for node in (1, 2):
            review = self.ctl.review('enable', node, None, self.out)
            self.ctl.execute(review, self.out)
        self.node.drop_reads.add((0x6041, 0))
        self.bus.sent.clear()
        results = self.ctl.close(self.out)
        self.assertEqual([(r['node'], r['disabled_confirmed']) for r in results], [(1, False), (2, True)])
        self.assertEqual(requests(self.bus, True), [(0x6040, 0, 6)] * 2)
        self.assertTrue(self.bus.closed)

    def test_review_raw_integer_is_same_negative_relative_wire_plan(self):
        self.authorized()
        self.enabled_pp_start()
        review = self.review('relative', 50)
        target = next(w for w in review['writes'] if w['key'] == 'target_position')
        self.assertEqual((target['index'], target['type'], target['value']), (0x607a, 'int32', -1414))
        self.assertIn('写 607Ah = -1414（int32，用户单位）', review['lines'])
        self.ctl.execute(review, self.out)
        self.assertIn((0x607a, 0, (-1414) & 0xffffffff), requests(self.bus, True))

    def test_zero_review_has_controlword_trigger_and_no_fake_target_plan(self):
        self.authorized()
        review = self.review('zero', None)
        self.assertIn('写 6040h = 0x001F（uint16，触发回零）', review['lines'])
        self.assertFalse(any(w['index'] == 0x607a for w in review['writes']))
        from pmp4010_bench import write_text
        self.assertEqual(write_text(dict(key='target_velocity', index=0x60ff, type='int32', value=-7)),
                         '写 60FFh = -7（int32）')
        with self.assertRaises(ValueError):
            self.review('velocity', -7)

    def batch_online(self):
        self.bus.nodes.extend(BenchNode(n) for n in (2, 3))
        self.authorized()
        row = self.ctl.row(3)
        row.update(quarantined=True, quarantine_reason='模拟隔离')
        self.ctl._put(row)
        self.ctl.blocked[self.ctl.context[0], 3] = '模拟隔离'

    def test_batch_disable_two_online_skip_quarantined_and_results_per_node(self):
        self.batch_online()
        review = self.ctl.review_batch('disable_all', self.out)
        self.assertEqual(review['token'], 'DISABLE ALL 1,2')
        self.assertIn('节点 3：已隔离，跳过', review['lines'])
        self.bus.sent.clear()
        result = self.ctl.execute_batch(review, self.out)
        self.assertEqual([(r['node'], r['success']) for r in result], [(1, True), (2, True)])
        writes = [f for f in self.bus.sent if f.data[0] in (0x2b, 0x23, 0x2f)]
        self.assertEqual([f.arbitration_id for f in writes], [tool._canopen.cob_id_sdo_tx(n) for n in (1, 2)])
        self.assertFalse(any(f.arbitration_id == tool._canopen.cob_id_sdo_tx(3) for f in self.bus.sent))

    def test_batch_stop_has_no_parameter_or_motion_writes(self):
        self.batch_online()
        review = self.ctl.review_batch('stop_all', self.out)
        self.assertEqual(review['token'], 'STOP ALL 1,2')
        self.bus.sent.clear()
        result = self.ctl.execute_batch(review, self.out)
        self.assertTrue(all(r['success'] for r in result))
        self.assertEqual(requests(self.bus, True), [(0x6040, 0, 2)] * 2)

    def test_batch_failure_reports_unknown_continues_other_nodes_and_never_retries(self):
        self.batch_online()
        review = self.ctl.review_batch('disable_all', self.out)
        self.node.drop_write_ack.add((0x6040, 0))
        self.bus.sent.clear()
        result = self.ctl.execute_batch(review, self.out)
        self.assertEqual([(r['success'], r['state_unknown']) for r in result], [(False, True), (True, False)])
        self.assertEqual(sum(f.arbitration_id == tool._canopen.cob_id_sdo_tx(1) and f.data[0] == 0x2b
                             for f in self.bus.sent), 1)

    def test_batch_no_online_od_guard_and_membership_drift_fail_before_writes(self):
        with self.assertRaisesRegex(ValueError, '无在线节点'):
            self.ctl.review_batch('disable_all', self.out)
        self.batch_online()
        self.ctl.od_reason = None
        with self.assertRaises(ValueError):
            self.ctl.review_batch('disable_all', self.out)
        self.ctl.od_reason = 'isolated MockBus'
        review = self.ctl.review_batch('disable_all', self.out)
        row = self.ctl.row(2); row['lost'] = True; self.ctl._put(row)
        self.bus.sent.clear()
        with self.assertRaisesRegex(ValueError, '清单'):
            self.ctl.execute_batch(review, self.out)
        self.assertFalse(requests(self.bus, True))


class BenchRoundTwoInteractionTests(BenchFixture):
    app = BenchInteractionTests.app
    press = staticmethod(BenchInteractionTests.press)

    def test_policy_all_writes_confirm_readonly_no_confirmation(self):
        from pmp4010_bench import ACTION_POLICY
        for action in ('absolute', 'relative', 'zero', 'enable', 'disable', 'stop', 'disable_all', 'stop_all'):
            self.assertTrue(ACTION_POLICY[action]['confirm'])
            self.assertTrue(ACTION_POLICY[action]['od'])
        for action in ('scan', 'refresh', 'detail', 'help'):
            self.assertFalse(ACTION_POLICY[action]['confirm'])
        app = self.app()
        for action in ('enable', 'disable', 'stop', 'zero'):
            with self.subTest(action=action):
                app.begin_action(action)
                self.assertEqual(app.current_view, 'action')
                app.review_action()
                self.assertEqual(app.current_view, 'confirm')
                self.assertIsNotNone(app.pending)
                app.set_form(None)
        self.assertFalse(requests(self.bus, True))

    def test_review_rechecks_shared_od_and_settings_requirements(self):
        app = self.app()
        app.begin_action('enable')
        self.ctl.od_reason = None
        app.review_action()
        self.assertEqual(app.current_view, 'od')
        self.assertIsNone(app.pending)
        self.ctl.od_reason = 'isolated MockBus'
        app.begin_action('enable')
        self.ctl.settings['max_displacement'] = None
        app.review_action()
        self.assertEqual(app.current_view, 'setting')
        self.assertFalse(requests(self.bus, True))

    def test_overview_batch_requires_review_and_exact_action_nodes_token(self):
        app = self.app()
        app.current_view = 'overview'
        self.press(app, 'd')
        self.assertEqual(app.current_view, 'action')
        self.press(app, 'f5')
        self.assertEqual(app.current_view, 'confirm')
        self.assertEqual(app.pending['token'], 'DISABLE ALL 1')
        with mock.patch.object(self.ctl, 'execute_batch', return_value=[]) as execute:
            app.input_field._draft = 'DISABLE ALL 2'
            self.press(app, 'f5')
            execute.assert_not_called()
            app.input_field.value = 'DISABLE ALL 1'
            self.press(app, 'f5')
            app.worker.join(1); app.poll()
            execute.assert_called_once()
        self.assertFalse(requests(self.bus, True))

    def test_idle_enable_q_shutdown_is_async_and_finally_prints_unknown(self):
        app = self.app()
        self.ctl.execute(self.review('enable', None), self.out)
        self.node.drop_reads.add((0x6041, 0))
        output = io.StringIO()
        def run():
            self.assertFalse(self.press(app, 'q'))
            self.assertEqual(app.progress, '退出失能确认中')
            app.worker.join(1)
            app.poll()
            return 0
        with mock.patch.object(ui.TuiApp, 'run', side_effect=run), redirect_stdout(output):
            self.assertEqual(app.run(), 0)
        self.assertTrue(self.bus.closed)
        self.assertIn('节点 1 失能未确认，状态未知，须独立急停', output.getvalue())

    def test_busy_enable_abort_cleanup_finishes_before_exit_shutdown_and_close(self):
        app = self.app()
        review = self.review('enable', None)
        enabled, release = threading.Event(), threading.Event()
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if (frame.data[:4] == b'\x2b\x40\x60\x00'
                    and int.from_bytes(frame.data[4:6], 'little') == 15):
                enabled.set()
                self.assertTrue(release.wait(1))
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send):
            app._launch('execute', lambda out: self.ctl.execute(review, out))
            try:
                self.assertTrue(enabled.wait(1))
                self.assertFalse(self.press(app, 'q'))
                self.assertFalse(self.bus.closed)
            finally:
                release.set()
                app.worker.join(1)
            app.poll()
            if app.worker:
                app.worker.join(1)
                app.poll()
        self.assertTrue(self.bus.closed)
        words = [v for index, _, v in requests(self.bus, True) if index == 0x6040]
        self.assertEqual(words[-3:], [2, 0, 6])
        self.assertTrue(self.ctl.shutdown_results[0]['disabled_confirmed'])
        self.assertIn('退出失能确认=True', app.recovery)

    def test_sigint_enabled_cleanup_uses_shutdown_before_terminal_report(self):
        app = self.app()
        self.ctl.execute(self.review('enable', None), self.out)
        self.bus.sent.clear()
        output = io.StringIO()
        with mock.patch.object(ui.TuiApp, 'run', side_effect=KeyboardInterrupt()), redirect_stdout(output):
            with self.assertRaises(KeyboardInterrupt):
                app.run()
        self.assertEqual(requests(self.bus, True), [(0x6040, 0, 6)])
        self.assertIn('operation enabled=0，失能已确认', output.getvalue())
        self.assertTrue(self.bus.closed)


if __name__ == '__main__':
    unittest.main()
