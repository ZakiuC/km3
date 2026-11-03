#!/usr/bin/env python3
"""Proposed exception: exactly 603F:00 then 6041:00, one upload each, no retry.

No live action without --live-read-only-once. Execution authorization is held by
parent/user, not this flag. This is a diagnostic exception, never a recovery.
"""
import argparse
import datetime
import hashlib
import importlib
import json
import pathlib
import subprocess
import sys
import time

sys.dont_write_bytecode = True
P = pathlib.Path
ROOT = P('/var/tmp/km3-pmp4010-linear-20261019-c91126d7f56a/payload')
MANIFEST_SHA = '8cc82c8aa29b82d62bcd055f54a691ac781198640233b8066a9bd1423cf564d6'
EXPECTED_BOOT = 'dc67bdcd-fa50-4927-8f9b-7207359c70af'
RT_ARGV = ['/opt/km3/current/km3-rt-controller', '--config', '/etc/km3/device.json', '--require-config']
TABLE = ((0x603F, 0, 2), (0x6041, 0, 2))
REQUEST_TIMEOUT = 0.25
TOTAL_TIMEOUT = 1.0


def emit(event, **data):
    print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         event=event, **data), ensure_ascii=False), flush=True)


class Guard:
    """Exact pending upload-only gate; every rejection permanently closes its gate."""
    def __init__(self, bus, module, clock=time.monotonic):
        self.bus, self.module, self.clock = bus, module, clock
        self.pending, self.sent, self.count, self.cursor = None, False, 0, 0
        self.failed = False
        self.start = clock()

    def reject(self, message):
        self.failed = True
        raise RuntimeError(message)

    def deadline(self):
        if self.failed or self.clock() - self.start >= TOTAL_TIMEOUT:
            self.reject('terminal gate or 1s total transaction deadline')

    def begin(self, row):
        self.deadline()
        if self.pending is not None or self.cursor >= 2 or row != TABLE[self.cursor]:
            self.reject('only ordered 603F:00 then 6041:00 is allowed')
        self.pending, self.sent = row, False
        self.request_end = min(self.start + TOTAL_TIMEOUT, self.clock() + REQUEST_TIMEOUT)

    def send(self, frame):
        self.deadline()
        d = frame.data
        if (self.count >= 2 or self.pending is None or self.sent or
                frame.arbitration_id != 0x601 or frame.extended or frame.remote or
                frame.frame_type != self.module.FRAME_CLASSIC or len(d) != 8 or
                d[0] != 0x40 or d[4:] != bytes(4) or
                (d[1] | d[2] << 8, d[3], 2) != self.pending):
            self.reject('upload-only exact-pending send guard rejected frame')
        remaining = self.request_end - self.clock()
        if remaining <= 0:
            self.failed = True
            raise TimeoutError('send deadline; no request sent')
        self.sent = True
        self.count += 1  # Count attempts before handing bytes to transport.
        emit('tx', cob_id='601', raw_hex=d.hex(), attempt=self.count)
        try:
            self.bus._sock.settimeout(remaining)  # Bound send(), including first request.
            self.bus.send(frame)
        except BaseException:
            self.failed = True
            raise

    def recv(self):
        self.deadline()
        if self.pending is None or not self.sent:
            self.reject('receive without one pending sent request')
        remaining = self.request_end - self.clock()
        if remaining <= 0:
            self.failed = True
            raise TimeoutError('request deadline; no retry or later request')
        try:
            frame = self.bus.recv(remaining)  # Exactly one receive, no wait/retry loop.
        except BaseException:
            self.failed = True
            raise
        self.deadline()
        if frame is None:
            self.failed = True
            raise TimeoutError('first receive timed out; no retry or later request')
        d = frame.data
        emit('rx', cob_id=f'{frame.arbitration_id:03X}', raw_hex=d.hex(),
             extended=frame.extended, remote=frame.remote, frame_type=frame.frame_type)
        if (frame.arbitration_id != 0x581 or frame.extended or frame.remote or
                frame.frame_type != self.module.FRAME_CLASSIC or len(d) != 8 or
                d[0] != 0x4B or (d[1] | d[2] << 8, d[3], 2) != self.pending):
            self.reject('response must be node1 matching index/sub expedited uint16')
        if self.clock() > self.request_end:
            self.failed = True
            raise TimeoutError('response arrived beyond deadline')
        value = int.from_bytes(d[4:6], 'little', signed=False)
        self.cursor += 1
        self.pending, self.sent = None, False
        return value, d[4:6].hex()


def read_two(bus, module, clock=time.monotonic):
    guard = Guard(bus, module, clock)
    values = {}
    try:
        bus.open()
        for row in TABLE:
            guard.begin(row)
            index, sub, _ = row
            frame = module.Frame(0x601, bytes((0x40, index & 255, index >> 8, sub, 0, 0, 0, 0)))
            guard.send(frame)
            value, raw = guard.recv()
            values[f'{index:04X}:{sub:02X}'] = value
            emit('value', index=f'{index:04X}', sub=sub, type='uint16', value=value,
                 hex=f'0x{value:04X}', raw_hex=raw)
        emit('complete', values=values, requests=guard.count, writes=0, retries=0,
             recovery_operations=0, meaning='historical read only; no stop or recovery confirmation')
        return values
    except BaseException as exc:
        guard.failed = True
        emit('failed', exception=type(exc).__name__, message=str(exc),
             attempted_requests=guard.count, later_requests=0, retries=0, recovery_operations=0)
        raise
    finally:
        bus.close()  # Only cleanup: no write, stop command, extra read or recovery.


def pinned_dependencies():
    assert ROOT.resolve(strict=True) == ROOT, 'payload root changed or symlinked'
    raw = (ROOT / 'payload-manifest.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA, 'approved manifest changed'
    manifest = json.loads(raw)
    for item in manifest['files']:
        p = ROOT / item['path']
        assert p.resolve(strict=True).is_relative_to(ROOT), 'manifest path escapes payload'
        if 'symlink' in item:
            assert p.is_symlink() and p.readlink().as_posix() == item['symlink']
        else:
            assert hashlib.sha256(p.read_bytes()).hexdigest() == item['sha256'], str(p)
    emit('pinned_payload', root=str(ROOT), manifest_sha256=MANIFEST_SHA,
         verified_entries=len(manifest['files']))
    sys.path.insert(0, str(ROOT / 'hwtui'))
    canopen = importlib.import_module('canopen')
    canbus = importlib.import_module('canbus')
    for module in (canopen, canbus):
        assert P(module.__file__).resolve().parent == ROOT / 'hwtui', 'dependency escaped package'
    return canopen, canbus


def passive_preflight():
    model = P('/proc/device-tree/model').read_bytes().rstrip(b'\0').decode()
    boot = P('/proc/sys/kernel/random/boot_id').read_text().strip()
    hostname = P('/proc/sys/kernel/hostname').read_text().strip()
    processes, maintenance, rt = [], [], []
    for p in P('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv = [x.decode(errors='replace') for x in (p / 'cmdline').read_bytes().split(b'\0') if x]
        except OSError:
            continue
        if not argv:
            continue
        joined = ' '.join(argv)
        # Own stdin Python invocation has no package/maintenance words.
        if any(word in joined for word in ('pmp4010', 'candump', 'cansend', 'cangen', 'canplayer',
                                           'drive_probe', 'pty_runner', 'mock_app', 'diagnose.py',
                                           'km3-rt-controller', 'hwtools', 'canopen')):
            item = dict(pid=int(p.name), argv=argv)
            processes.append(item)
            if argv == RT_ARGV and int(p.name) == 600:
                rt.append(item)
            else:
                maintenance.append(item)
    receive_lists = {p.name: p.read_text() for p in P('/proc/net/can').glob('rcvlist*')}
    occupied = any(line.strip() and not line.startswith('receive list') and 'no entry' not in line
                   for text in receive_lists.values() for line in text.splitlines())
    argv = ['ip', '-details', '-statistics', '-json', 'link', 'show', 'can1']
    result = subprocess.run(argv, capture_output=True, text=True, timeout=3)
    interface = json.loads(result.stdout) if result.returncode == 0 else None
    emit('passive_before', model=model, boot_id=boot, hostname=hostname, processes=processes,
         can_receive_lists=receive_lists, interface_command=dict(argv=argv, rc=result.returncode,
         stdout=result.stdout, stderr=result.stderr), remote_writes=0, CAN_opens=0, CAN_sends=0)
    assert model == 'TL3588-EVM' and hostname == 'RK3588-Tronlong' and boot == EXPECTED_BOOT, 'identity/boot changed'
    assert not maintenance and len(rt) == 1, 'new maintenance or changed RT process; stop, do not disturb'
    assert receive_lists and not occupied, 'existing CAN receiver; stop, do not disturb'
    assert result.returncode == 0 and len(interface) == 1, 'interface state unavailable'
    link = interface[0]
    assert link['ifname'] == 'can1' and 'UP' in link['flags'], 'can1 not UP'
    assert link['linkinfo']['info_data']['state'] == 'ERROR-ACTIVE', 'CAN state changed'
    assert link['linkinfo']['info_data']['bittiming']['bitrate'] == 1000000, 'CAN bitrate changed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-read-only-once', action='store_true')
    args = parser.parse_args()
    emit('plan', interface='can1', node=1, request_id='601', response_id='581',
         requests=['40 3F 60 00 00 00 00 00', '40 41 60 00 00 00 00 00'],
         ordered_uint16_objects=['603F:00', '6041:00'], max_requests=2,
         per_request_timeout=REQUEST_TIMEOUT, total_transaction_deadline=TOTAL_TIMEOUT,
         writes=0, retries=0, recovery_operations=0, live_requested=args.live_read_only_once)
    if not args.live_read_only_once:
        emit('offline_plan_only', CAN_opens=0, CAN_sends=0)
        return 0
    try:
        module, transport = pinned_dependencies()
        passive_preflight()
        bus = transport.CanBus('can1', loopback=False, receive_own_messages=False)
        read_two(bus, module)
        return 0
    except BaseException as exc:
        emit('terminal', exception=type(exc).__name__, message=str(exc), retries=0,
             recovery_operations=0, no_further_operations=True)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == '__main__':
    sys.exit(main())
