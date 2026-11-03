"""Frozen local proposal. No CAN I/O without --execute-readonly-once.

One native socket, uploads only, no SdoClient/recovery/retry/control writes.
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
# Sources: local E06 manual sections 9.1/9.2 and 10 OD tables.
# 6073 retained as raw U16 because manual unit descriptions conflict.
# 1003 slots retained without a newest/oldest inference.
READS = (
    (0x6041, 0, 2, False, 'disabled_guard'),
    (0x2002, 1, 2, False, 'control_mode'),
    (0x603F, 0, 2, False, 'error_before'),
    (0x1001, 0, 1, False, 'error_register'),
    (0x200E, 2, 4, True, 'stall_reaction_raw'),
    (0x2000, 9, 2, False, 'rated_current_raw'),
    (0x2000, 10, 2, False, 'max_current_raw'),
    (0x2000, 11, 2, False, 'max_current_duration_raw'),
    (0x6072, 0, 2, False, 'max_torque_raw'),
    (0x6073, 0, 2, False, 'current_limit_raw_unit_unresolved'),
    (0x2007, 10, 2, False, 'positive_torque_limit_raw'),
    (0x2007, 11, 2, False, 'negative_torque_limit_raw'),
    (0x2002, 4, 2, False, 'brake_pwm_raw'),
    (0x2004, 5, 2, False, 'do3_function_raw'),
    (0x6086, 0, 2, True, 'profile_ramp_type'),
    (0x60A4, 1, 4, False, 'profile_acceleration_jerk_raw'),
    (0x60A4, 2, 4, False, 'profile_deceleration_jerk_raw'),
    (0x1003, 0, 1, False, 'history_count'),
    (0x1003, 1, 4, False, 'history_slot_01_raw'),
    (0x1003, 2, 4, False, 'history_slot_02_raw'),
    (0x603F, 0, 2, False, 'error_after'),
    (0x6041, 0, 2, False, 'status_after'),
)


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


def probe(sock, co, cb, monotonic=time.monotonic, sleep=time.sleep):
    deadline = monotonic() + 10.0
    values = []
    for ordinal, (index, sub, size, signed, label) in enumerate(READS, 1):
        if label == 'error_after':
            if monotonic() + 0.5 >= deadline:
                raise TimeoutError('total deadline before final delay')
            sleep(0.5)
        if monotonic() >= deadline:
            raise TimeoutError('total deadline')
        request = co.Frame(0x601, bytes((0x40, index & 255, index >> 8, sub, 0, 0, 0, 0)))
        raw = cb.pack_can_frame(request)
        request_deadline = min(deadline, monotonic() + 0.250)
        sock.settimeout(max(0.000001, request_deadline - monotonic()))
        emit('tx_attempt', ordinal=ordinal, index=f'{index:04X}', subindex=sub,
             cob_id='601', data_hex=request.data.hex(), socketcan_hex=raw.hex())
        sent = sock.send(raw)
        if sent != 16:
            raise ValueError('short CAN send')
        emit('tx_sent', ordinal=ordinal, bytes_sent=sent,
             cob_id='601', data_hex=request.data.hex(), socketcan_hex=raw.hex())
        remaining = request_deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError('per-request deadline after send')
        sock.settimeout(remaining)
        response = sock.recv(17)
        emit('rx', ordinal=ordinal, socketcan_hex=response.hex(), raw_bytes=len(response))
        if monotonic() > request_deadline:
            raise TimeoutError('per-request deadline after receive')
        if len(response) != 16:
            raise ValueError('malformed classic CAN size')
        can_id, dlc, data = struct.unpack('<IB3x8s', response)
        if can_id != 0x581 or dlc != 8:
            raise ValueError('unexpected CAN ID/flags/DLC')
        if data[1:4] != request.data[1:4]:
            raise ValueError('unexpected SDO object')
        if data[0] == 0x80:
            raise ValueError('SDO abort 0x%08X' % int.from_bytes(data[4:8], 'little'))
        if data[0] != {1: 0x4F, 2: 0x4B, 4: 0x43}[size]:
            raise ValueError('SDO expedited width/command mismatch')
        value = int.from_bytes(data[4:4 + size], 'little', signed=signed)
        item = dict(ordinal=ordinal, label=label, index=f'{index:04X}', subindex=sub,
                    size_bytes=size, signed=signed, raw_value_hex=data[4:4 + size].hex(), value=value)
        values.append(item)
        emit('value', **item)
        if label in ('disabled_guard', 'status_after') and value & 0x6F != 0x40:
            raise ValueError('drive not switch-on-disabled; no control action allowed')
    emit('read_only_complete', reads=len(values), maximum_requests=len(READS),
         device_writes=0, retries=0, final_delay_seconds=0.5, values=values)
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute-readonly-once', action='store_true')
    mode.add_argument('--cold-load', action='store_true')
    parser.add_argument('--payload-root', type=pathlib.Path, default=ROOT)
    args = parser.parse_args()
    if not args.execute_readonly_once and not args.cold_load:
        emit('plan_only', interface='can1', bitrate=1000000, node=1, reads=READS,
             maximum_requests=len(READS), request_timeout_seconds=0.25, total_seconds=10,
             writes=0, retries=0, CAN_opens=0)
        return 0
    co, cb = load_pinned(args.payload_root)
    if args.cold_load:
        return 0
    if args.payload_root != ROOT:
        raise ValueError('live root override forbidden')
    os_preflight()
    def timed_out(signum, frame):
        raise TimeoutError('10 second live wall-clock deadline')
    signal.signal(signal.SIGALRM, timed_out)
    signal.setitimer(signal.ITIMER_REAL, 10.0)
    sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    try:
        sock.setsockopt(101, 3, 0)  # CAN_RAW_LOOPBACK: disabled.
        sock.setsockopt(101, 4, 0)  # CAN_RAW_RECV_OWN_MSGS: disabled.
        sock.setsockopt(101, 1, struct.pack('=II', 0x581, 0xFFFFFFFF))
        sock.setsockopt(101, 2, struct.pack('=I', 0x1FFFFFFF))  # Fail on received error frame.
        sock.bind(('can1',))
        probe(sock, co, cb)
    finally:
        sock.close()  # Sole cleanup action: no uploads, writes, client recreation or recovery.
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit('ended_without_retry', error_type=type(exc).__name__, error=str(exc),
             device_writes=0, retries=0)
        sys.exit(1)
