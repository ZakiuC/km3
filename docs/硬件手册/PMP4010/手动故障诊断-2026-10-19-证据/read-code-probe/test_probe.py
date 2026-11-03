"""Offline proof of two-upload gate; socket creation and subprocess are forbidden."""
import dataclasses
import importlib.util
from pathlib import Path
import socket
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('read_code_probe', Path(__file__).with_name('probe.py'))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@dataclasses.dataclass
class Frame:
    arbitration_id: int
    data: bytes
    extended: bool = False
    remote: bool = False
    frame_type: str = 'classic'


MODULE = types.SimpleNamespace(Frame=Frame, FRAME_CLASSIC='classic')


def response(index, value=0, **overrides):
    values = dict(arbitration_id=0x581,
                  data=bytes((0x4B, index & 255, index >> 8, 0, value & 255, value >> 8, 0, 0)))
    values.update(overrides)
    return Frame(**values)


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        assert 0 < value <= probe.REQUEST_TIMEOUT
        self.timeouts.append(value)


class Bus:
    def __init__(self, responses, send_exception=None):
        self.responses = list(responses)
        self.sent, self.receives, self.opens, self.closes = [], [], 0, 0
        self.send_exception = send_exception
        self._sock = FakeSocket()

    def open(self):
        self.opens += 1

    def send(self, frame):
        self.sent.append(frame)
        if self.send_exception:
            raise self.send_exception

    def recv(self, timeout):
        self.receives.append(timeout)
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self):
        self.closes += 1


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.socket_patch = patch.object(socket, 'socket', side_effect=AssertionError('live socket forbidden in mock'))
        self.process_patch = patch.object(subprocess, 'run', side_effect=AssertionError('subprocess forbidden in mock'))
        self.socket_mock = self.socket_patch.start()
        self.process_mock = self.process_patch.start()
        self.addCleanup(self.socket_patch.stop)
        self.addCleanup(self.process_patch.stop)

    def tearDown(self):
        self.socket_mock.assert_not_called()
        self.process_mock.assert_not_called()

    def test_normal_exact_two_uploads(self):
        bus = Bus([response(0x603F, 0x1234), response(0x6041, 0x237)])
        self.assertEqual(probe.read_two(bus, MODULE), {'603F:00': 0x1234, '6041:00': 0x237})
        self.assertEqual([f.data.hex() for f in bus.sent], ['403f600000000000', '4041600000000000'])
        self.assertEqual([f.arbitration_id for f in bus.sent], [0x601, 0x601])
        self.assertEqual(len(bus._sock.timeouts), 2)
        self.assertEqual((bus.opens, bus.closes, len(bus.receives)), (1, 1, 2))

    def assert_first_failure(self, item, exc=RuntimeError):
        bus = Bus([item, response(0x6041)])
        with self.assertRaises(exc):
            probe.read_two(bus, MODULE)
        self.assertEqual((len(bus.sent), len(bus.receives), bus.closes), (1, 1, 1))
        self.assertEqual(len(bus.responses), 1)

    def test_first_timeout_has_zero_later_requests(self):
        self.assert_first_failure(None, TimeoutError)

    def test_wrong_node_response_rejected(self):
        self.assert_first_failure(response(0x603F, arbitration_id=0x582))

    def test_wrong_index_response_rejected(self):
        self.assert_first_failure(response(0x6041))

    def test_wrong_sub_response_rejected(self):
        f = response(0x603F)
        self.assert_first_failure(dataclasses.replace(f, data=f.data[:3] + b'\x01' + f.data[4:]))

    def test_wrong_width_response_rejected(self):
        f = response(0x603F)
        self.assert_first_failure(dataclasses.replace(f, data=b'\x43' + f.data[1:]))

    def test_abort_response_stops_without_retry(self):
        f = response(0x603F)
        self.assert_first_failure(dataclasses.replace(f, data=b'\x80' + f.data[1:]))

    def test_extended_remote_and_fd_responses_rejected(self):
        for changes in ({'extended': True}, {'remote': True}, {'frame_type': 'fd'}):
            with self.subTest(changes=changes):
                self.assert_first_failure(response(0x603F, **changes))

    def test_interrupt_closes_only(self):
        self.assert_first_failure(KeyboardInterrupt(), KeyboardInterrupt)

    def test_send_failure_has_no_receive_or_later_request(self):
        bus = Bus([], OSError('mock send failure'))
        with self.assertRaises(OSError):
            probe.read_two(bus, MODULE)
        self.assertEqual((len(bus.sent), len(bus.receives), bus.closes), (1, 0, 1))

    def test_second_timeout_has_two_total_requests(self):
        bus = Bus([response(0x603F), None])
        with self.assertRaises(TimeoutError):
            probe.read_two(bus, MODULE)
        self.assertEqual((len(bus.sent), len(bus.receives), bus.closes), (2, 2, 1))

    def test_disallowed_or_write_requests_rejected_before_transport(self):
        frames = [Frame(0x601, bytes.fromhex(h)) for h in (
            '4040600000000000',  # Nonallowlisted 6040 upload.
            '2b3f600000000000',  # Download, even to approved index.
            '4041600000000000',  # Wrong order: 6041 first.
            '403f600001000000',  # Nonzero reserved bytes.
        )] + [Frame(0x602, bytes.fromhex('403f600000000000')),
              Frame(0, bytes.fromhex('0101000000000000'))]
        for frame in frames:
            with self.subTest(frame=frame):
                bus = Bus([])
                guard = probe.Guard(bus, MODULE)
                guard.begin(probe.TABLE[0])
                with self.assertRaises(RuntimeError):
                    guard.send(frame)
                self.assertEqual(bus.sent, [])
                self.assertTrue(guard.failed)

    def test_duplicate_send_and_terminal_reuse_rejected(self):
        bus = Bus([])
        guard = probe.Guard(bus, MODULE)
        guard.begin(probe.TABLE[0])
        frame = Frame(0x601, bytes.fromhex('403f600000000000'))
        guard.send(frame)
        with self.assertRaises(RuntimeError):
            guard.send(frame)
        with self.assertRaises(RuntimeError):
            guard.begin(probe.TABLE[1])
        self.assertEqual(len(bus.sent), 1)

    def test_total_deadline_prevents_send(self):
        clock = [0.0]
        bus = Bus([])
        guard = probe.Guard(bus, MODULE, lambda: clock[0])
        guard.begin(probe.TABLE[0])
        clock[0] = 1.1
        with self.assertRaises(RuntimeError):
            guard.send(Frame(0x601, bytes.fromhex('403f600000000000')))
        self.assertEqual(bus.sent, [])

    def test_request_deadline_prevents_send(self):
        clock = [0.0]
        bus = Bus([])
        guard = probe.Guard(bus, MODULE, lambda: clock[0])
        guard.begin(probe.TABLE[0])
        clock[0] = 0.3
        with self.assertRaises(TimeoutError):
            guard.send(Frame(0x601, bytes.fromhex('403f600000000000')))
        self.assertEqual(bus.sent, [])

    def test_third_request_blocked_after_success(self):
        bus = Bus([response(0x603F), response(0x6041)])
        guard = probe.Guard(bus, MODULE)
        for row in probe.TABLE:
            guard.begin(row)
            i, sub, _ = row
            guard.send(Frame(0x601, bytes((0x40, i & 255, i >> 8, sub, 0, 0, 0, 0))))
            guard.recv()
        with self.assertRaises(RuntimeError):
            guard.begin(probe.TABLE[0])
        self.assertEqual(len(bus.sent), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
