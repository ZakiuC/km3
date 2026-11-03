"""Offline runner verification; fixtures never enter live mode."""
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sys
from unittest import mock


def run_mock(args, modules, transaction):
    sys.path.append(str(args.mock_repo.resolve()/'device/drivers/pmp4010/tools'))
    from _test_manual import DelayedManualNode
    from _test_bench_support import DelayedBus, mockbus, requests
    tool = modules['pmp4010']
    assert tool.std_layer_available()
    modules['pmp4010_velocity'].require_velocity_library()

    class Node(DelayedManualNode):
        def __init__(self):
            super().__init__()
            self.enable_drift = 0
        def handle(self, frame, now):
            data = frame.data
            if data[:4] == bytes.fromhex('2b406000'):
                word = int.from_bytes(data[4:6], 'little')
                if word == 6:
                    mockbus.MockNode.handle(self, frame, now)
                    self.set_value(0x6041, 0x21)
                    return
                if word == 0x2f:
                    self.final_triggered = True  # Fixture simulates accepted PP target arrival.
            super().handle(frame, now)
    node = Node()
    bus = DelayedBus([node])
    cfg = {'bus': {'can': [{'logical': 'can-2', 'iface': 'can1', 'bitrate': 1000000}]}}
    actual = {'state': 'UP', 'bitrate': 1000000, 'can_state': 'ERROR-ACTIVE'}
    preapproval = []
    def owner(expected):
        return {'mock_only': True, 'expected_CAN_RAW': expected}
    def approve(digest):
        preapproval.extend(requests(bus, True))
        assert not preapproval, 'read/review phase must have zero writes'
        if args.mock_case == 'abort':
            return False
        if args.mock_case == 'drift':
            node.set_value(0x6064, node.value(0x6064, signed=True)+1)
        elif args.mock_case == 'fault':
            node.transition_sequences[0x106] = [0x268]
        elif args.mock_case == 'uncertain':
            node.drop_write_ack.add((0x607a, 0))
        return True
    with ExitStack() as stack:
        for name, value in [('load_device_json', (cfg, 'mock-config')),
                            ('read_iface_actual', actual), ('service_active', (True, 'active')),
                            ('parse_ip_stats', {}), ('_std_open_active', bus)]:
            stack.enter_context(mock.patch.object(tool, name, return_value=value))
        stack.enter_context(mock.patch.object(socket, 'socket', side_effect=AssertionError('real socket forbidden')))
        rc = transaction(modules, args.output, args.rt_reason, args.od_reason,
                         owner, approve, observe_seconds=.3)
    writes = requests(bus, True)
    words = [v for i, s, v in writes if i == 0x6040]
    assert all(i in (0x6040, 0x607a, 0x6081) for i, s, v in writes), writes
    assert all(f.arbitration_id != 0 for f in bus.sent), 'NMT forbidden'
    expected = {'success': 0, 'abort': 2, 'drift': 1, 'fault': 1, 'uncertain': 1}
    assert rc == expected[args.mock_case], (args.mock_case, rc)
    if args.mock_case == 'success':
        assert words == [0x106, 0x107, 0x127, 0x137, 0x127, 0x12f, 0x2f, 2, 0, 6], words
        review = json.loads((args.output/'review.json').read_text())['snapshot']
        assert review['displacement'] == 36 and review['velocity_units_per_s'] == 100
        assert node.value(0x6064, signed=True) == review['target']
    elif args.mock_case in ('abort', 'drift'):
        assert not writes, writes
    elif args.mock_case == 'fault':
        assert words == [0x106, 2, 0], words
    elif args.mock_case == 'uncertain':
        assert not words, words
        assert [i for i, s, v in writes] == [0x6081, 0x607a], writes
        # The uncertain target write is the last request. No cleanup/retry read.
        assert requests(bus)[-1][0] == 0x607a
    result = dict(mock_case=args.mock_case, runner_rc=rc, assertions='PASS',
                  controlwords=words, writes=writes, read_phase_writes=preapproval,
                  hardware_acceptance=False)
    (args.output/'mock-verdict.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)
    return 0
