"""Local reset-observation proposal. No CAN I/O without explicit reviewed approval.

One native socket, exact 24-request whitelist, 3 control writes, no cleanup writes.
The parent must review this file and authorize the exact live invocation.
"""
import argparse
import datetime
import hashlib
import importlib
import json
import pathlib
import signal
import socket
import struct
import subprocess
import sys
import time

ROOT = pathlib.Path('/var/tmp/km3-pmp4010-alarm-20261019-735538978204/payload')
MANIFEST_SHA = '5072e207253957356b28f03d989efa3b798efa85b240e196060d6eb40495e40b'
LIB_SHA = '59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
BOOT = 'dc67bdcd-fa50-4927-8f9b-7207359c70af'
CONFIG_SHA = '96a15d8d28f760ab9c5b8f43f6b5ddccc667c77f89413fcd9c3201aa9ddbb205'
ENV_SHA = '9253d719d847eed56ea7ef768985c0215faf0f78dfc98838152c734a0feff407'


def emit(event, **data):
    print(json.dumps(dict(event=event, utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          monotonic_ns=time.monotonic_ns(), **data), ensure_ascii=False), flush=True)


def load_pinned(root):
    root = pathlib.Path(root)
    if root.resolve(strict=True) != root:
        raise ValueError('payload root changed or symlinked')
    raw = (root / 'payload-manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise ValueError('manifest hash changed')
    for item in json.loads(raw)['files']:
        path = root / item['path']
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError('member escapes payload')
        if 'symlink' in item:
            if not path.is_symlink() or path.readlink().as_posix() != item['symlink']:
                raise ValueError('symlink changed: ' + item['path'])
        elif hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('member hash changed: ' + item['path'])
    if hashlib.sha256((root / 'drivers/pmp4010/libpmp4010_velocity.so.1.0.0').read_bytes()).hexdigest() != LIB_SHA:
        raise ValueError('ARM library hash changed')
    # Intentionally cold direct imports; no fixtures, tool loader or lazy-module assumption.
    for name in ('canopen', 'canbus', 'canopen_consts', 'canopen_wire'):
        if name in sys.modules:
            raise ValueError('not a cold standard-layer load: ' + name)
    sys.path.insert(0, str(root / 'hwtui'))
    co = importlib.import_module('canopen')
    cb = importlib.import_module('canbus')
    origins = {}
    for name in ('canopen', 'canbus', 'canopen_wire'):
        module = sys.modules[name]
        path = pathlib.Path(module.__file__).resolve(strict=True)
        if path.parent != root / 'hwtui':
            raise ValueError('module origin changed: ' + name)
        origins[name] = str(path)
    emit('cold_load_pass', manifest_sha256=MANIFEST_SHA, library_sha256=LIB_SHA, origins=origins)
    return co, cb


def os_preflight():
    p = pathlib.Path
    assert socket.gethostname() == 'RK3588-Tronlong'
    assert p('/proc/device-tree/model').read_bytes().rstrip(b'\0') == b'TL3588-EVM'
    assert p('/proc/sys/kernel/random/boot_id').read_text().strip() == BOOT
    for name, expected in (('/etc/km3/device.json', CONFIG_SHA), ('/etc/km3/can.env', ENV_SHA)):
        assert hashlib.sha256(p(name).read_bytes()).hexdigest() == expected, name
    assert json.loads(p('/etc/km3/device.json').read_text())['device']['id'] == 'km3-test-001'
    assert p('/opt/km3/hwtools/current').resolve() == p('/opt/km3/hwtools/releases/0.1.5+zh-5e31e29')
    # Any CAN receiver or competing known diagnostic process aborts before opening a socket.
    for path in p('/proc/net/can').glob('rcvlist*'):
        for line in path.read_text().splitlines():
            if line.strip() and not ('receive list' in line or 'no entry' in line):
                raise ValueError('CAN receive list occupied: ' + line)
    for proc in p('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == __import__('os').getpid():
            continue
        try:
            cmd = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        except FileNotFoundError:
            continue
        if any(word in cmd for word in ('pmp4010', 'pty_runner', 'mock_app', 'km3-pp',
                                        'drive_probe', 'board_app', 'candump', 'cansend', 'cangen', 'canplayer')):
            raise ValueError('competing diagnostic process: ' + proc.name + ' ' + cmd)
    result = subprocess.run(['ip', '-details', '-statistics', '-json', 'link', 'show', 'can1'],
                            check=True, capture_output=True, text=True, timeout=2)
    link, = json.loads(result.stdout)
    assert link['ifname'] == 'can1' and link['mtu'] == 16 and 'UP' in link['flags']
    assert link['linkinfo']['info_data']['state'] == 'ERROR-ACTIVE'
    assert link['linkinfo']['info_data']['bittiming']['bitrate'] == 1000000
    assert all(v == 0 for v in link['linkinfo']['info_xstats'].values())
    for direction in ('rx', 'tx'):
        assert link['stats64'][direction]['errors'] == 0
        assert link['stats64'][direction]['dropped'] == 0
    emit('os_preflight_pass', boot_id=BOOT, can_link=link)


class Experiment:
    def __init__(self, sock, co, cb, clock=time.monotonic, sleep=time.sleep):
        self.sock, self.co, self.cb = sock, co, cb
        self.clock, self.sleep = clock, sleep
        self.deadline = clock() + 10.0
        self.cursor = 0
        self.samples = []
        self.bit7_may_remain_set = False
        self.high_attempted = False
        self.final_zero_ack = False

    def wait_until(self, target):
        if target >= self.deadline:
            raise TimeoutError('scheduled observation exceeds total deadline')
        if self.clock() < target:
            self.sleep(target - self.clock())
        if self.clock() >= self.deadline:
            raise TimeoutError('total deadline after wait')

    def transaction(self, operation, index, sub, size, signed=False, word=None):
        token = (operation, index, sub, size, word)
        if self.cursor >= len(SEQUENCE) or token != SEQUENCE[self.cursor]:
            raise ValueError('transaction outside exact whitelist')
        if self.clock() >= self.deadline:
            raise TimeoutError('total deadline before transaction')
        ordinal = self.cursor + 1
        if operation == 'write':
            data = bytes((0x2B, index & 255, index >> 8, sub)) + word.to_bytes(2, 'little') + bytes(2)
        else:
            data = bytes((0x40, index & 255, index >> 8, sub, 0, 0, 0, 0))
        frame = self.co.Frame(0x601, data)
        raw = self.cb.pack_can_frame(frame)
        end = min(self.deadline, self.clock() + .25)
        self.sock.settimeout(max(.000001, end - self.clock()))
        emit('tx_attempt', ordinal=ordinal, operation=operation, index=f'{index:04X}',
             subindex=sub, cob_id='601', data_hex=data.hex(), socketcan_hex=raw.hex())
        if operation == 'write' and word == 0x80:
            # A failed send may be uncertain. Only final zero ACK clears this flag.
            self.high_attempted = True
            self.bit7_may_remain_set = True
        sent = self.sock.send(raw)
        if sent != 16:
            raise ValueError('short CAN send; no later request')
        emit('tx_sent', ordinal=ordinal, bytes_sent=sent, socketcan_hex=raw.hex(), data_hex=data.hex())
        remaining = end - self.clock()
        if remaining <= 0:
            raise TimeoutError('request deadline after send')
        self.sock.settimeout(remaining)
        response = self.sock.recv(17)
        emit('rx', ordinal=ordinal, socketcan_hex=response.hex(), raw_bytes=len(response))
        if self.clock() > end:
            raise TimeoutError('request deadline after receive')
        if len(response) != 16:
            raise ValueError('malformed classic CAN length')
        can_id, dlc, data = struct.unpack('<IB3x8s', response)
        if can_id != 0x581 or dlc != 8 or data[1:4] != frame.data[1:4]:
            raise ValueError('unexpected CAN flags/ID/DLC or SDO object')
        if data[0] == 0x80:
            raise ValueError('SDO abort 0x%08X' % int.from_bytes(data[4:], 'little'))
        expected = 0x60 if operation == 'write' else {1:0x4F, 2:0x4B, 4:0x43}[size]
        if data[0] != expected:
            raise ValueError('SDO command/width mismatch')
        ack_time = self.clock()
        self.cursor += 1
        if operation == 'write':
            if self.high_attempted and word == 0:
                self.final_zero_ack = True
                self.bit7_may_remain_set = False
            emit('write_ack', ordinal=ordinal, index='6040', word=f'{word:04X}',
                 ack_monotonic_seconds=ack_time, bit7_may_remain_set=self.bit7_may_remain_set)
            return ack_time
        value = int.from_bytes(data[4:4 + size], 'little', signed=signed)
        emit('value', ordinal=ordinal, index=f'{index:04X}', subindex=sub,
             size_bytes=size, signed=signed, raw_value_hex=data[4:4+size].hex(), value=value)
        if index == 0x6041:
            if value & 0x6F != 0x40 or value & 0x08:
                raise ValueError('not disabled or Fault set; no cleanup write')
        if index == 0x603F and value not in (0, 0x2312):
            raise ValueError('new nonzero error code; no cleanup write')
        if index == 0x1001 and value != 0:
            raise ValueError('new nonzero error register; no cleanup write')
        return value

    def read(self, index, sub=0, size=2, signed=False):
        return self.transaction('read', index, sub, size, signed)

    def write(self, word):
        return self.transaction('write', 0x6040, 0, 2, word=word)

    def sample(self, phase, offset, reference):
        self.wait_until(reference + offset)
        start = self.clock()
        status = self.read(0x6041)
        code = self.read(0x603F)
        register = self.read(0x1001, size=1)
        sample = dict(phase=phase, target_offset_seconds=offset,
                      actual_start_offset_seconds=start-reference,
                      actual_end_offset_seconds=self.clock()-reference,
                      statusword=status, warning=bool(status & 0x80), fault=bool(status & 0x08),
                      error_code=code, error_register=register)
        self.samples.append(sample)
        emit('observation', **sample)

    def run(self):
        mode = self.read(0x2002, 1)
        if mode != 0: raise ValueError('initial control mode is not CiA402')
        status = self.read(0x6041)
        if not status & 0x80: raise ValueError('initial Warning is not set')
        if self.read(0x603F) != 0x2312: raise ValueError('initial code is not 2312')
        if self.read(0x1001, size=1) != 0: raise ValueError('initial error register is nonzero')
        if self.read(0x200E, 2, 4, True) != 0x03002312:
            raise ValueError('initial stall reaction differs from approved value')
        low_start = self.write(0)
        self.read(0x6041)
        # Measured from initial zero ACK. This is an experiment budget, not a
        # vendor-specified minimum reset pulse width.
        self.wait_until(low_start + .1)
        if self.read(0x2002, 1) != 0: raise ValueError('mode changed before reset high')
        high_ack = self.write(0x80)
        self.sample('high', .2, high_ack)
        self.sample('high', 1.0, high_ack)
        if self.read(0x2002, 1) != 0: raise ValueError('mode changed before final reset low')
        zero_ack = self.write(0)
        final_controlword = self.read(0x6040)
        if final_controlword != 0:
            self.bit7_may_remain_set = bool(final_controlword & 0x80)
            raise ValueError('final controlword readback is not exactly zero')
        self.sample('low', .05, zero_ack)
        self.sample('low', .5, zero_ack)
        high = [s for s in self.samples if s['phase']=='high']
        low = [s for s in self.samples if s['phase']=='low']
        if any(not s['warning'] for s in high) and any(s['warning'] for s in low):
            classification = 'warning_relatched_after_high_clear'
        elif all(not s['warning'] and not s['fault'] and s['error_register']==0 and s['error_code']==0 for s in low):
            classification = 'cleared_at_final_observation'
        elif all(not s['warning'] and not s['fault'] and s['error_register']==0 and s['error_code']==0x2312 for s in low):
            classification = 'warning_cleared_error_code_retained_not_tool_unlocked'
        elif all(s['warning'] for s in self.samples):
            classification = 'warning_unchanged_not_cleared'
        else:
            classification = 'transient_or_incomplete_clear_not_confirmed'
        emit('reset_observation_complete', classification=classification,
             requests=self.cursor, maximum_requests=24, control_writes=3, retries=0,
             samples=self.samples, final_controlword=final_controlword,
             final_zero_ack=self.final_zero_ack, bit7_may_remain_set=self.bit7_may_remain_set,
             motion_operations=0, old_short_pulse_cause_proven=False)
        return classification


R = lambda index, sub=0, size=2: ('read', index, sub, size, None)
W = lambda word: ('write', 0x6040, 0, 2, word)
SAMPLE = (R(0x6041), R(0x603F), R(0x1001, size=1))
SEQUENCE = (R(0x2002,1), R(0x6041), R(0x603F), R(0x1001,size=1), R(0x200E,2,4),
            W(0), R(0x6041), R(0x2002,1), W(0x80),
            *SAMPLE, *SAMPLE, R(0x2002,1), W(0), R(0x6040), *SAMPLE, *SAMPLE)
assert len(SEQUENCE)==24 and sum(row[0]=='write' for row in SEQUENCE)==3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--execute-reset-observe-once', action='store_true')
    modes.add_argument('--cold-load', action='store_true')
    parser.add_argument('--payload-root', type=pathlib.Path, default=ROOT)
    args = parser.parse_args()
    if not args.execute_reset_observe_once and not args.cold_load:
        emit('local_plan_only', sequence=SEQUENCE, maximum_requests=24, control_writes=3,
             CAN_opens=0, retries=0, experiment_not_vendor_minimum_pulse=True)
        return 0
    co, cb = load_pinned(args.payload_root)
    if args.cold_load: return 0
    if args.payload_root != ROOT: raise ValueError('live payload override forbidden')
    os_preflight()
    def timed_out(signum, frame): raise TimeoutError('10 second live wall-clock deadline')
    signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL,10.0)
    sock = socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW)
    experiment = Experiment(sock,co,cb)
    try:
        sock.setsockopt(101,3,0)
        sock.setsockopt(101,4,0)
        sock.setsockopt(101,1,struct.pack('=II',0x581,0xC00007FF))
        sock.setsockopt(101,2,struct.pack('=I',0x1FFFFFFF))
        sock.bind(('can1',))
        return_value = experiment.run()
    except BaseException as exc:
        emit('experiment_ended_no_cleanup', exception=type(exc).__name__, error=str(exc),
             acknowledged_requests=experiment.cursor, retries=0,
             bit7_may_remain_set=experiment.bit7_may_remain_set,
             high_attempted=experiment.high_attempted, final_zero_ack=experiment.final_zero_ack,
             samples=experiment.samples, motion_operations=0)
        raise
    finally:
        sock.close()  # Sole cleanup. A possible high bit7 is reported, never cleared here.
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        emit('ended_without_retry', error_type=type(exc).__name__, error=str(exc), retries=0)
        sys.exit(1)

