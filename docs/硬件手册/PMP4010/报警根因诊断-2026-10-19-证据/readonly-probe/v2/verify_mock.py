"""Local-only deterministic response injection; never opens a native socket."""
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import socket
import struct
import subprocess
import sys
from unittest import mock

BASE = pathlib.Path(__file__).resolve().parent
PAYLOAD = BASE.parents[2] / '报警内容显示-2026-10-19-证据/package/payload'
spec = importlib.util.spec_from_file_location('readonly_once', BASE / 'readonly_once.py')
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)
checks = []


class Clock:
    def __init__(self): self.value = 0
    def now(self): return self.value
    def sleep(self, seconds): self.value += seconds


class MockSocket:
    def __init__(self, clock, fail_at=None, fault=None):
        self.clock, self.fail_at, self.fault = clock, fail_at, fault
        self.requests, self.timeouts = [], []
    def settimeout(self, seconds):
        assert 0 < seconds <= .25
        self.timeouts.append(seconds)
    def send(self, raw):
        ordinal = len(self.requests) + 1
        assert len(raw) == 16
        can_id, dlc, data = struct.unpack('<IB3x8s', raw)
        index, sub, size, signed, label = p.READS[ordinal - 1]
        assert can_id == 0x601 and dlc == 8
        assert data == bytes((0x40, index & 255, index >> 8, sub, 0, 0, 0, 0))
        self.requests.append(raw)
        if ordinal == self.fail_at and self.fault == 'send_error': raise OSError('mock send')
        if ordinal == self.fail_at and self.fault == 'short_send': return 15
        if ordinal == self.fail_at and self.fault == 'late_send': self.clock.value += .251
        return 16
    def recv(self, size):
        assert size == 17
        ordinal = len(self.requests)
        index, sub, width, signed, label = p.READS[ordinal - 1]
        value = 0x2C0 if label in ('disabled_guard', 'status_after') else (-1 if signed else 0x12)
        data = bytes(({1: 0x4F, 2: 0x4B, 4: 0x43}[width], index & 255, index >> 8, sub))
        data += value.to_bytes(width, 'little', signed=signed) + bytes(4-width)
        can_id, dlc = 0x581, 8
        if ordinal == self.fail_at:
            if self.fault == 'timeout': raise socket.timeout('mock silence')
            if self.fault == 'recv_error': raise OSError('mock recv')
            if self.fault == 'late_receive': self.clock.value += .251
            if self.fault == 'abort': data = bytes((0x80,)) + data[1:4] + bytes.fromhex('00000206')
            if self.fault == 'wrong_width': data = bytes((0x4B if width != 2 else 0x43,)) + data[1:]
            if self.fault == 'wrong_object': data = data[:3] + bytes(((sub+1) % 256,)) + data[4:]
            if self.fault == 'wrong_id': can_id = 0x582
            if self.fault == 'error_frame': can_id = 0x20000004
            if self.fault == 'rtr_frame': can_id |= 0x40000000
            if self.fault == 'extended_frame': can_id |= 0x80000000
            if self.fault == 'wrong_dlc': dlc = 7
            if self.fault == 'not_disabled': data = data[:4] + bytes.fromhex('27000000')
        raw = struct.pack('<IB3x8s', can_id, dlc, data)
        if ordinal == self.fail_at and self.fault == 'short_receive': raw = raw[:-1]
        if ordinal == self.fail_at and self.fault == 'fd_receive': raw += b'!'
        return raw


def run_case(name, ordinal=None, fault=None, expect_count=22, initial_time=0):
    clock = Clock()
    sock = MockSocket(clock, ordinal, fault)
    output = io.StringIO()
    failure = None
    with contextlib.redirect_stdout(output):
        try: values = p.probe(sock, co, cb, clock.now, clock.sleep)
        except Exception as exc: failure = str(exc)
    if fault:
        assert failure is not None, (name, 'failed to stop')
        assert len(sock.requests) == expect_count, (name, len(sock.requests), expect_count)
        assert not any(json.loads(line)['event']=='read_only_complete' for line in output.getvalue().splitlines())
    else:
        assert failure is None and len(values)==22 and len(sock.requests)==22
        assert clock.value==.5
        assert values[4]['value']==-1 and values[14]['value']==-1
    checks.append(dict(name=name, result='PASS', sent=len(sock.requests), failure=failure))


with mock.patch('socket.socket', side_effect=AssertionError('native socket forbidden in local test')):
    with contextlib.redirect_stdout(io.StringIO()): co, cb = p.load_pinned(PAYLOAD)
    checks.append(dict(name='cold_payload_load_native_socket_blocked', result='PASS'))
    run_case('normal_all_22_uploads')
    for ordinal in range(1,23):
        for fault in ('timeout','abort','wrong_width','wrong_object','wrong_id','wrong_dlc',
                      'short_receive','fd_receive','late_receive','send_error','short_send',
                      'late_send','recv_error','error_frame','rtr_frame','extended_frame'):
            run_case(f'{fault}_at_{ordinal}', ordinal, fault, ordinal)
    for ordinal in (1,22):
        run_case(f'not_disabled_at_{ordinal}', ordinal, 'not_disabled', ordinal)
    # Main's finally block closes exactly once after a protocol failure and sends no cleanup.
    class LifecycleSocket(MockSocket):
        def __init__(self): super().__init__(Clock(),1,'timeout'); self.closes=0
        def setsockopt(self,*args):
            if args[:2]==(101,1):
                can_id,mask=struct.unpack('=II',args[2])
                assert can_id==0x581 and mask==0xC00007FF
                assert mask & 0x20000000 == 0
            if args[:2]==(101,2): assert args[2]==struct.pack('=I',0x1FFFFFFF)
        def bind(self,address): assert address==('can1',)
        def close(self): self.closes+=1
    lifecycle=LifecycleSocket()
    with mock.patch.object(p,'load_pinned',return_value=(co,cb)), mock.patch.object(p,'os_preflight'), \
         mock.patch.object(p.signal,'signal'), mock.patch.object(p.signal,'setitimer'), \
         mock.patch.object(p.socket,'socket',return_value=lifecycle), \
         mock.patch.object(sys,'argv',['readonly_once.py','--execute-readonly-once']), \
         contextlib.redirect_stdout(io.StringIO()):
        try: p.main()
        except socket.timeout: pass
        else: raise AssertionError('main hid timeout')
    assert lifecycle.closes==1 and len(lifecycle.requests)==1
    checks.append(dict(name='failure_finally_socket_close_only', result='PASS', closes=1, sent=1))

cold_argv=[sys.executable,'-I','-B',str(BASE/'readonly_once.py'),'--cold-load','--payload-root',str(PAYLOAD)]
cold=subprocess.run(cold_argv,capture_output=True,text=True,timeout=10)
(BASE/'cold-load.stdout').write_text(cold.stdout)
(BASE/'cold-load.stderr').write_text(cold.stderr)
assert cold.returncode==0, cold.stderr+cold.stdout
checks.append(dict(name='isolated_cold_process', result='PASS', argv=cold_argv, rc=cold.returncode))
summary=dict(result='PASS', checks=len(checks), cases=checks, live_CAN_opens=0, live_CAN_sends=0,
             source_sha256=hashlib.sha256((BASE/'readonly_once.py').read_bytes()).hexdigest())
(BASE/'mock-results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(result='PASS',checks=len(checks),source_sha256=summary['source_sha256'],live_CAN_opens=0,live_CAN_sends=0)))
