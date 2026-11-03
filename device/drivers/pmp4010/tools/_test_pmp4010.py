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


class MotionClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class MotionBus:
    """Independent expedited-SDO motor fixture; real SdoClient owns wire handling.

    Literal request/response bytes deliberately avoid reproducing the client's
    codecs. State changes occur only after accepted controlword downloads.
    """
    def __init__(self, clock, initial=0x40):
        self.clock = clock
        self.sw = initial
        self.objects = {(0x2002, 1): b'\x00\x00', (0x6060, 0): b'\x01',
                        (0x6061, 0): b'\x01', (0x6098, 0): b'\x11'}
        for key, value in (((0x608f, 1), 131072), ((0x608f, 2), 1), ((0x6091, 1), 1),
                           ((0x6091, 2), 1), ((0x607f, 0), 10000000), ((0x6080, 0), 3000),
                           ((0x60ff, 0), 6553600), ((0x60b1, 0), 0)):
            self.objects[key] = value.to_bytes(4, 'little')
        self.sent = []
        self.closed = False
        self.pending = []
        self.statuses = []
        self.selectors = []
        self.homing = [0x27, 0x1427]
        self.freeze = set()
        self.abort = None
        self.silence = None
        self.interrupt = None
        self.mode_echo = True
        self.delay = 0.0
        self.timeouts = []
        self.idle_homing_status = 0x27
        self.status_flags = 0

    def send(self, frame):
        self.sent.append(frame)
        assert frame.arbitration_id == 0x601
        raw = frame.data
        key = (raw[1] | raw[2] << 8, raw[3])
        cs = raw[0]
        if self.silence == (cs, key):
            return
        header = bytes(raw[1:4])
        if self.abort == (cs, key):
            self.pending.append(canopen.Frame(0x581, b'\x80' + header + b'\x00\x00\x02\x06'))
            return
        if cs == 0x40:
            if key == (0x2002, 1) and self.selectors:
                value = self.selectors.pop(0).to_bytes(2, 'little')
            elif key == (0x6041, 0):
                if self.statuses:
                    self.sw = self.statuses.pop(0)
                value = (self.sw | self.status_flags).to_bytes(2, 'little')
            else:
                value = self.objects[key]
            command = {1: 0x4F, 2: 0x4B, 4: 0x43}[len(value)]
            reply = bytes([command]) + header + value.ljust(4, b'\x00')
        else:
            size = {0x2B: 2, 0x2F: 1, 0x23: 4}[cs]
            value = bytes(raw[4:4 + size])
            if key == (0x6040, 0):
                word = int.from_bytes(value, 'little')
                if word not in self.freeze:
                    self.sw = {0: 0x40, 0x80: 0x40, 6: 0x21, 7: 0x23,
                               0xF: 0x27, 2: 0x07, 0x1F: 0x27, 0x3F: 0x27, 0x7F: 0x27}[word]
                    if word == 0xF and self.objects[(0x6061, 0)] == b'\x06':
                        self.sw = self.idle_homing_status
                    if word == 0x1F:
                        self.statuses = list(self.homing)
                if word == self.interrupt:
                    raise KeyboardInterrupt()
            self.objects[key] = value
            if key == (0x6060, 0) and self.mode_echo:
                self.objects[(0x6061, 0)] = value
            reply = b'\x60' + header + b'\x00' * 4
        self.pending.append(canopen.Frame(0x581, reply))

    def recv(self, timeout):
        self.timeouts.append(timeout)
        self.clock.now += self.delay
        return self.pending.pop(0) if self.pending else None

    def close(self):
        self.closed = True

    def controls(self):
        return [int.from_bytes(frame.data[4:6], 'little') for frame in self.sent
                if frame.data[0] == 0x2B and frame.data[1:4] == b'\x40\x60\x00']

    def downloads(self):
        return [frame for frame in self.sent if frame.data[0] & 0xE0 == 0x20]


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
            for action in ('enable', 'disable', 'stop', 'home'):
                self.assertEqual(self.invoke('--motion', action, '--node', '1'), 3)

    def test_od_gaps_bypass_never_opens_nmt(self):
        """旁路只对 --motion 开；--nmt 传 args=None，因此即使带上参数也不放行。

        NMT Start 会让总线上**所有**节点同时进入可运动状态，风险面比
        单节点位置使能大一个量级。这条测试钉的就是"参数存在但不生效"——
        如果将来有人顺手把 args 传进 cmd_nmt，这里会红。
        """
        with mock.patch.object(tool, '_std_open_active', side_effect=AssertionError('bus opened')):
            self.assertEqual(
                self.invoke('--nmt', '--node', '1',
                            '--od-gaps-bypass', '试图放行 NMT', '--i-know-rt-is-running'), 3)
        self.assertIn('项对象字典未核对', self.stdout.getvalue())
        self.assertNotIn('已放行 OD 闸门', self.stdout.getvalue())

    def test_od_gaps_bypass_opens_motion_and_reports_what_was_waived(self):
        """带理由时 --motion 放行，且必须把放行条目与理由打在屏上。

        用真实的 CanBusError 作哨兵：它证明流程**已经越过闸门**、
        走到 connect_bus 才停（而不是被 OD 闸门挡下）。
        """
        def sentinel(iface):
            self.assertEqual(iface, 'testcan')
            raise tool._canbus.CanBusError('哨兵：已到达发帧阶段')

        with mock.patch.object(tool, 'service_active', return_value=False), \
                mock.patch.object(tool, '_std_open_active', side_effect=sentinel):
            code = self.invoke('--motion', 'stop', '--node', '1',
                               '--od-gaps-bypass', '整机调试，单节点位置验收')
        self.assertEqual(code, 3)
        text = self.stdout.getvalue()
        self.assertIn('已放行 OD 闸门', text)
        self.assertIn('整机调试，单节点位置验收', text)
        self.assertIn('没有**提高任何条目的可信度', text)
        self.assertIn('哨兵：已到达发帧阶段', text)
        # 放行后不应再出现"拒绝执行"的措辞——那说明闸门其实没开。
        self.assertNotIn('拒绝执行', text)

    def test_od_gaps_bypass_requires_a_reason_not_a_bare_flag(self):
        """裸开关不算数：缺理由时 argparse 直接报错，不进入主流程。

        理由不是形式主义——它会被 journal 记下来，供后来的人判断
        "当时是谁、基于什么放行的"。没有理由的放行等于没有留痕。
        """
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stderr(io.StringIO()):
                self.invoke('--motion', 'stop', '--node', '1', '--od-gaps-bypass')
        self.assertEqual(caught.exception.code, 2)

    def test_od_gaps_bypass_does_not_touch_od_or_generated_files(self):
        """放行不得留下任何持久痕迹：od.json 字节不变。

        旁路的语义是"这一次放行"，不是"把这几条洗成 verified"。
        如果它悄悄改了 status，整条闸门就失去了意义。
        """
        od_path = Path(tool.od_json_path())
        before = od_path.read_bytes()

        def sentinel(iface):
            raise tool._canbus.CanBusError('哨兵')

        with mock.patch.object(tool, 'service_active', return_value=False), \
                mock.patch.object(tool, '_std_open_active', side_effect=sentinel):
            self.invoke('--motion', 'stop', '--node', '1', '--od-gaps-bypass', '测试不落盘')
        self.assertEqual(od_path.read_bytes(), before)

    def test_od_gaps_bypass_rejects_whitespace_before_transport_even_if_od_verified(self):
        for verified in (False, True):
            for reason in ('', ' ', '\t\r\n', '\u3000'):
                with self.subTest(verified=verified, reason=reason), \
                        mock.patch.object(tool, 'od_status', return_value=(verified, 0, [], [])), \
                        mock.patch.object(tool, '_std_open_active') as opening:
                    with self.assertRaises(SystemExit) as caught:
                        self.invoke('--motion', 'stop', '--node', '1', '--od-gaps-bypass', reason)
                    self.assertEqual(caught.exception.code, 2)
                    opening.assert_not_called()

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
        self.assertTrue(all(tui._display_width(row) == 80 for row in frame))
        self.assertNotIn('F5 refresh', app.hints())
        self.assertTrue(tui.render(None, frame))

    def test_no_tui_panel_has_no_transport_access(self):
        with mock.patch.object(tool, 'read_iface_actual', side_effect=self.actual), \
                mock.patch.object(tool, 'service_active', return_value=None):
            self.assertEqual(self.invoke('--no-tui'), 0)
        self.assertIn('运动已阻止', self.stdout.getvalue())

    def test_bad_options_fail_before_any_transport_access(self):
        for args in [('--bus', 'can-1'), ('--timeout', 'nan'), ('--timeout', '-1'),
                     ('--listen-ms', '0'), ('--count', '-1'), ('--verify',),
                     ('--node-range', '4-1'), ('--node', '128')]:
            with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                self.invoke(*args)
            self.assertEqual(raised.exception.code, 2)


class MotionTests(unittest.TestCase):
    invoke = ToolContractTests.invoke
    actual = ToolContractTests.actual

    def setUp(self):
        ToolContractTests.setUp(self)
        self.clock = MotionClock()
        self.bus = MotionBus(self.clock)
        for patch in (mock.patch.object(tool, 'time', self.clock),
                      mock.patch.object(tool, 'od_status', return_value=(True, 0, [], [])),
                      mock.patch.object(tool, 'service_active', return_value=False),
                      mock.patch.object(tool, '_std_open_active', return_value=self.bus)):
            patch.start()
            self.addCleanup(patch.stop)

    def motion(self, action, *args):
        return self.invoke('--motion', action, '--node', '1',
                           '--state-timeout', '0.08', '--home-timeout', '0.1', *args)

    def test_motion_enable_confirms_every_transition_and_ignores_other_bits(self):
        self.bus.status_flags = 0x8180
        self.assertEqual(self.motion('enable'), 0)
        self.assertEqual(self.bus.controls(), [6, 7, 15])
        self.assertEqual(self.bus.sw & 0x6F, 0x27)
        self.assertTrue(self.bus.closed)
        self.assertEqual(self.motion('enable'), 0)
        self.assertEqual(self.bus.controls(), [6, 7, 15])

    def test_motion_enable_rejects_mode_specific_and_unknown_modes_before_writes(self):
        for mode in (0, 2, 5, 7, 11, 255):
            for sw in (0x40, 0x08, 0x27):
                with self.subTest(mode=mode, sw=sw):
                    self.bus.sw = sw
                    self.bus.objects[(0x6061, 0)] = bytes([mode])
                    self.assertEqual(self.motion('enable'), 3)
                    self.assertEqual(self.bus.controls(), [])
        self.assertIn('不在本工具允许使能的集合', self.stdout.getvalue())

    def test_motion_enable_supports_only_documented_0f_modes(self):
        # Velocity modes have a separate explicit zero-target path below.
        for mode in (1, 4, 6, 8, 10):
            with self.subTest(mode=mode):
                self.bus.sent.clear()
                self.bus.sw = 0x40
                self.bus.objects[(0x6061, 0)] = bytes([mode])
                self.assertEqual(self.motion('enable'), 0)
                self.assertEqual(self.bus.controls(), [6, 7, 15])

    def test_speed_mode_cli_explicit_zero_precedes_enable_and_replaces_residual(self):
        for mode in (3, 9):
            self.bus.sent.clear()
            self.bus.sw = 0x40
            self.bus.objects[(0x6061, 0)] = bytes([mode])
            self.bus.objects[(0x60ff, 0)] = (6553600).to_bytes(4, 'little')
            self.assertEqual(self.motion('enable'), 0)
            self.assertEqual(self.bus.controls(), [6, 7, 15])
            downloads = self.bus.downloads()
            self.assertEqual(downloads[0].data[:4], b'\x23\xff\x60\x00')
            self.assertEqual(downloads[0].data[4:], bytes(4))
            self.assertEqual(self.bus.objects[0x60ff, 0], bytes(4))

    def test_motion_enable_waits_for_initial_not_ready_without_premature_write(self):
        self.bus.statuses = [0, 0, 0x40]
        self.assertEqual(self.motion('enable'), 0)
        self.assertEqual(self.bus.controls(), [6, 7, 15])
        self.assertGreaterEqual(self.clock.now, 0.02)
        self.bus.sent.clear()
        self.bus.statuses = [0] * 20
        self.assertEqual(self.motion('enable'), 3)
        self.assertEqual(self.bus.controls(), [])

    def test_motion_fault_reset_waits_for_reaction_and_clears_reset_edge(self):
        self.bus.sw = 0xF
        self.bus.statuses = [0xF, 0x2F, 0x8]
        self.assertEqual(self.motion('enable'), 0)
        self.assertEqual(self.bus.controls(), [0, 0x80, 0, 6, 7, 15])

    def test_motion_enable_transition_timeout_never_sends_later_enable(self):
        self.bus.freeze.add(7)
        self.assertEqual(self.motion('enable'), 3)
        self.assertEqual(self.bus.controls(), [6, 7, 2, 0])
        self.assertIn('Switched on', self.stdout.getvalue())
        self.assertIn('超时', self.stdout.getvalue())
        self.assertTrue(self.bus.closed)

    def test_motion_fault_during_transition_fails_and_attempts_stop(self):
        self.bus.statuses = [0x40, 0x8]
        self.assertEqual(self.motion('enable'), 3)
        self.assertEqual(self.bus.controls(), [6, 2, 0])
        self.assertIn('故障状态字', self.stdout.getvalue())

    def test_motion_disable_and_stop_require_status_confirmation(self):
        for action, expected in [('disable', 0x40), ('stop', 7)]:
            with self.subTest(action=action):
                self.bus.sw = 0x27
                self.assertEqual(self.motion(action), 0)
                self.assertEqual(self.bus.sw, expected)
        for action, word in [('disable', 0), ('stop', 2)]:
            with self.subTest(action=action):
                self.bus.sw = 0x27
                self.bus.freeze = {word}
                self.assertEqual(self.motion(action), 3)

    def test_motion_home_selects_mode_while_disabled_and_confirms_completion(self):
        self.bus.sw = 0x27
        self.assertEqual(self.motion('home'), 0)
        self.assertEqual(self.bus.controls(), [0, 6, 7, 15, 15, 31, 15])
        writes = [frame for frame in self.bus.sent if frame.data[0] in (0x2B, 0x2F)]
        self.assertEqual(writes[0].data[1:6], b'\x40\x60\x00\x00\x00')
        self.assertEqual(self.bus.objects[(0x6098, 0)], b'\x11')
        self.assertEqual(self.bus.objects[(0x6061, 0)], b'\x06')
        self.assertTrue(self.bus.closed)

    def test_motion_home_stale_completion_must_clear_before_success(self):
        self.bus.idle_homing_status = 0x1427
        self.bus.homing = [0x1427, 0x1427, 0x27, 0x1427]
        self.assertEqual(self.motion('home'), 0)
        self.assertEqual(self.bus.statuses, [])
        self.bus.homing = [0x1427] * 20
        self.assertEqual(self.motion('home'), 3)
        self.assertEqual(self.bus.controls()[-2:], [2, 0])

    def test_motion_home_requires_both_target_reached_and_homing_attained(self):
        self.bus.homing = [0x0427, 0x1027, 0x1427]
        self.assertEqual(self.motion('home'), 0)
        self.assertEqual(self.bus.statuses, [])
        self.assertEqual(self.bus.controls()[-2:], [31, 15])

    def test_motion_home_current_position_method_is_explicit(self):
        self.bus.homing = [0x1427]
        self.assertEqual(self.motion('home', '--homing-method', '35'), 0)
        self.assertEqual(self.bus.objects[(0x6098, 0)], b'\x23')

    def test_motion_home_errors_timeout_and_lost_enable_all_stop(self):
        for homing in ([0x3427], [0x8], [0x1423], [0x27] * 20):
            with self.subTest(homing=homing):
                self.bus.sent.clear()
                self.bus.statuses = []
                self.bus.sw = 0x40
                self.bus.homing = homing
                self.assertEqual(self.motion('home'), 3)
                self.assertEqual(self.bus.controls()[-2:], [2, 0])
                self.assertTrue(self.bus.closed)
        self.assertIn('回零错误', self.stdout.getvalue())
        self.assertNotIn('[OK] 回零完成', self.stdout.getvalue())

    def test_motion_home_mode_echo_failure_does_not_enable(self):
        self.bus.mode_echo = False
        self.assertEqual(self.motion('home'), 3)
        self.assertEqual(self.bus.controls(), [0, 2, 0])
        self.assertNotIn(15, self.bus.controls())

    def test_motion_home_read_failures_stop_or_report_quarantine_and_close(self):
        for failure in ('abort', 'silence', 'length'):
            with self.subTest(failure=failure):
                self.bus.sent.clear()
                self.bus.abort = self.bus.silence = None
                self.bus.statuses = []
                if failure == 'length':
                    self.bus.mode_echo = False
                    self.bus.objects[(0x6061, 0)] = b'\x06\x00'
                else:
                    setattr(self.bus, failure, (0x40, (0x6061, 0)))
                self.assertEqual(self.motion('home'), 3)
                if failure == 'silence':
                    # Timeout quarantines this SDO session: cleanup must not
                    # transmit into the unresolved response stream.
                    self.assertEqual(self.bus.controls(), [0])
                    self.assertEqual(len(self.bus.sent), 7)  # Includes disabled-only mode-write status guard.
                    self.assertEqual(self.bus.sent[-1].data[:4], b'\x40\x61\x60\x00')
                    self.assertIn('Quick stop 未确认', self.stdout.getvalue())
                    self.assertIn('机械停止未知，立即使用独立急停', self.stdout.getvalue())
                else:
                    self.assertEqual(self.bus.controls(), [0, 2, 0])
                self.assertTrue(self.bus.closed)

    def test_motion_home_interruption_after_start_attempts_stop_and_closes(self):
        self.bus.interrupt = 31
        self.assertEqual(self.motion('home'), 130)
        self.assertEqual(self.bus.controls()[-1], 31)
        self.assertIn('失能未确认', self.stdout.getvalue())
        self.assertIn('独立急停', self.stdout.getvalue())
        self.assertTrue(self.bus.closed)

    def test_motion_home_stop_failure_is_reported_without_success(self):
        self.bus.homing = [0x3427]
        self.bus.freeze.add(2)
        self.assertEqual(self.motion('home'), 3)
        self.assertIn('Quick stop 未确认', self.stdout.getvalue())
        self.assertNotIn('[OK] 回零完成', self.stdout.getvalue())

    def test_motion_non_cia402_mode_refuses_without_writes(self):
        self.bus.objects[(0x2002, 1)] = b'\x04\x00'
        for action in ('enable', 'disable', 'stop', 'home'):
            with self.subTest(action=action):
                self.bus.sent.clear()
                self.assertEqual(self.motion(action), 3)
                self.assertEqual(len(self.bus.sent), 1)
                self.assertEqual(self.bus.sent[0].data[:4], b'\x40\x02\x20\x01')
                self.assertEqual(len(self.bus.downloads()), 0)
                self.assertTrue(self.bus.closed)

    def test_motion_selector_read_failure_refuses_all_actions_without_downloads(self):
        for failure in ('abort', 'silence', 'length'):
            for action in ('enable', 'disable', 'stop', 'home'):
                with self.subTest(failure=failure, action=action):
                    self.bus.sent.clear()
                    self.bus.pending.clear()
                    self.bus.abort = self.bus.silence = None
                    self.bus.objects[(0x2002, 1)] = b'\x00\x00'
                    if failure == 'length':
                        self.bus.objects[(0x2002, 1)] = b'\x00'
                    else:
                        setattr(self.bus, failure, (0x40, (0x2002, 1)))
                    self.assertEqual(self.motion(action), 3)
                    self.assertEqual(len(self.bus.sent), 1)
                    self.assertEqual(len(self.bus.downloads()), 0)
                    self.assertTrue(self.bus.closed)

    def test_every_controlword_rechecks_selector_before_download(self):
        for word in (0, 2, 6, 7, 15, 31, 128):
            for failure in ('mode', 'abort', 'silence', 'length'):
                with self.subTest(word=word, failure=failure):
                    self.bus.sent.clear()
                    self.bus.pending.clear()
                    self.bus.abort = self.bus.silence = None
                    self.bus.objects[(0x2002, 1)] = b'\x00\x00'
                    client = canopen.SdoClient(self.bus, 1, timeout=0.001)
                    session = tool.MotionSession(client, tool.Out(), 0.08)
                    session.require_cia402()
                    if failure == 'mode':
                        self.bus.objects[(0x2002, 1)] = b'\x04\x00'
                    elif failure == 'length':
                        self.bus.objects[(0x2002, 1)] = b'\x00'
                    else:
                        setattr(self.bus, failure, (0x40, (0x2002, 1)))
                    with self.assertRaises(tool.MotionError):
                        session.control(word, session.deadline())
                    self.assertEqual(len(self.bus.sent), 2)
                    self.assertEqual(len(self.bus.downloads()), 0)
                    self.assertFalse(session.needs_stop)

    def test_motion_selector_change_blocks_later_control_and_cleanup_downloads(self):
        # Initial validation and Shutdown succeed; Switch on and cleanup see NiMotion.
        self.bus.selectors = [0, 0, 4, 4, 4]
        self.assertEqual(self.motion('enable'), 3)
        self.assertEqual(self.bus.controls(), [6])
        self.assertEqual(len(self.bus.downloads()), 1)
        selector_reads = [frame for frame in self.bus.sent
                          if frame.data[:4] == b'\x40\x02\x20\x01']
        self.assertEqual(len(selector_reads), 5)
        self.assertIn('Quick stop 未确认', self.stdout.getvalue())
        self.assertTrue(self.bus.closed)

    def test_motion_absolute_deadline_bounds_each_sdo_and_total_wait(self):
        self.bus.delay = 0.03
        self.bus.freeze.add(6)
        self.assertEqual(self.motion('enable', '--timeout', '5'), 3)
        self.assertLess(self.clock.now, 0.4)  # includes independent bounded stop attempt
        self.assertLessEqual(max(self.bus.timeouts), 0.08)

    def test_motion_missing_node_and_broadcast_never_open_bus(self):
        with mock.patch.object(tool, '_std_open_active') as opening:
            self.assertEqual(self.invoke('--motion', 'enable'), 2)
            self.assertEqual(self.invoke('--motion', 'enable', '--node', '0'), 2)
            opening.assert_not_called()

    def test_motion_all_gates_fail_before_transport_and_preserve_order(self):
        for action in ('enable', 'disable', 'stop', 'home'):
            with self.subTest(action=action), \
                    mock.patch.object(tool, 'od_status', return_value=(False, 6, ['x'], [])), \
                    mock.patch.object(tool, 'require_std_layer') as standard, \
                    mock.patch.object(tool, 'require_rt_ack') as rt, \
                    mock.patch.object(tool, '_std_open_active') as opening:
                self.assertEqual(self.motion(action, '--i-know-rt-is-running'), 3)
                standard.assert_not_called()
                rt.assert_not_called()
                opening.assert_not_called()
                self.assertEqual(len(self.bus.sent), 0)
        with mock.patch.object(tool, 'require_std_layer', return_value=False), \
                mock.patch.object(tool, 'require_rt_ack') as rt, \
                mock.patch.object(tool, '_std_open_active') as opening:
            self.assertEqual(self.motion('enable'), 5)
            rt.assert_not_called()
            opening.assert_not_called()
        for state in (True, None):
            with self.subTest(state=state), mock.patch.object(tool, 'service_active', return_value=state), \
                    mock.patch.object(tool, '_std_open_active') as opening:
                self.assertEqual(self.motion('enable'), 4)
                opening.assert_not_called()
        with mock.patch.object(tool, 'service_active', return_value=True):
            self.assertEqual(self.motion('enable', '--i-know-rt-is-running'), 0)

    def test_nmt_gates_use_od_standard_rt_order_with_zero_frames_when_closed(self):
        with mock.patch.object(tool, 'od_status', return_value=(False, 6, ['x'], [])), \
                mock.patch.object(tool, 'require_std_layer') as standard, \
                mock.patch.object(tool, 'require_rt_ack') as rt:
            self.assertEqual(self.invoke('--nmt', '--i-know-rt-is-running'), 3)
            standard.assert_not_called()
            rt.assert_not_called()
            self.assertEqual(len(self.bus.sent), 0)
        with mock.patch.object(tool, 'require_std_layer', return_value=False), \
                mock.patch.object(tool, 'require_rt_ack') as rt:
            self.assertEqual(self.invoke('--nmt'), 5)
            rt.assert_not_called()
            self.assertEqual(len(self.bus.sent), 0)
        with mock.patch.object(tool, 'service_active', return_value=True):
            self.assertEqual(self.invoke('--nmt'), 4)
            self.assertEqual(len(self.bus.sent), 0)

    def test_motion_invalid_timeouts_and_homing_method_fail_before_transport(self):
        for args in (('--state-timeout', 'nan'), ('--state-timeout', '-1'),
                     ('--home-timeout', 'inf'), ('--home-timeout', '0'),
                     ('--homing-method', '1')):
            with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                self.motion('home', *args)
            self.assertEqual(raised.exception.code, 2)
        self.assertEqual(self.bus.sent, [])


if __name__ == '__main__':
    unittest.main()
