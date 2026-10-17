"""C acceptance: independent expedited SDO peer, no hardware transport."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('position_cli_test', HERE / 'pmp4010.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
assert tool.std_layer_available()
import canopen
import pmp4010_position as position


class Clock:
    now = 0.0
    def monotonic(self):
        return self.now
    def sleep(self, amount):
        self.now += amount


class Peer:
    """Literal wire responder. State only changes on observed accepted downloads."""
    def __init__(self, clock):
        self.clock = clock
        self.values = {(0x2002, 1): b'\0\0', (0x6060, 0): b'\x01', (0x6061, 0): b'\x01',
                       (0x6040, 0): b'\0\0', (0x6041, 0): b'\x40\0'}
        for key, value in [((0x6064, 0), 0), ((0x607a, 0), 0), ((0x608f, 1), 36000),
                           ((0x608f, 2), 1), ((0x6091, 1), 3), ((0x6091, 2), 1),
                           ((0x607f, 0), 1000), ((0x6080, 0), 100), ((0x60c5, 0), 1000),
                           ((0x60c6, 0), 1000), ((0x6081, 0), 0), ((0x6082, 0), 99),
                           ((0x6083, 0), 0), ((0x6084, 0), 0)]:
            self.values[key] = value.to_bytes(4, 'little', signed=key == (0x6064, 0))
        self.sent, self.pending, self.events = [], [], []
        self.triggered = False
        self.stale = self.no_ack = self.overrun = self.freeze_move = self.freeze_disable = False
        self.ignored = self.abort = self.lost_ack = self.interrupt = None
        self.closed = False
        self.interrupt_word = None
        self.post_read_failure = False
        self.fast_completion = False
        self.stale_ack = False
        self.sw = 0x40

    def send(self, frame):
        self.sent.append(frame)
        assert frame.arbitration_id == 0x601
        cs, lo, hi, sub = frame.data[:4]
        key = (lo | hi << 8, sub)
        head = bytes(frame.data[1:4])
        if cs == 0x40:
            if self.post_read_failure and self.triggered and self.sw == 0x40 and key == (0x6064, 0):
                self.pending.append(canopen.Frame(0x581, b'\x80' + head + b'\x00\x00\x02\x06'))
                return
            value = self.sw.to_bytes(2, 'little') if key == (0x6041, 0) else self.values[key]
            self.events.append(('read', key, value))
            reply = bytes([{1: 0x4f, 2: 0x4b, 4: 0x43}[len(value)]]) + head + value.ljust(4, b'\0')
        else:
            size = {0x2f: 1, 0x2b: 2, 0x23: 4}[cs]
            value = bytes(frame.data[4:4+size])
            self.events.append(('write', key, value))
            if key == self.abort:
                self.pending.append(canopen.Frame(0x581, b'\x80' + head + b'\x30\x00\x09\x06'))
                return
            if key != self.ignored:
                self.values[key] = value
            if key == (0x6060, 0):
                self.values[0x6061, 0] = value
            if key == (0x6040, 0):
                word = int.from_bytes(value, 'little')
                if word == 0 and self.freeze_disable and self.triggered:
                    pass
                else:
                    self.sw = {0: 0x40, 2: 7, 6: 0x21, 7: 0x23, 15: 0x427,
                               0x2f: 0x427, 0x3f: 0x427 if self.stale or self.no_ack else 0x1027}[word]
                if word == 0x2f and self.stale_ack:
                    self.sw = 0x1427
                if word == 0x3f:
                    self.triggered = True
                    if self.fast_completion:
                        self.sw = 0x1427
                        self.values[0x6064, 0] = self.values[0x607a, 0]
                    if self.overrun:
                        self.values[0x6064, 0] = (31).to_bytes(4, 'little')
                    if self.no_ack:
                        self.sw = 0x27
                if word == 0x2f and self.triggered and not self.freeze_move:
                    self.values[0x6064, 0] = self.values[0x607a, 0]
                if self.freeze_move and self.triggered and word == 0x2f:
                    self.sw = 0x27
                if word == self.interrupt_word:
                    self.interrupt_word = None
                    raise KeyboardInterrupt()
                if word == self.lost_ack:
                    return
            if key == self.interrupt:
                self.interrupt = None
                raise KeyboardInterrupt()
            reply = b'\x60' + head + b'\0'*4
        self.pending.append(canopen.Frame(0x581, reply))

    def recv(self, timeout):
        self.clock.now += min(.001, timeout)
        return self.pending.pop(0) if self.pending else None

    def close(self):
        self.closed = True

    def controls(self):
        return [int.from_bytes(v, 'little') for action, key, v in self.events
                if action == 'write' and key == (0x6040, 0)]


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='km3-position-')
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.peer = Peer(self.clock)
        self.logs = 0
        self.cfg = {'bus': {'can': [{'logical': tool.LOGICAL_BUS, 'iface': 'mockcan', 'bitrate': 1000000}]}}
        for patch in [mock.patch.object(tool, 'connect_bus', return_value=self.peer),
                      mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock-config')),
                      mock.patch.object(tool, 'read_iface_actual', return_value={'state': 'UP', 'bitrate': 1000000, 'can_state': 'ERROR-ACTIVE'}),
                      mock.patch.object(tool, 'require_rt_ack', return_value=True),
                      mock.patch.object(tool.time, 'monotonic', side_effect=self.clock.monotonic),
                      mock.patch.object(tool.time, 'sleep', side_effect=self.clock.sleep),
                      mock.patch.object(tool._canbus.socket, 'socket', side_effect=AssertionError('real socket forbidden'))]:
            patch.start()
            self.addCleanup(patch.stop)

    def invoke(self, *extra, apply=True, bypass=True):
        self.logs += 1
        self.path = Path(self.tmp.name) / ('motion-{}.jsonl'.format(self.logs))
        args = ['--position', '--node', '1', '--target', '10', '--velocity', '100',
                '--acceleration', '100', '--deceleration', '100', '--soft-min', '-30', '--soft-max', '30',
                '--max-displacement', '30', '--state-timeout', '.2', '--move-timeout', '2', '--motion-log', str(self.path)]
        if apply:
            args += ['--apply']
        else:
            args += ['--offline-calibration', '36000', '1', '3', '1', '--offline-limits', '1000', '100', '1000', '1000', '--start-position', '0']
        if bypass:
            args += ['--od-gaps-bypass', 'isolated local mock']
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = tool.main(args + list(extra))
        self.output = output.getvalue()
        return rc

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def assert_disabled(self):
        self.assertEqual(self.peer.sw, 0x40)
        result = self.records()[-1]
        self.assertTrue(result['disabled_confirmed'])
        self.assertFalse(result['mechanical_stop_confirmed'])
        self.assertEqual(self.peer.controls()[-2:], [2, 0])

    def test_offline_default_and_explicit_dryrun_zero_real_frames(self):
        for extra in ([], ['--dry-run']):
            self.assertEqual(self.invoke(*extra, apply=False), 0, self.output)
            self.assertEqual(self.peer.sent, [])
            tool.connect_bus.assert_not_called()
            tool.read_iface_actual.assert_not_called()
            tool.require_rt_ack.assert_not_called()
            self.assertIn('真实发送计数=0', self.output)
            self.assertIn('0x601#2B4060003F000000', self.output)
            self.assertTrue(all(r['source'] == 'offline_simulation' for r in self.records()))
        print('C offline default/explicit dry-run: real_frame_count=0 real_bus_open_count=0')

    def test_preview_exactly_matches_independent_peer_nominal_requests(self):
        self.assertEqual(self.invoke(apply=False), 0, self.output)
        plans = [line.split(': ', 1)[1] for line in self.output.splitlines() if '拟发帧 ' in line]
        self.assertEqual(self.invoke(), 0, self.output)
        actual = ['0x{:03X}#{}'.format(f.arbitration_id, f.data.hex().upper()) for f in self.peer.sent]
        self.assertEqual(plans, actual)
        self.assertEqual(len(actual), 62)
        print('C preview matches independent SDO peer: 62/62 complete nominal request frames')

    def test_bounds_refused_before_bus_or_frame(self):
        for options in [('--target', '31'), ('--soft-max', '-40'), ('--max-displacement', '0'),
                        ('--target', '2147483648'), ('--velocity', '4294967296'), ('--acceleration', '0'),
                        ('--deceleration', '-1'), ('--max-displacement', '34'), ('--target', '0'),
                        ('--move-timeout', 'nan')]:
            with self.subTest(options=options):
                self.assertNotEqual(self.invoke(*options, apply=False), 0)
                self.assertEqual(self.peer.sent, [])
                tool.connect_bus.assert_not_called()
        print('C invalid parameter/soft travel/small displacement checks: zero real frames')

    def test_device_limits_and_calibration_invalid_abort_before_enable(self):
        for key, value in [((0x607f, 0), 99), ((0x6080, 0), 0), ((0x60c5, 0), 99),
                           ((0x60c6, 0), 99), ((0x608f, 2), 0)]:
            with self.subTest(key=key):
                original = self.peer.values[key]
                self.peer.values[key] = value.to_bytes(4, 'little')
                self.peer.sent.clear(); self.peer.events.clear()
                self.assertNotEqual(self.invoke(), 0)
                self.assertNotIn(15, self.peer.controls())
                self.assert_disabled()
                self.peer.values[key] = original

    def test_success_mode_disabled_first_readbacks_and_final_log(self):
        self.peer.sw = 0x27
        self.peer.values[0x6060, 0] = self.peer.values[0x6061, 0] = b'\x09'
        self.assertEqual(self.invoke(), 0, self.output)
        writes = [(k, v) for action, k, v in self.peer.events if action == 'write']
        self.assertEqual(writes[:2], [((0x6040, 0), b'\0\0'), ((0x6060, 0), b'\x01')])
        self.assertEqual(self.peer.values[0x6082, 0], b'\0'*4)
        for i, event in enumerate(self.peer.events):
            if event[0] == 'write' and event[1] in [(0x6081, 0), (0x6082, 0), (0x6083, 0), (0x6084, 0), (0x607a, 0)]:
                self.assertEqual(self.peer.events[i+1], ('read', event[1], event[2]))
        self.assert_disabled()
        events = [r['event'] for r in self.records()]
        self.assertIn('before', events); self.assertIn('target_reached', events); self.assertIn('after_cleanup', events)
        self.assertTrue(self.records()[-1]['completed'])

    def test_stale_target_reached_and_missing_ack_never_succeed(self):
        for flag in ('stale', 'no_ack'):
            with self.subTest(flag=flag):
                setattr(self.peer, flag, True)
                self.assertNotEqual(self.invoke(), 0)
                self.assert_disabled()
                self.assertFalse(self.records()[-1]['completed'])
                setattr(self.peer, flag, False)
                self.peer.triggered = False

    def test_stale_ack_must_clear_before_trigger(self):
        self.peer.stale_ack = True
        self.assertNotEqual(self.invoke(), 0)
        self.assertNotIn(63, self.peer.controls())
        self.assert_disabled()

    def test_fast_completion_with_fresh_ack_does_not_need_sampled_incomplete_bit(self):
        self.peer.fast_completion = True
        self.assertEqual(self.invoke(), 0, self.output)
        self.assert_disabled()
        movement = [r for r in self.records() if r['event'] in ('ready_for_setpoint', 'setpoint_ack', 'target_completion')]
        self.assertEqual([r['statusword'] for r in movement], [0x427, 0x1427, 0x427])
        self.assertTrue(self.records()[-1]['completed'])
        print('C fast move: fresh ACK 0->1->0 + target actual position; all sampled target-reached bits=1 accepted')

    def test_live_position_overrun_aborts_and_logs(self):
        self.peer.overrun = True
        self.assertNotEqual(self.invoke(), 0)
        self.assert_disabled()
        self.assertTrue(any(r.get('actual_position') == 31 for r in self.records()))
        self.assertTrue(any(r['event'] == 'exception' for r in self.records()))

    def test_timeout_during_motion_still_confirms_disabled(self):
        self.peer.freeze_move = True
        self.assertNotEqual(self.invoke('--state-timeout', '.05'), 0)
        self.assert_disabled()
        self.assertIn('超时', self.output)

    def test_readback_mismatch_disables_without_enabling(self):
        self.peer.ignored = (0x6083, 0)
        self.assertNotEqual(self.invoke(), 0)
        self.assert_disabled()
        self.assertNotIn(15, self.peer.controls())
        self.assertIn('写读回不一致', self.output)

    def test_download_abort_still_can_disable(self):
        self.peer.abort = (0x6083, 0)
        self.assertNotEqual(self.invoke(), 0)
        self.assert_disabled()

    def test_ctrl_c_between_transactions_disables(self):
        with mock.patch.object(tool.MotionSession, 'pause', side_effect=KeyboardInterrupt):
            self.peer.no_ack = True
            self.assertEqual(self.invoke(), 130)
        self.assert_disabled()
        self.assertTrue(any(r.get('type') == 'KeyboardInterrupt' for r in self.records()))

    def test_ctrl_c_in_mode_setup_or_enable_reports_quarantine(self):
        for key in [(0x6060, 0), (0x6083, 0), (0x6040, 0)]:
            with self.subTest(key=key):
                self.peer.interrupt = key
                self.peer.pending.clear()
                self.peer.sent.clear()
                self.assertEqual(self.invoke(), 130)
                self.assertFalse(self.records()[-1]['disabled_confirmed'])
                self.assertTrue(self.records()[-1]['independent_estop_required'])
                self.assertIn('机械停止未知', self.output)
                self.assertIn('独立急停', self.output)

    def test_ctrl_c_in_each_enable_and_trigger_write_is_explicitly_unconfirmed(self):
        for word in (6, 7, 15, 47, 63):
            with self.subTest(word=word):
                self.peer.interrupt_word = word
                self.peer.pending.clear()
                self.peer.triggered = False
                self.assertEqual(self.invoke(), 130)
                self.assertFalse(self.records()[-1]['disabled_confirmed'])
                self.assertTrue(self.records()[-1]['independent_estop_required'])
                self.assertIn('独立急停', self.output)

    def test_overall_deadline_includes_setup_and_logs_incomplete_result(self):
        self.assertNotEqual(self.invoke('--move-timeout', '.012'), 0)
        self.assertNotIn(15, self.peer.controls())
        self.assertFalse(self.records()[-1]['completed'])
        self.assertTrue(any(r['event'] == 'exception' for r in self.records()))
        if not self.records()[-1]['disabled_confirmed']:
            self.assertIn('独立急停', self.output)

    def test_after_position_read_failure_is_incomplete_even_if_disabled(self):
        self.peer.post_read_failure = True
        self.assertNotEqual(self.invoke(), 0)
        self.assertTrue(self.records()[-1]['disabled_confirmed'])
        self.assertFalse(self.records()[-1]['completed'])
        self.assertTrue(any(r['event'] == 'after_cleanup_unavailable' for r in self.records()))

    def test_lost_enable_ack_quarantine_never_claims_stopped(self):
        self.peer.lost_ack = 15
        self.assertNotEqual(self.invoke(), 0)
        self.assertEqual(self.peer.controls()[-1], 15)
        self.assertEqual(self.peer.sw, 0x427)
        self.assertFalse(self.records()[-1]['disabled_confirmed'])
        self.assertTrue(self.records()[-1]['independent_estop_required'])
        self.assertIn('quarantined', self.output)
        self.assertIn('独立急停', self.output)
        self.assertTrue(any(r['event'] == 'after_cleanup_unavailable' for r in self.records()))
        print('C lost enable ACK: SDO quarantined; stop and disable attempted, unconfirmed; independent E-stop required')

    def test_disable_status_failure_not_success(self):
        self.peer.freeze_disable = True
        self.assertNotEqual(self.invoke(), 0)
        self.assertFalse(self.records()[-1]['disabled_confirmed'])
        self.assertIn('独立急停', self.output)

    def test_od_gate_unchanged_no_bypass_zero_frames(self):
        self.assertEqual(self.invoke(bypass=False), 3)
        self.assertEqual(self.peer.sent, [])
        tool.connect_bus.assert_not_called()

    def test_no_implicit_calibration_and_no_live_offline_mix(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertNotEqual(tool.main(['--calibrate', '--node', '1']), 0)
            self.assertNotEqual(tool.main(['--calibrate', '--node', '1', '--apply', '--offline-calibration', '36000', '1', '3', '1']), 0)
        self.assertEqual(self.peer.sent, [])

    def test_calibration_motor_output_angles_and_readonly_four_uploads(self):
        cal = position.calibration([36000, 1, 3, 1])
        self.assertEqual(cal['motor_degrees_per_user_unit'], '3/100')
        self.assertEqual(cal['output_degrees_per_user_unit'], '1/100')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(tool.main(['--calibrate', '--node', '1', '--apply']), 0)
        self.assertEqual(len(self.peer.sent), 4)
        self.assertTrue(all(frame.data[0] == 0x40 for frame in self.peer.sent))
        self.assertTrue(self.peer.closed)


if __name__ == '__main__':
    unittest.main()
