"""Local MockBus checks only. Proves uploads-only and stop-on-first-timeout."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest.mock as mock

HERE = Path('/tmp/km3-pp-preflight-20261017')
SOURCE = Path('/home/zakiu/km3')
sys.path.insert(0, str(SOURCE/'device/drivers/pmp4010/tools'))
spec = importlib.util.spec_from_file_location('preflight', HERE/'preflight.py')
pre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre)
import _test_bench_support as support
import pmp4010_bench as bench

results = []
for name, fail in [('nominal_uploads_only', False), ('timeout_stops_without_retry', True)]:
    node = support.BenchNode()
    if fail:
        node.drop_reads.add((0x607f, 0))
    bus = support.mockbus.MockBus([node], loopback=False)
    controller = bench.BenchController(support.tool, support.options())
    controller.rt_ack = True
    stream = io.StringIO()
    with contextlib.ExitStack() as stack:
        for patch in support.environment(bus):
            stack.enter_context(patch)
        stack.enter_context(mock.patch.object(support.tool._canopen.SdoClient, 'write', side_effect=AssertionError('WRITE forbidden')))
        stack.enter_context(mock.patch.object(support.tool._canopen.SdoClient, 'confirm_external_recovery', side_effect=AssertionError('recovery forbidden')))
        with contextlib.redirect_stdout(stream):
            controller._configuration(pre.Output())
            client = support.tool._canopen.SdoClient(bus, 1, timeout=.015)
            failed = False
            try:
                result = pre.collect(controller, support.tool, support.position, client, 0)
            except support.tool._canopen.BusSilenceError:
                failed = True
                controller._latch(client, 1, '只读预检', 'mock timeout', pre.Output())
            before_close = len(bus.sent)
            assert controller.enabled_sessions == {}
            controller.close(pre.Output())
            assert len(bus.sent) == before_close, 'close unexpectedly sent requests'
    assert all(f.arbitration_id == 0x601 for f in bus.sent)
    assert all(f.data[0] in (0x40, 0x60, 0x70) for f in bus.sent), 'non-upload request'
    assert failed == fail
    if fail:
        assert client.quarantined
        assert bus.sent[-1].data[:4] == bytes([0x40, 0x7f, 0x60, 0])
        assert sum(f.data[:4] == bytes([0x40, 0x7f, 0x60, 0]) for f in bus.sent) == 1
        assert ('mock-bench', 1) in controller.blocked
    else:
        assert not result['rejection_reasons'], result
        assert result['candidate_delta'] == 36
        assert result['candidate_target'] == -1428
        assert result['live_read_count'] == 62
        assert result['candidate_motor_degrees'] == '405/4096'
    (HERE/(name+'.jsonl')).write_text(stream.getvalue())
    results.append(dict(name=name, passed=True, request_frames=len(bus.sent),
                        writes=0, nmt=0, close_frames=0, real_can_opens=0,
                        quarantined=client.quarantined, retry_count=0))
print(json.dumps(dict(mock_only=True, results=results), ensure_ascii=False, indent=2))
