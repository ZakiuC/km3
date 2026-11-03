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


class CLISafetyTests(unittest.TestCase):
    """Safety seams retained after the obsolete five-view form was removed."""
    def setUp(self):
        self.args = tool.build_parser().parse_args(['--position', '--node', '1', '--target', '10',
            '--velocity', '100', '--acceleration', '100', '--deceleration', '100',
            '--soft-min', '-30', '--soft-max', '30', '--max-displacement', '30',
            '--motion-log', '/tmp/unused-bench-drift.jsonl', '--od-gaps-bypass', 'local mock'])
        self.cfg = {'bus': {'can': [{'logical': tool.LOGICAL_BUS, 'iface': 'mock-iface', 'bitrate': 500000}]},
                    'network': {'sync_period_us': 4000}}
        self.out = ui.JobOutput(queue.Queue(), threading.Event())

    def test_target_config_drift_is_refused_inside_shared_position_command(self):
        a = copy.copy(self.args); a.apply = True
        a._tui_expected_target = ('old-iface', 500000, 1)
        with mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock')), \
             mock.patch.object(tool, 'require_rt_ack', return_value=True), \
             mock.patch.object(tool, 'connect_bus', side_effect=AssertionError('opened')):
            with self.assertRaisesRegex(ValueError, 'Target changed'):
                tool.cmd_position(a, self.out)

    def test_od_drift_refused_inside_shared_position_command(self):
        a = copy.copy(self.args); a._tui_expected_od = {}
        with self.assertRaisesRegex(ValueError, 'OD changed'):
            tool.cmd_position(a, self.out)

    def test_params_plan_drift_refused_before_any_bus_open(self):
        a = copy.copy(self.args); a.params = 'interpolation'; a.node = 1
        a._tui_expected_writes = [('bogus', 1)]
        with mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock')), \
             mock.patch.object(tool, 'connect_bus', side_effect=AssertionError('opened')):
            with self.assertRaisesRegex(ValueError, 'parameter plan changed'):
                tool.cmd_params(a, self.out)

    def test_shared_read_commands_emit_quarantine_and_close_mock_bus(self):
        actual = dict(state='UP', bitrate=500000, can_state='ERROR-ACTIVE')
        for action in ('calibrate', 'backup'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp:
                a = copy.copy(self.args); a.timeout = .001
                a.calibrate = action == 'calibrate'; a.position = False
                a.apply = a.calibrate
                a.params = 'backup'; a.backup = str(Path(tmp) / 'unused-backup.json')
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
