#!/usr/bin/env python3
"""Read-only, no-bus Python/native loading and exact mathematics smoke check.

Run: python3 -I -B /explicit/extracted/package/load_smoke.py
Host logic check only: add --library /absolute/host/libpmp4010_velocity.so
No CLI main, discovery, TUI, device configuration, service or CAN operation runs.
"""
import argparse
import ctypes
from fractions import Fraction
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
ATTEMPTS = []


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def audit(event, args):
    # Raise before the underlying operation; any attempt also fails final checks.
    if event in ('socket.__new__', 'socket.connect', 'socket.bind',
                 'subprocess.Popen', 'os.system', 'os.fork', 'os.forkpty',
                 'os.posix_spawn', 'os.exec', 'pty.spawn'):
        ATTEMPTS.append(event)
        raise RuntimeError('load smoke forbids I/O operation: ' + event)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path,
                        help='Explicit absolute native library; host logic check override only')
    parser.add_argument('--check-missing-library', action='store_true')
    args = parser.parse_args()
    sys.addaudithook(audit)
    manifest = json.loads((ROOT / 'payload-manifest.json').read_text())
    for item in manifest['files']:
        path = ROOT / item['path']
        if 'symlink' in item:
            check(path.is_symlink() and os.readlink(path) == item['symlink'],
                  'symlink mismatch: ' + str(path))
        else:
            check(path.is_file() and digest(path) == item['sha256'],
                  'payload SHA256 mismatch: ' + str(path))
    print('payload_manifest=PASS source_head=' + manifest['source_head'], flush=True)
    sys.path[:0] = [str(ROOT / 'drivers/pmp4010'), str(ROOT / 'hwtui'), str(ROOT)]
    names = ('canopen_consts', 'canopen_wire', 'canopen', 'canbus', 'mockbus',
             'tui', 'discover', 'hwtui.canopen', 'hwtui.tui', 'hwtui.discover',
             'pmp4010_velocity', 'pmp4010_params', 'pmp4010_position',
             'pmp4010_bench', 'pmp4010_tui', 'pmp4010', 'pmp4010_manual')
    for name in names:
        module = importlib.import_module(name)
        check(Path(module.__file__).resolve().is_relative_to(ROOT),
              'import escaped package: ' + name)
        print('import=' + name + ' path=' + str(Path(module.__file__).resolve()), flush=True)
    velocity = sys.modules['pmp4010_velocity']
    # For board mode explicitly ignore inherited environment selection. This
    # tests the library delivered alongside the scripts, not a different release.
    os.environ.pop(velocity.LIBRARY_ENV, None)
    if args.library:
        check(args.library.is_absolute(), '--library must be absolute')
        os.environ[velocity.LIBRARY_ENV] = str(args.library)
    library = velocity.require_velocity_library()
    realpath = Path(library._name).resolve(strict=True)
    expected = (args.library if args.library else ROOT / 'drivers/pmp4010/libpmp4010_velocity.so').resolve(strict=True)
    check(realpath == expected, 'loaded wrong library')
    check(library.pmp4010_velocity_abi_version() == 1, 'native ABI is not 1')
    print(json.dumps({'library_realpath': str(realpath), 'library_sha256': digest(realpath),
                      'abi': 1, 'machine': platform.machine(), 'python': platform.python_version(),
                      'mode': 'explicit-library-logic-check' if args.library else 'packaged-library'},
                     ensure_ascii=False), flush=True)
    vectors = json.loads((ROOT / 'drivers/pmp4010/velocity_vectors.json').read_text())['vectors']
    native_count = python_count = integer_count = 0
    for v in vectors:
        num, den = ctypes.create_string_buffer(b'UNCHANGED', 128), ctypes.create_string_buffer(b'UNCHANGED', 128)
        status = library.pmp4010_velocity_convert(v['direction'], v['input'][0].encode('ascii'),
                    v['input'][1].encode('ascii'), *v['calibration'], num, len(num), den, len(den))
        check(status == v.get('error', 0), 'native error mismatch: ' + v['name'])
        if status:
            check(num.value == den.value == b'UNCHANGED', 'native failure mutated output')
        else:
            check([num.value.decode(), den.value.decode()] == v['expected'], 'native exact mismatch: ' + v['name'])
            if 'int32_error' in v:
                out = ctypes.c_int32(123)
                istatus = library.pmp4010_velocity_require_int32(num.value, den.value, ctypes.byref(out))
                check(istatus == v['int32_error'], 'native int32 error mismatch: ' + v['name'])
                check(out.value == (123 if istatus else int(v['expected'][0])), 'native int32 output mismatch')
                integer_count += 1
        native_count += 1
        cal = {'values': v['calibration']}
        value = Fraction(*map(int, v['input']))
        convert = velocity.unit_to_velocity if v['direction'] == 0 else velocity.velocity_to_unit
        try:
            result = convert(value, cal)
        except ValueError:
            check(bool(v.get('error')), 'Python unexpectedly rejected: ' + v['name'])
        else:
            check(not v.get('error'), 'Python accepted invalid vector: ' + v['name'])
            check(result == Fraction(*map(int, v['expected'])), 'Python exact mismatch: ' + v['name'])
            if 'int32_error' in v:
                try:
                    integer = velocity.velocity_to_int32(value, cal)
                except ValueError:
                    check(bool(v['int32_error']), 'Python unexpectedly rejected int32: ' + v['name'])
                else:
                    check(not v['int32_error'] and integer == int(v['expected'][0]), 'Python int32 mismatch')
        python_count += 1
        print('vector=PASS ' + v['name'] + ' native_status=' + str(status) +
              (' exact=' + '/'.join(v['expected']) if not status else ''), flush=True)
    if args.check_missing_library:
        missing = ROOT / '__intentionally_missing_velocity__.so'
        check(not missing.exists(), 'missing-library test path exists')
        os.environ[velocity.LIBRARY_ENV] = str(missing)
        try:
            velocity.require_velocity_library()
        except ValueError as exc:
            print('missing_library=EXPECTED_REJECTION ' + str(exc), flush=True)
        else:
            raise RuntimeError('missing library unexpectedly accepted')
    check(not ATTEMPTS, 'forbidden operations were attempted: ' + repr(ATTEMPTS))
    print(json.dumps({'result': 'PASS', 'native_vectors': native_count, 'python_vectors': python_count,
                      'native_int32_vectors': integer_count, 'forbidden_io_attempts': len(ATTEMPTS),
                      'hardware_acceptance': False}), flush=True)


if __name__ == '__main__':
    main()
