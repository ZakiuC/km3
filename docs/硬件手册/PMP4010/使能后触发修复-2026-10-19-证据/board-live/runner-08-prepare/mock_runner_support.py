"""One normal new-package runner transaction, standard MockBus only."""
from contextlib import ExitStack
import json
import socket
import sys
from unittest import mock


def run_mock(args, modules, transaction):
    sys.path.append(str(args.mock_repo.resolve()/'device/drivers/pmp4010/tools'))
    from _test_manual import EnabledEdgeNode
    from _test_bench_support import DelayedBus, mockbus, requests
    tool = modules['pmp4010']
    assert args.mock_case == 'success'
    assert tool.std_layer_available()
    modules['pmp4010_velocity'].require_velocity_library()

    class Node(EnabledEdgeNode):
        def handle(self, frame, now):
            data = frame.data
            if data[:4] == bytes.fromhex('2b406000') and int.from_bytes(data[4:6], 'little') == 6:
                mockbus.MockNode.handle(self, frame, now)
                self.set_value(0x6041, 0x21)
                self.trajectory_target = None
                self.set_value(0x606c, 0)
                return
            if (data[:4] == bytes.fromhex('40416000') and
                    self.trajectory_target is not None and
                    self.value(0x6064, signed=True) == self.trajectory_target):
                self.set_value(0x6041, self.value(0x6041) | 0x400)
            if data[0] == 0x40:
                index = int.from_bytes(data[1:3], 'little')
                if index in (0x606b, 0x6069):
                    self.set_value(index, self.value(0x606c, signed=True))
                elif index == 0x6063:
                    self.set_value(index, self.value(0x6064, signed=True))
                elif index == 0x60f4:
                    self.set_value(index, self.value(0x6062, signed=True)-self.value(0x6064, signed=True))
            super().handle(frame, now)

    node = Node()
    for index in (0x606b, 0x6069, 0x6063, 0x60f4):
        node.od[index, 0] = mockbus.OdEntry(bytes(4), writable=False)
    bus = DelayedBus([node])
    cfg = {'bus': {'can': [{'logical': 'can-2', 'iface': 'can1', 'bitrate': 1000000}]}}
    actual = {'state': 'UP', 'bitrate': 1000000, 'can_state': 'ERROR-ACTIVE'}
    def owner(expected):
        return {'mock_only': True, 'bus_open_phase': bool(expected)}
    def approve(digest):
        assert not requests(bus, True), 'review phase must have zero writes'
        return True
    with ExitStack() as stack:
        for name, value in [('load_device_json', (cfg, 'mock-config')),
                            ('read_iface_actual', actual), ('service_active', (True, 'active')),
                            ('parse_ip_stats', {}), ('_std_open_active', bus)]:
            stack.enter_context(mock.patch.object(tool, name, return_value=value))
        stack.enter_context(mock.patch.object(socket, 'socket', side_effect=AssertionError('real socket forbidden')))
        rc = transaction(modules, args.output, args.rt_reason, args.od_reason,
                         owner, approve, observe_seconds=10)
    assert rc == 0, rc
    writes = requests(bus, True)
    words = [v for i, s, v in writes if i == 0x6040]
    assert words == [0x106, 0x107, 0x127, 0x137, 0x127, 0x12f, 0x2f, 0x3f, 0x2f, 2, 0, 6], words
    assert all(i in (0x6040, 0x607a, 0x6081) for i, s, v in writes)
    assert all(frame.arbitration_id != 0 for frame in bus.sent)
    review = json.loads((args.output/'review.json').read_text())['snapshot']
    assert review['displacement'] == 291 and review['settings']['max_displacement'] == 364
    assert review['target']-review['start'] == 291 and review['velocity_units_per_s'] == 100
    assert review['settings']['soft_min'] >= -1847 and review['settings']['soft_max'] <= -1119
    assert node.enabled_edges == [(0x2f, 0x3f, review['target'])], node.enabled_edges
    assert node.value(0x6064, signed=True) == review['target']
    records = [json.loads(line) for line in (args.output/'runner.jsonl').read_text().splitlines()]
    go_ack = [row['data'] for row in records if row['event'] == 'manual_transition_snapshot'
              and row['data']['phase'] == '使能后新ACK核对']
    assert [row['expected_ack'] for row in go_ack] == [False, True, False]
    final = next(row['data'] for row in reversed(records) if row['event']=='FINAL')
    assert final['completed'] and not final['hardware_motion_acceptance']
    details = [row['data'] for row in records if row['event'] == '动作后细速与偏差']
    assert details and {int(row['index'],16) for row in details} == {0x606b,0x6069,0x6063,0x60f4}
    assert all(row['width']==4 for row in details)
    # One focused read-path check, no second motion transaction: drop first RO.
    node.drop_reads.add((0x606b, 0))
    failed_bus = DelayedBus([node])
    client = tool._canopen.SdoClient(failed_bus, 1, timeout=.01)
    session = tool.MotionSession(client, None, .1)
    class NullOutput:
        def event(self, *args): pass
    try:
        transaction.__globals__['read_motion_details'](session, session.deadline(), NullOutput())
        raise AssertionError('RO timeout did not fail')
    except tool.MotionError:
        pass
    assert client.quarantined
    count = len(failed_bus.sent)
    try:
        transaction.__globals__['read_motion_details'](session, session.deadline(), NullOutput())
        raise AssertionError('quarantine accepted a request')
    except tool.MotionError:
        pass
    assert len(failed_bus.sent) == count == 1
    failed_bus.close()
    verdict = dict(result='PASS_MOCK_ONLY', runner_rc=rc, review_writes=0,
                   commanded_displacement=291, production_budget=364,
                   controlwords=words, enabled_edges=node.enabled_edges,
                   new_ack=[row['expected_ack'] for row in go_ack], diagnostic_samples=len(details),
                   read_timeout_quarantined=True, requests_after_quarantine=0, final=final)
    (args.output/'mock-verdict.json').write_text(json.dumps(verdict, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(verdict, ensure_ascii=False), flush=True)
    return 0
