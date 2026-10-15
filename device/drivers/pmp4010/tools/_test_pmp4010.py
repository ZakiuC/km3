"""PMP4010 tool contract regressions. Strictly offline: no SocketCAN or subprocesses.

Run: python3 -m unittest discover -s device/drivers/pmp4010/tools -p '_test_*.py' -v
"""
import ast
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

TOOL_PATH = Path(__file__).with_name('pmp4010.py')
SPEC = importlib.util.spec_from_file_location('pmp4010_tool', TOOL_PATH)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)
assert tool.std_layer_available()
import canopen
import mockbus
import tui


class ToolContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='km3-pmp4010-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / 'device.json'
        self.env = self.root / 'can.env'
        self.mapping = {'bus': {'can': [
            {'logical': 'can-2', 'iface': 'testcan', 'bitrate': 500000}
        ]}}
        self.config.write_text(json.dumps(self.mapping))
        for name, value in [('DEVICE_JSON', str(self.config)),
                            ('DEVICE_JSON_FALLBACKS', []), ('CAN_ENV', str(self.env))]:
            patch = mock.patch.object(tool, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        # Any unexpected attempt to touch host transports/commands fails this suite.
        self.subprocess = mock.patch.object(tool.subprocess, 'run', side_effect=AssertionError('host command'))
        self.subprocess.start()
        self.addCleanup(self.subprocess.stop)
        self.socket = mock.patch.object(tool._canbus.socket, 'socket', side_effect=AssertionError('live socket'))
        self.socket.start()
        self.addCleanup(self.socket.stop)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.redirect_stdout = contextlib.redirect_stdout(self.stdout)
        self.redirect_stderr = contextlib.redirect_stderr(self.stderr)
        self.redirect_stdout.__enter__()
        self.redirect_stderr.__enter__()
        self.addCleanup(self.redirect_stdout.__exit__, None, None, None)
        self.addCleanup(self.redirect_stderr.__exit__, None, None, None)

    def invoke(self, *args):
        return tool.main(list(args))

    def actual(self, iface):
        self.assertEqual(iface, 'testcan')
        return {'state': 'UP', 'can_state': 'ERROR-ACTIVE', 'bitrate': 500000,
                'flags': 'UP,LOWER_UP', 'error': None}

    def test_no_duplicate_top_level_functions(self):
        tree = ast.parse(TOOL_PATH.read_text())
        names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        self.assertEqual(len(names), len(set(names)))

    def test_offline_self_test_uses_no_live_transport_or_commands(self):
        self.assertEqual(self.invoke('--test', '--bus', 'can-2'), 0)
        self.assertIn('MockBus', self.stdout.getvalue())
        self.assertIn('离线自检通过', self.stdout.getvalue())

    def test_offline_self_test_requires_standard_layer(self):
        with mock.patch.object(tool, 'std_layer_available', return_value=False):
            self.assertEqual(self.invoke('--test'), 1)

    def test_missing_od_fails_self_test_and_motion_closed(self):
        with mock.patch.object(tool, 'od_json_path', return_value=str(self.root / 'missing.json')):
            self.assertEqual(self.invoke('--test'), 1)
            self.assertEqual(self.invoke('--nmt'), 3)
            self.assertEqual(self.invoke('--motion', 'stop'), 3)

    def test_od_dump_accepts_hexadecimal_string_indices(self):
        self.assertEqual(self.invoke('--od-dump'), 0)
        self.assertIn('0x6040', self.stdout.getvalue())
        self.assertIn('项对象字典未核对', self.stdout.getvalue())

    def test_current_nmt_and_motion_cannot_open_bus(self):
        with mock.patch.object(tool, '_std_open_active', side_effect=AssertionError('bus opened')):
            self.assertEqual(self.invoke('--nmt', '--i-know-rt-is-running'), 3)
            self.assertEqual(self.invoke('--motion', 'enable'), 3)

    def test_empty_or_malformed_od_never_opens_motion_gate(self):
        path = self.root / 'od.json'
        cases = [{}, {'model': 'PMP4010', 'entries': []},
                 {'model': 'PMP4010', 'entries': [1]},
                 {'model': 'PMP4010', 'entries': [{'key': 'x', 'status': 'verified', 'cite': None}]},
                 {'model': 'PMP4010', 'entries': [{'key': 'x', 'status': 'unverified', 'cite': 'manual'}]}]
        with mock.patch.object(tool, 'od_json_path', return_value=str(path)):
            for data in cases:
                with self.subTest(data=data):
                    path.write_text(json.dumps(data))
                    with self.assertRaises(tool.ConfigError):
                        tool.od_status()

    def test_scan_uses_shared_protocol_and_reads_real_identity_subobjects(self):
        node = mockbus.MockNode(2, manufacturer_name='test vendor', software_version='v1.2.3',
                                vendor_id=123, product_code=456, serial_number=789)
        bus = mockbus.MockBus([node], loopback=False)
        with mock.patch.object(tool, 'service_active', return_value=False), \
                mock.patch.object(tool, '_std_open_active', return_value=bus):
            self.assertEqual(self.invoke('--scan', '--node-range', '1-3', '--identity', '--timeout', '0.02'), 0)
        self.assertTrue(bus.closed)
        text = self.stdout.getvalue()
        self.assertIn('test vendor', text)
        self.assertIn('vendor_id=0x0000007B', text)
        self.assertIn('product_code=0x000001C8', text)
        self.assertIn('serial_number=0x00000315', text)
        self.assertGreater(len(bus.sent), 5)
        for frame in bus.sent:
            self.assertTrue(0x601 <= frame.arbitration_id <= 0x603)
            self.assertIn(frame.data[0] & 0xE0, (0x40, 0x60))

    def test_scan_optional_object_abort_does_not_hide_identity(self):
        node = mockbus.MockNode(1, vendor_id=42)
        node.set_abort(0x1008, 0, 0x06020000)
        bus = mockbus.MockBus([node], loopback=False)
        with mock.patch.object(tool, 'service_active', return_value=False), \
                mock.patch.object(tool, '_std_open_active', return_value=bus):
            self.assertEqual(self.invoke('--scan', '--node-range', '1', '--identity'), 0)
        self.assertIn('读取失败', self.stdout.getvalue())
        self.assertIn('vendor_id=0x0000002A', self.stdout.getvalue())

    def test_silent_scan_is_failure_and_closes_transport(self):
        bus = mockbus.MockBus.silent()
        with mock.patch.object(tool, 'service_active', return_value=False), \
                mock.patch.object(tool, '_std_open_active', return_value=bus):
            self.assertEqual(self.invoke('--scan', '--node-range', '1'), 1)
        self.assertTrue(bus.closed)

    def test_active_unknown_and_transitioning_rt_require_ack_before_open(self):
        for state in (True, None):
            with self.subTest(state=state), mock.patch.object(tool, 'service_active', return_value=state), \
                    mock.patch.object(tool, '_std_open_active') as opening:
                self.assertEqual(self.invoke('--scan', '--node-range', '1'), 4)
                opening.assert_not_called()

    def test_ack_can_allow_read_only_scan_with_active_rt(self):
        bus = mockbus.MockBus([mockbus.MockNode(1)], loopback=False)
        with mock.patch.object(tool, 'service_active', return_value=True), \
                mock.patch.object(tool, '_std_open_active', return_value=bus):
            self.assertEqual(self.invoke('--scan', '--node-range', '1', '--i-know-rt-is-running'), 0)

    def test_missing_standard_layer_scan_refuses_without_fallback(self):
        with mock.patch.object(tool, 'service_active', return_value=False), \
                mock.patch.object(tool, 'std_layer_available', return_value=False), \
                mock.patch.object(tool, '_std_open_active') as opening:
            self.assertEqual(self.invoke('--scan', '--node-range', '1'), 5)
            opening.assert_not_called()

    def test_transport_failure_closes_bus_and_is_reported(self):
        bus = mockbus.MockBus.silent()
        bus.recv = mock.Mock(side_effect=tool._canbus.InterfaceError('receive failure'))
        with mock.patch.object(tool, 'service_active', return_value=False), \
                mock.patch.object(tool, '_std_open_active', return_value=bus):
            self.assertEqual(self.invoke('--scan', '--node-range', '1'), 3)
        self.assertTrue(bus.closed)
        self.assertIn('receive failure', self.stdout.getvalue())

    def test_init_verify_and_read_only_alias_preserve_files(self):
        before = b'CAN_BITRATE=125000\nCAN_IFACES=testcan\n'
        self.env.write_bytes(before)
        with mock.patch.object(tool, 'read_iface_actual', side_effect=self.actual), \
                mock.patch.object(tool, 'write_can_env_bitrate', side_effect=AssertionError('write')):
            for flag in ('--verify', '--read-only'):
                self.assertEqual(self.invoke('--init', flag, '--bus', 'can-2', '--no-tui'), 0)
        self.assertEqual(self.env.read_bytes(), before)

    def test_init_writes_and_reads_back_preserving_unrelated_lines(self):
        self.env.write_text('# field settings\nCAN_IFACES=testcan\n CAN_BITRATE =125000\nCAN_BITRATE=250000\n')
        with mock.patch.object(tool, 'read_iface_actual', side_effect=self.actual):
            self.assertEqual(self.invoke('--init', '--no-tui'), 0)
        text = self.env.read_text()
        self.assertIn('# field settings\nCAN_IFACES=testcan\n', text)
        self.assertEqual(text.count('CAN_BITRATE='), 1)
        self.assertEqual(tool.read_can_env()[0]['CAN_BITRATE'], '500000')
        self.assertIn('读回', self.stdout.getvalue())

    def test_new_can_env_permissions_are_restricted(self):
        tool.write_can_env_bitrate(500000)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o640)

    def test_service_active_does_not_treat_unknown_or_transitions_as_inactive(self):
        for state, expected in [('active', True), ('activating', True), ('deactivating', True),
                                ('reloading', True), ('inactive', False), ('failed', False), ('other', None)]:
            with self.subTest(state=state), mock.patch.object(tool, 'run_readonly',
                    return_value=(0, 'LoadState=loaded\nActiveState=' + state, '')):
                self.assertIs(tool.service_active(tool.RT_SERVICE), expected)
        with mock.patch.object(tool, 'run_readonly', return_value=(1, 'LoadState=loaded\nActiveState=inactive', 'error')):
            self.assertIsNone(tool.service_active(tool.RT_SERVICE))

    def test_mapping_rejects_duplicate_and_unsafe_configuration(self):
        for modify in ('duplicate', 'iface', 'bitrate', 'root'):
            cfg = copy.deepcopy(self.mapping)
            if modify == 'duplicate':
                cfg['bus']['can'] *= 2
            elif modify == 'iface':
                cfg['bus']['can'][0]['iface'] = '../bad'
            elif modify == 'bitrate':
                cfg['bus']['can'][0]['bitrate'] = '500000\nINJECT=value'
            else:
                cfg = []
            with self.subTest(case=modify), self.assertRaises(tool.ConfigError):
                tool.find_can_bus(cfg, 'can-2')

    def test_raw_only_receives_and_closes_transport(self):
        node = mockbus.MockNode(1)
        node.queue_heartbeat()
        bus = mockbus.MockBus([node], loopback=False)
        with mock.patch.object(tool, 'connect_bus', return_value=bus):
            self.assertEqual(self.invoke('--raw', '--count', '1'), 0)
        self.assertEqual(bus.sent, [])
        self.assertTrue(bus.closed)

    def test_stats_parse_real_iproute2_table_layout(self):
        raw = '''4: testcan: <NOARP,UP,LOWER_UP> mtu 16 state UNKNOWN
    can state ERROR-PASSIVE restart-ms 0
      bitrate 500000
      re-started bus-errors arbit-lost error-warn error-pass bus-off
      1          2          3          4          5          6
    RX: bytes packets errors dropped missed mcast
        10    11      12     13      14     15
    TX: bytes packets errors dropped carrier collsns
        20    21      22     23      24      25
'''
        with mock.patch.object(tool, 'run_readonly', return_value=(0, raw, '')):
            actual = tool.read_iface_actual('testcan')
            self.assertEqual(actual['state'], 'UP')
            self.assertEqual(actual['can_state'], 'ERROR-PASSIVE')
            self.assertEqual(tool.parse_ip_stats('testcan')['rx_packets'], 11)
            self.assertEqual(tool.parse_ip_stats('testcan')['tx_errors'], 22)
        counters = tool.parse_can_xstats(raw)
        self.assertEqual(counters['arbitration-lost'], 3)
        self.assertEqual(counters['error-warn'], 4)
        self.assertEqual(counters['bus-off'], 6)
        self.assertEqual(tool.parse_can_xstats('RX: errors\n0'), {})

    def test_kernel_module_absence_is_unknown(self):
        with mock.patch.object(tool.os.path, 'exists', return_value=False):
            self.assertIn('未知', tool.probe_kernel_module('testcan'))

    def test_snapshot_panel_reuses_shared_grid_and_zero_size_fallback(self):
        rows = tool.panel_rows('testcan', str(self.config), self.actual('testcan'), 500000, None)
        with mock.patch.object(tui, '_ioctl_size', return_value=(0, 0)), \
                mock.patch.dict(os.environ, {'LINES': '0', 'COLUMNS': '0'}):
            cls = tool.HwPanel(rows, rows_forced=24, cols_forced=80, box='ascii')._make_app()
            app = cls()
            frame = app.build().to_lines()
        self.assertEqual(len(frame), 24)
        self.assertTrue(all(len(row) == 80 for row in frame))
        self.assertNotIn('F5 refresh', app.hints())
        self.assertTrue(tui.render(None, frame))

    def test_no_tui_panel_has_no_transport_access(self):
        with mock.patch.object(tool, 'read_iface_actual', side_effect=self.actual), \
                mock.patch.object(tool, 'service_active', return_value=None):
            self.assertEqual(self.invoke('--no-tui'), 0)
        self.assertIn('motion blocked', self.stdout.getvalue())

    def test_bad_options_fail_before_any_transport_access(self):
        for args in [('--bus', 'can-1'), ('--timeout', 'nan'), ('--timeout', '-1'),
                     ('--listen-ms', '0'), ('--count', '-1'), ('--verify',),
                     ('--node-range', '4-1'), ('--node', '128')]:
            with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                self.invoke(*args)
            self.assertEqual(raised.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
