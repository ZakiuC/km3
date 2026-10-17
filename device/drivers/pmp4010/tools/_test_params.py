"""Parameter acceptance with independent SDO peer, never a real CAN socket."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

tool = load('parameter_cli_test', HERE / 'pmp4010.py')
params = load('parameter_module_test', HERE / 'pmp4010_params.py')
assert tool.std_layer_available()
import canopen


class Peer:
    """Literal expedited protocol peer: no production encoder on response side."""
    def __init__(self):
        self.values = {(i, 2): b'\xff' for i in list(range(0x1400, 0x1404)) + list(range(0x1800, 0x1804))}
        self.values.update({(0x60c2, 1): b'\x14', (0x60c2, 2): b'\xff',
                            (0x1010, 1): b'\x01\x00\x00\x00',
                            (0x1010, 11): b'\x01\x00\x00\x00'})
        for sub, raw in enumerate([b'\x09\x05\x00\x00', b'\x01\x00\x00\x03',
                                   b'\x03\x03\x00\x00', b'\x34\x12\x00\x00'], 1):
            self.values[0x1018, sub] = raw
        self.sent, self.pending, self.events = [], [], []
        self.closed = False
        self.ignore_write = None
        self.abort_write = None
        self.bad_save = False

    def send(self, frame):
        self.sent.append(frame)
        assert frame.arbitration_id == 0x601
        raw = frame.data
        key = (raw[1] | raw[2] << 8, raw[3])
        header = raw[1:4]
        if raw[0] == 0x40:
            value = self.values[key]
            self.events.append(('read', key, value))
            reply = bytes([{1: 0x4f, 2: 0x4b, 4: 0x43}[len(value)]]) + header + value.ljust(4, b'\x00')
        else:
            size = {0x2f: 1, 0x2b: 2, 0x23: 4}[raw[0]]
            value = raw[4:4+size]
            self.events.append(('write', key, value))
            if self.abort_write == key:
                self.pending.append(canopen.Frame(0x581, b'\x80' + header + b'\x30\x00\x09\x06'))
                return
            if key != self.ignore_write:
                if key == (0x1010, 1):
                    assert value == b'save'
                    self.values[key] = b'\x00\x00\x00\x00' if self.bad_save else b'\x01\x00\x00\x00'
                else:
                    self.values[key] = bytes(value)
            reply = b'\x60' + header + b'\x00'*4
        self.pending.append(canopen.Frame(0x581, reply))

    def recv(self, timeout):
        return self.pending.pop(0) if self.pending else None

    def close(self):
        self.closed = True

    def writes(self):
        return [event for event in self.events if event[0] == 'write']


class ParameterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'backup.json'
        self.peer = Peer()
        self.od = tool.load_od()
        self.specs = params.specifications(self.od)
        self.cfg = {'network': {'sync_period_us': 4000}, 'bus': {'can': [
            {'logical': tool.LOGICAL_BUS, 'iface': 'mock-interface', 'bitrate': 1000000, 'protocol': 'canopen'}]}}
        self.backup = params.capture(canopen.SdoClient(self.peer, 1), self.od, self.specs, 'mock-interface', 1000000, 1)
        params.save_backup(self.path, self.backup, self.specs)
        self.peer.sent.clear()
        self.peer.events.clear()
        self.patches = [
            mock.patch.object(tool, 'load_device_json', return_value=(self.cfg, 'mock-config')),
            mock.patch.object(tool, 'read_iface_actual', return_value={'state': 'UP', 'bitrate': 1000000, 'can_state': 'ERROR-ACTIVE'}),
            mock.patch.object(tool, 'require_rt_ack', return_value=True),
            mock.patch.object(tool, 'connect_bus', return_value=self.peer),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_cli(self, *args, bypass=True):
        argv = ['--params', *args, '--node', '1', '--no-tui']
        if bypass:
            argv += ['--od-gaps-bypass', 'isolated mock test']
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = tool.main(argv)
        return rc, output.getvalue()

    def test_every_default_plan_is_offline_zero_frames(self):
        for action in ('interpolation', 'transmission', 'rollback', 'save'):
            with self.subTest(action=action):
                rc, output = self.run_cli(action, '--backup', str(self.path))
                self.assertEqual(rc, 0, output)
                self.assertIn('零发送', output)
                self.assertEqual(self.peer.sent, [])
                tool.connect_bus.assert_not_called()
                tool.read_iface_actual.assert_not_called()
        rc, output = self.run_cli('interpolation')
        self.assertEqual(rc, 0, output)
        self.assertIn('bytes=fd', output)
        self.assertIn('bytes=04', output)
        print('B dry-run mock frame_count=0; bus_open_count=0; link_query_count=0')

    def test_backup_complete_roundtrip_and_exclusive_creation(self):
        data = params.load_backup(self.path, self.specs)
        self.assertEqual(data, self.backup)
        self.assertEqual(len(data['objects']), 12)
        self.assertEqual(len(data['identity']), 4)
        self.assertEqual(data['objects']['60C2:02']['value'], -1)
        self.assertEqual(data['objects']['60C2:02']['raw_hex'], 'ff')
        target = Path(self.tmp.name) / 'fresh.json'
        rc, output = self.run_cli('backup', '--backup', str(target))
        self.assertEqual(rc, 0, output)
        self.assertTrue(self.peer.closed)
        self.assertEqual(params.load_backup(target, self.specs)['objects'], data['objects'])
        self.assertEqual(len(self.peer.sent), 16)
        self.assertEqual(self.peer.writes(), [])
        self.peer.sent.clear()
        rc, _ = self.run_cli('backup', '--backup', str(target))
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.peer.sent, [])
        print('B explicit backup mock frames=16 SDO uploads; downloads=0; objects=12 identity=4')

    def test_apply_and_rollback_bytes_roundtrip_skip_save_commands(self):
        original = copy.deepcopy(self.peer.values)
        rc, output = self.run_cli('interpolation', '--apply', '--backup', str(self.path))
        self.assertEqual(rc, 0, output)
        self.assertEqual([event[2] for event in self.peer.writes()], [b'\x04', b'\xfd'])
        self.assertEqual([event[:2] for event in self.peer.events[4:]], [
            ('read', (0x60c2, 1)), ('write', (0x60c2, 1)), ('read', (0x60c2, 1)),
            ('read', (0x60c2, 2)), ('write', (0x60c2, 2)), ('read', (0x60c2, 2))])
        self.peer.events.clear()
        rc, output = self.run_cli('rollback', '--apply', '--backup', str(self.path))
        self.assertEqual(rc, 0, output)
        self.assertEqual(self.peer.values, original)
        self.assertEqual(len(self.peer.writes()), 10)
        self.assertFalse(any(e[1][0] == 0x1010 for e in self.peer.writes()))

    def test_entire_plan_bounds_checked_before_any_bus_frames(self):
        for action, values in [('interpolation', ['60C2:01=256']),
                               ('interpolation', ['60C2:02=-129']),
                               ('interpolation', ['60C2:02=128']),
                               ('interpolation', ['60C2:01=4', '60C2:02=253']),
                               ('transmission', ['1400:02=241']),
                               ('transmission', ['1800:02=256']),
                               ('interpolation', ['1010:01=0x65766173']),
                               ('save', ['60C2:01=4'])]:
            argv = [action, '--apply', '--backup', str(self.path)]
            for value in values:
                argv.extend(['--set', value])
            with self.subTest(values=values):
                rc, _ = self.run_cli(*argv)
                self.assertNotEqual(rc, 0)
                self.assertEqual(self.peer.sent, [])
        print('B out-of-range/late-invalid-plan mock frame_count=0')

    def test_malformed_or_partial_backup_refused_before_frames(self):
        variants = []
        for mutate in [lambda d: d['objects'].pop('1010:0B'),
                       lambda d: d['identity'].pop('4'),
                       lambda d: d['objects']['60C2:02'].update(value=253),
                       lambda d: d['objects']['60C2:02'].update(raw_hex='fe'),
                       lambda d: d.update(timestamp_utc='not-a-time')]:
            data = copy.deepcopy(self.backup)
            mutate(data)
            variants.append(data)
        for data in variants:
            self.path.write_text(json.dumps(data))
            rc, _ = self.run_cli('interpolation', '--apply', '--backup', str(self.path))
            self.assertNotEqual(rc, 0)
            self.assertEqual(self.peer.sent, [])
        rc, _ = self.run_cli('interpolation', '--apply')
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.peer.sent, [])

    def test_write_read_mismatch_and_abort_stop_immediately(self):
        for kind in ('ignore_write', 'abort_write'):
            with self.subTest(kind=kind):
                peer = Peer()
                setattr(peer, kind, (0x60c2, 1))
                with mock.patch.object(tool, 'connect_bus', return_value=peer):
                    rc, _ = self.run_cli('interpolation', '--apply', '--backup', str(self.path))
                self.assertNotEqual(rc, 0)
                self.assertEqual(len(peer.writes()), 1)
                self.assertTrue(peer.closed)

    def test_save_independent_second_confirmation_and_status_one(self):
        for token in (None, 'yes', 'SAVE wrong-device 1 4660'):
            args = ['save', '--apply', '--backup', str(self.path)]
            if token is not None:
                args.extend(['--confirm-save', token])
            rc, _ = self.run_cli(*args)
            self.assertNotEqual(rc, 0)
            self.assertEqual(self.peer.sent, [])
        token = params.confirmation(self.backup)
        rc, output = self.run_cli('save', '--apply', '--backup', str(self.path), '--confirm-save', token)
        self.assertEqual(rc, 0, output)
        self.assertEqual(self.peer.writes(), [('write', (0x1010, 1), b'save')])
        self.assertIn('readback=1', output)
        peer = Peer()
        peer.bad_save = True
        with mock.patch.object(tool, 'connect_bus', return_value=peer):
            rc, _ = self.run_cli('save', '--apply', '--backup', str(self.path), '--confirm-save', token)
        self.assertNotEqual(rc, 0)
        self.assertEqual(len(peer.writes()), 1)

    def test_identity_or_transport_mismatch_never_writes(self):
        for sub in (1, 2, 3, 4):
            peer = Peer()
            peer.values[0x1018, sub] = b'\x00'*4
            with mock.patch.object(tool, 'connect_bus', return_value=peer):
                rc, _ = self.run_cli('interpolation', '--apply', '--backup', str(self.path))
            self.assertNotEqual(rc, 0)
            self.assertEqual(peer.writes(), [])
            self.assertTrue(peer.closed)
        for key, value in [('iface', 'wrong'), ('node', 2), ('bitrate', 500000)]:
            data = dict(self.backup, **{key: value})
            self.path.write_text(json.dumps(data))
            rc, _ = self.run_cli('interpolation', '--apply', '--backup', str(self.path))
            self.assertNotEqual(rc, 0)
            self.assertEqual(self.peer.sent, [])

    def test_live_bitrate_state_width_and_gate_closed(self):
        for actual in (None, {'state': 'DOWN'}, {'state': 'UP', 'bitrate': 500000},
                       {'state': 'UP', 'bitrate': 1000000, 'can_state': 'BUS-OFF'}):
            with mock.patch.object(tool, 'read_iface_actual', return_value=actual):
                rc, _ = self.run_cli('interpolation', '--apply', '--backup', str(self.path))
            self.assertNotEqual(rc, 0)
            self.assertEqual(self.peer.sent, [])
        self.peer.values[0x60c2, 1] = b'\x14\x00'
        rc, _ = self.run_cli('interpolation', '--apply', '--backup', str(self.path))
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.peer.writes(), [])
        self.peer.sent.clear()
        rc, output = self.run_cli('interpolation', bypass=False)
        self.assertEqual(rc, 3, output)
        self.assertIn('项对象字典未核对', output)
        self.assertEqual(self.peer.sent, [])

    def test_transmission_requires_human_power_cycle_confirmation(self):
        rc, _ = self.run_cli('transmission', '--apply', '--backup', str(self.path))
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.peer.sent, [])
        rc, output = self.run_cli('transmission', '--apply', '--backup', str(self.path), '--confirm-interpolation-readback')
        self.assertEqual(rc, 0, output)
        self.assertEqual(len(self.peer.writes()), 8)
        self.assertTrue(all(event[2] == b'\x00' for event in self.peer.writes()))

    def test_duplicate_backup_json_rejected_before_frames(self):
        text = self.path.read_text()
        self.path.write_text(text.replace('"model": "pmp4010",',
                                          '"model": "pmp4010", "model": "pmp4010",'))
        rc, _ = self.run_cli('rollback', '--apply', '--backup', str(self.path))
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.peer.sent, [])

    def test_save_unsupported_pre_read_never_downloads(self):
        self.peer.values[0x1010, 1] = b'\x00'*4
        rc, output = self.run_cli('save', '--apply', '--backup', str(self.path),
                                  '--confirm-save', params.confirmation(self.backup))
        self.assertNotEqual(rc, 0, output)
        self.assertEqual(self.peer.writes(), [])
        self.assertTrue(self.peer.closed)

    def test_period_comes_from_configuration_and_exact_integer_math(self):
        self.assertEqual(params.interpolation_values(4000), (4, -3))
        self.cfg['network']['sync_period_us'] = 8000
        rc, output = self.run_cli('interpolation')
        self.assertEqual(rc, 0, output)
        self.assertIn('→ 8 bytes=08', output)
        for value in (0, True, 4000.0, 1000001, 257):
            with self.assertRaises(params.ParameterError):
                params.interpolation_values(value)


if __name__ == '__main__':
    unittest.main()
