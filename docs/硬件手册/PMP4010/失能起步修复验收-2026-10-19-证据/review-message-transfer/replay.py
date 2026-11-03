import sys, json, hashlib, copy, queue, threading
from pathlib import Path
from contextlib import ExitStack
from unittest import mock
sys.path.insert(0, str(Path('device/drivers/pmp4010/tools').resolve()))
from _test_bench_support import environment, options, requests, tool, position, DelayedBus
from _test_disabled_start import HaltProbeNode
from _test_position import Clock
from pmp4010_bench import BenchController
import pmp4010_tui as ui
manifest = Path('/tmp/km3-pp-disabled-start-20261019-impl/probe-freeze/source-sha256.json')
expected = json.loads(manifest.read_text())['files']
def hashes():
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in expected}
assert hashes() == expected
forbidden = []
def audit(event, args):
    if event == 'open' and ((args[1] and any(c in args[1] for c in 'wax+')) or (isinstance(args[2], int) and args[2] & 3)):
        forbidden.append(event); raise AssertionError('file write forbidden')
    if event in ('socket.__new__', 'socket.connect', 'subprocess.Popen'):
        forbidden.append(event); raise AssertionError('external IO forbidden')
sys.addaudithook(audit)
class MemoryJournal:
    def __init__(self, *args, **kwargs):
        self.events = []; journals.append(self)
    def event(self, event, **data):
        self.events.append((event, copy.deepcopy(data)))
        if event == 'result' and case == 'final_postflight_follow_error':
            node.set_value(0x6041, 0x2040)
    def close(self): pass
cases = ['nominal_halt_only_and_single_use', 'no_new_ACK', 'old_6062_not_replaced',
         'first_fault', 'first_position_drift', 'first_nonzero_velocity',
         'premature_ACK_before_trigger_read_1', 'premature_ACK_before_trigger_read_2',
         'seal_args_tampering_zero_IO', 'cancelled_seal_is_zero_IO',
         'lost_write_ACK_quarantine_no_wire_retry', 'final_postflight_follow_error',
         'actual_global_deadline_0.5', 'actual_global_deadline_1.0']
results = []
normal = [0x106, 0x107, 0x10f, 0x12f, 0x13f, 0x12f, 2, 0]
for case in cases:
    node = HaltProbeNode(); bus = DelayedBus([node]); clock = Clock(); clock.now = 0
    journals = []; phase = [None]; counts = {}; injected = [False]
    args = options('memory-only.jsonl')
    args.move_timeout = float(case.rsplit('_', 1)[1]) if case.startswith('actual_global_deadline_') else 4
    with ExitStack() as stack:
        for patch in environment(bus): stack.enter_context(patch)
        stack.enter_context(mock.patch('time.monotonic', clock.monotonic))
        stack.enter_context(mock.patch('time.sleep', clock.sleep))
        stack.enter_context(mock.patch.object(position, 'Journal', MemoryJournal))
        ctl = BenchController(tool, args); out = ui.JobOutput(queue.Queue(), threading.Event())
        ctl.rt_ack = True; ctl.scan(out); ctl.refresh(out)
        ctl.od_reason = 'independent readonly memory audit'
        ctl.settings.update(soft_min=-1500, soft_max=-1400, max_displacement=100)
        review = ctl.review('probe_pp_start', 1, None, out); bus.sent.clear()
        real_factory = ctl._disabled_start_guard
        def factory(*a):
            real = real_factory(*a)
            def guard(session, label, **kwargs):
                phase[0] = label; counts.clear()
                return real(session, label, **kwargs)
            return guard
        stack.enter_context(mock.patch.object(ctl, '_disabled_start_guard', side_effect=factory))
        real_send = bus.send
        def send(frame):
            data = frame.data; addr = (data[1] | data[2] << 8, data[3])
            fault_map = {'first_fault': (0x6041, 0x268), 'first_position_drift': (0x6064, -1469), 'first_nonzero_velocity': (0x606c, 1)}
            target = fault_map.get(case)
            label = '起步稳定采样2/5'; ordinal = 1
            if case.startswith('premature_ACK_'):
                target = (0x6041, 0x1027); label = 'before_trigger'; ordinal = int(case[-1])
            if target and data[0] == 0x40 and phase[0] == label and addr == (target[0], 0):
                counts[addr] = counts.get(addr, 0) + 1
                if counts[addr] == ordinal:
                    old = node.od[addr].data; node.set_value(target[0], target[1])
                    try: return real_send(frame)
                    finally: node.od[addr].data = old; injected[0] = True
            return real_send(frame)
        stack.enter_context(mock.patch.object(bus, 'send', side_effect=send))
        if case == 'no_new_ACK': node.ignore_setpoint = True
        if case == 'old_6062_not_replaced': node.keep_old_demand = True
        if case == 'seal_args_tampering_zero_IO': review['args'].move_timeout = .5
        if case == 'cancelled_seal_is_zero_IO': ctl.discard_prepare()
        if case == 'lost_write_ACK_quarantine_no_wire_retry': node.drop_write_ack.add((0x607a, 0))
        error = None
        try: ctl.execute(review, out)
        except BaseException as exc: error = exc
        controls = [v for i, _, v in requests(bus, True) if i == 0x6040]
        records = [r for j in journals for r in j.events]
        if case == 'nominal_halt_only_and_single_use':
            assert error is None and controls == normal
            samples = [d for ev, d in records if ev == 'disabled_start_sample']
            assert sum(d['phase'].startswith('起步稳定采样') for d in samples) == 5
            assert sum(d['phase'].startswith('Halt保持核证采样') for d in samples) == 5
            assert node.value(0x6064, signed=True) == node.value(0x6062, signed=True) == -1470
            assert node.value(0x6041) == 0x40
            assert all(w & 0x100 for w in controls[:-2])
            assert not any(i == 0x605d for i, _, _ in requests(bus, True))
            count = len(bus.sent)
            try: ctl.execute(review, out)
            except ValueError as exc: assert '审阅' in str(exc)
            else: raise AssertionError('replay accepted')
            assert len(bus.sent) == count
        else:
            assert error is not None, case
            if case.startswith('first_'):
                assert injected[0] and requests(bus, True) == []
            elif case.startswith('premature_ACK_'):
                assert injected[0] and 'ACK提前置位' in str(error) and 0x13f not in controls and controls[-2:] == [2, 0]
            elif case in ('seal_args_tampering_zero_IO', 'cancelled_seal_is_zero_IO'):
                assert '审阅' in str(error) and bus.sent == []
            elif case == 'lost_write_ACK_quarantine_no_wire_retry':
                assert len(requests(bus, True)) == 1 and controls == []
                count = len(bus.sent); node.drop_write_ack.clear(); ctl.refresh(out)
                try: ctl.execute(review, out)
                except ValueError: pass
                else: raise AssertionError('retry accepted')
                assert len(bus.sent) == count and ctl.blocked
            elif case.startswith('actual_global_deadline_'):
                assert '整体' in str(error) or '超时' in str(error)
                assert clock.now <= args.move_timeout and node.value(0x6041) & 0x4f == 0x40
                assert requests(bus, True) == [] if args.move_timeout == .5 else controls == normal
            elif case == 'final_postflight_follow_error':
                assert '最终读回' in str(error) and controls == normal
            else:
                assert controls[-2:] == [2, 0]
        results.append(dict(case=case, result='PASS', error=None if error is None else str(error),
                            controls=[hex(v) for v in controls], requests=len(bus.sent), elapsed_mock_seconds=round(clock.now, 6)))
assert hashes() == expected and not forbidden
print(json.dumps(dict(result='PASS', cases=results, case_count=len(results), source_files_verified=len(expected),
    source_before_after_equal=True, forbidden_IO_attempts=len(forbidden), journal='memory only', hardware_acceptance=False,
    history_correction='The first audit output mislabeled seal_args_tampering_zero_IO as whole_deadline_includes_initial_sampling. The separate 0.5s and 1.0s runs, and this replay, test the actual global deadline. Preserve the earlier naming mistake and correction.'), ensure_ascii=False, indent=2))
