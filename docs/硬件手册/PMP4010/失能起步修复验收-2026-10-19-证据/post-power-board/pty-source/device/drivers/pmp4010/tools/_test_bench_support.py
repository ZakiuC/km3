"""Local-only bench fixtures. Every transport is a real standard-layer MockBus."""
from pathlib import Path
import importlib.util
import queue
import sys
import threading
import time
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('bench_acceptance_cli', HERE / 'pmp4010.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
assert tool.std_layer_available()
import mockbus
import canopen
import pmp4010_position as position


class BenchNode(mockbus.MockNode):
    """Behaviour fixture; MockNode independently encodes all SDO replies."""
    def __init__(self, node=1, velocity_unit=0):
        values = {
            (0x2002, 1): (0, 2, False), (0x6040, 0): (0, 2, False),
            (0x6041, 0): (0x0440, 2, False), (0x6060, 0): (1, 1, True),
            (0x605d, 0): (1, 2, True), (0x6062, 0): (-1464, 4, True),
            (0x6061, 0): (1, 1, True), (0x6064, 0): (-1464, 4, True),
            (0x606c, 0): (0, 4, True), (0x603f, 0): (0, 2, False),
            (0x6077, 0): (0, 2, True), (0x607c, 0): (0, 4, True), (0x2006, 0x12): (velocity_unit, 2, False),
            (0x608f, 1): (131072, 4, False), (0x608f, 2): (1, 4, False),
            (0x6091, 1): (1, 4, False), (0x6091, 2): (1, 4, False),
            (0x607f, 0): (10000, 4, False), (0x6080, 0): (1000, 4, False),
            (0x60c5, 0): (10000, 4, False), (0x60c6, 0): (10000, 4, False),
            (0x607d, 1): (-2147483627, 4, True), (0x607d, 2): (2147483627, 4, True),
            (0x607a, 0): (-1464, 4, True), (0x6081, 0): (100, 4, False),
            (0x6082, 0): (0, 4, False), (0x6083, 0): (100, 4, False),
            (0x6084, 0): (100, 4, False), (0x6098, 0): (17, 1, True),
            (0x60ff, 0): (6553600, 4, True), (0x60b1, 0): (0, 4, True),
        }
        super().__init__(node, od={addr: mockbus.OdEntry(value.to_bytes(size, 'little', signed=signed), writable=True)
                                  for addr, (value, size, signed) in values.items()},
                         manufacturer_name='PMPP4010B-CANopen-0HEB-004', software_version='V3.3')
        self.pp_triggered = False
        self.drop_reads = set()
        self.drop_write_ack = set()
        self.drift_before_target = None

    def value(self, index, sub=0, signed=False):
        return int.from_bytes(self.od[index, sub].data, 'little', signed=signed)

    def set_value(self, index, value, sub=0):
        entry = self.od[index, sub]
        entry.data = value.to_bytes(len(entry.data), 'little', signed=value < 0)

    def handle(self, frame, now):
        if frame.arbitration_id == 0:
            raise AssertionError('NMT forbidden in bench fixture')
        if frame.arbitration_id != canopen.cob_id_sdo_tx(self.node_id):
            return
        data = frame.data
        key = (data[1] | data[2] << 8, data[3])
        if data[0] == 0x40 and key in self.drop_reads:
            return
        super().handle(frame, now)
        if data[0] not in (0x2f, 0x2b, 0x23):
            return
        if key == (0x6060, 0):
            self.od[0x6061, 0].data = self.od[0x6060, 0].data
        if key == (0x6040, 0):
            word = self.value(0x6040)
            state = {0: 0x440, 2: 0x407, 6: 0x421, 7: 0x423,
                     15: 0x427, 0x2f: 0x427, 0x3f: 0x1027, 0x1f: 0x9427}[word]
            self.set_value(0x6041, state)
            if word == 0x3f:
                self.pp_triggered = True
            if word == 0x2f and self.pp_triggered:
                self.od[0x6064, 0].data = self.od[0x607a, 0].data
            if word == 0x1f and self.value(0x6060) == 6:
                self.set_value(0x6064, 0)
        if key in self.drop_write_ack:
            self._outbox.clear()


class DelayedBus(mockbus.MockBus):
    """Block only a selected SDO upload in the worker, never the UI thread."""
    def __init__(self, nodes):
        super().__init__(nodes, loopback=False)
        self.delay_key = None
        self.entered = threading.Event()
        self.release = threading.Event()

    def send(self, frame):
        if frame.data[0] == 0x40 and (frame.data[1] | frame.data[2] << 8, frame.data[3]) == self.delay_key:
            self.entered.set()
            if not self.release.wait(3):
                raise AssertionError('delayed MockBus not released')
        return super().send(frame)


def requests(bus, writing=None):
    result = []
    for frame in bus.sent:
        data = frame.data
        is_write = data[0] in (0x2f, 0x2b, 0x23)
        if writing is None or writing == is_write:
            result.append((data[1] | data[2] << 8, data[3],
                           int.from_bytes(data[4:4+{0x2f: 1, 0x2b: 2, 0x23: 4}.get(data[0], 0)], 'little')))
    return result


def options(log=None):
    args = tool.build_parser().parse_args([
        '--node-range', '1-4', '--timeout', '.015', '--state-timeout', '.15',
        '--home-timeout', '.3', '--move-timeout', '.5', '--rows', '30', '--cols', '120',
        '--soft-min', '-2000', '--soft-max', '500', '--velocity', '100',
        '--acceleration', '100', '--deceleration', '100'])
    if log:
        args.motion_log = str(log)
    return args


def environment(bus):
    cfg = {'bus': {'can': [{'logical': tool.LOGICAL_BUS, 'iface': 'mock-bench', 'bitrate': 1000000}]}}
    actual = {'state': 'UP', 'bitrate': 1000000, 'can_state': 'ERROR-ACTIVE', 'berr_counter': {'tx': 0, 'rx': 0}}
    return [mock.patch.object(tool, 'load_device_json', return_value=(cfg, 'mock-config')),
            mock.patch.object(tool, 'read_iface_actual', return_value=actual),
            mock.patch.object(tool, 'service_active', return_value=(True, 'active')),
            mock.patch.object(tool, 'parse_ip_stats', return_value={}),
            mock.patch.object(tool, 'connect_bus', return_value=bus),
            mock.patch.object(tool, '_std_open_active', return_value=bus),
            mock.patch.object(tool._canbus.socket, 'socket', side_effect=AssertionError('real CAN forbidden'))]
