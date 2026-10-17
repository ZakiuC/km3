#!/usr/bin/env python3
"""Reproduce real-curses bench screens against an isolated standard MockBus.

python3 -I tools/hwtui/verify_pmp4010_bench_pty.py --output-dir /tmp/pmp4010-bench-pty

The child patches all transport/config entry points before creating the app.
It navigates through review and confirmation; it never submits the execution
confirmation. Independent VT decoding comes from verify_pmp4010_pty.Terminal.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from verify_pmp4010_pty import Terminal, width


def mock_child(trace):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / 'device/drivers/pmp4010/tools'))
    from _test_bench_support import BenchNode, environment, mockbus, options, tool
    from pmp4010_tui import PmpApp
    node = BenchNode()
    class DelayEvidenceBus(mockbus.MockBus):
        def send(self, frame):
            if ((trace.parent / 'arm-delay').exists()
                    and not (trace.parent / 'delay-entered.json').exists()
                    and frame.data[:4] == b'\x40\x41\x60\x00'):
                # A real blocking SDO wait on the worker thread. The main
                # curses thread must still consume ? and redraw help.
                node.drop_reads.add((0x6041, 0))
                (trace.parent / 'delay-entered.json').write_text(json.dumps({'monotonic': time.monotonic(), 'delay_seconds': .8}))
                time.sleep(.8)
                (trace.parent / 'delay-released').write_text(str(time.monotonic()))
            return super().send(frame)
    bus = DelayEvidenceBus([node], loopback=False)
    with ExitStack() as stack:
        for patch in environment(bus):
            stack.enter_context(patch)
        args = options(trace.parent / 'mock-motion.jsonl')
        app = PmpApp(tool, args)
        # Budget remains an operator-specified value; the PTY sends it in the
        # settings dialog. No OD bypass or RT acknowledgment is preauthorized.
        try:
            return app.run()
        finally:
            trace.write_text(json.dumps({'transport': 'MockBus', 'closed': bus.closed,
                'requests': [{'id': f.arbitration_id, 'data': f.data.hex()} for f in bus.sent]}, indent=2), encoding='utf-8')


class Session:
    def __init__(self, out):
        self.out = out
        self.terminal = Terminal(30, 120)
        self.raw = bytearray()
        self.keys = []
        self.screens = {}
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 120, 0, 0))
        self.command = [sys.executable, '-I', str(Path(__file__).resolve()), '--mock-child', '--output-dir', str(out)]
        env = dict(os.environ, LANG='zh_CN.UTF-8', LC_ALL='zh_CN.UTF-8', TERM='xterm', ESCDELAY='25')
        env.pop('LINES', None)
        env.pop('COLUMNS', None)
        self.process = subprocess.Popen(self.command, stdin=slave, stdout=slave, stderr=slave,
                                        cwd=ROOT, env=env, start_new_session=True)
        os.close(slave)

    def read(self, duration=.15):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if not select.select([self.master], [], [], max(0, deadline-time.monotonic()))[0]:
                break
            try:
                data = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not data:
                break
            self.raw.extend(data)
            self.terminal.feed(data)
        return '\n'.join(self.terminal.lines())

    def send(self, text, name=None):
        self.keys.append(name or text)
        os.write(self.master, text.encode('utf-8'))
        return self.read(.2)

    def key(self, name):
        return self.send({'enter': '\r', 'esc': '\x1b', 'f2': '\x1bOQ', 'f5': '\x1b[15~',
                          'down': '\x1bOB', 'up': '\x1bOA'}[name], name)

    def until(self, marker, timeout=4):
        deadline = time.monotonic() + timeout
        text = '\n'.join(self.terminal.lines())
        while marker not in text and time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError('child exited while waiting for ' + marker + '\n' + text)
            text = self.read(.1)
        if marker not in text:
            raise AssertionError('screen lacks ' + marker + '\n' + text)
        return text

    def capture(self, name):
        lines = self.terminal.lines()
        if self.terminal.unknown:
            raise AssertionError('unsupported escapes: ' + repr(self.terminal.unknown))
        if not all(sum(width(c) for c in line) == 120 for line in lines):
            raise AssertionError('wide-column screen width mismatch')
        self.screens[name] = lines
        return '\n'.join(lines)

    def finish(self):
        # Cancel confirmation/action/settings before exit; no F5 is sent here.
        for _ in range(5):
            if self.process.poll() is not None:
                break
            self.key('esc')
            self.send('q')
        try:
            rc = self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=2)
            rc = self.process.returncode
        self.read(.1)
        os.close(self.master)
        (self.out / 'terminal.raw').write_bytes(self.raw)
        artwork = '\n\n'.join('=== '+name+' ===\n'+'\n'.join(lines) for name, lines in self.screens.items())+'\n'
        (self.out / 'screens.txt').write_text(artwork, encoding='utf-8')
        print(artwork, flush=True)
        return rc


def run_screens(session):
    session.until('RT 控制器可能正在占用总线')
    session.capture('startup-rt')
    session.key('f5')
    session.until('扫描完成')
    overview = session.capture('overview-1online-3offline')
    if overview.count('离线') != 3:
        raise AssertionError('main screen must contain exactly three offline slots')
    session.key('enter')
    session.until('固件 V3.3')
    session.capture('detail')
    session.send('a')
    session.until('OD 闸门')
    session.send('OD mock-bench 1')
    session.key('f5')
    session.until('安全设置 1/3')
    session.key('f5')  # explicit operator acceptance of displayed lower bound
    session.until('安全设置 2/3')
    session.key('f5')  # explicit operator acceptance of displayed upper bound
    session.until('安全设置 3/3')
    session.send('364')  # no prefilled displacement budget
    session.key('f5')
    session.until('绝对位置：实时预览')
    # Replace the one numeric input using actual terminal edit keys.
    session.send('\x1bOH' + '\x1b[3~' * 16 + '-1414', 'replace target -1414')
    preview = session.capture('action-preview')
    for marker in ('-1414', '50 U', '314 U'):
        if marker not in preview:
            raise AssertionError('action preview lacks ' + marker)
    session.key('f5')
    session.until('确认执行')
    confirmation = session.capture('confirmation')
    if 'ABSOLUTE mock-bench 1 -1414' not in confirmation:
        raise AssertionError('confirmation must freeze interface/node/absolute target')
    # First Esc cancels the confirmation editor, second cancels the review.
    session.key('esc')
    session.key('esc')
    session.until('固件 V3.3')
    (session.out / 'arm-delay').write_text('one delayed telemetry upload')
    deadline = time.monotonic() + 3
    while not (session.out / 'delay-entered.json').exists() and time.monotonic() < deadline:
        session.read(.02)
    if not (session.out / 'delay-entered.json').exists():
        raise AssertionError('delayed MockBus upload did not start')
    started = time.monotonic()
    session.send('?')
    session.until('测试台：固定四席', timeout=.4)
    latency = time.monotonic() - started
    still_waiting = not (session.out / 'delay-released').exists()
    session.capture('help-during-delayed-sdo')
    if latency >= .4 or not still_waiting:
        raise AssertionError('key response was not observed during delayed SDO')
    session.delay_proof = {'key': '?', 'latency_seconds': latency,
                           'worker_delay_seconds': .8, 'worker_still_waiting': still_waiting}
    print('PASS real PTY: ? key changed screen in {:.3f}s while MockBus SDO worker still waiting (0.800s delay)'.format(latency), flush=True)
    session.key('esc')
    session.until('已隔离')
    isolated = session.capture('detail-quarantined-preserves-last-data')
    if '-1464' not in isolated or '需外部恢复' not in isolated:
        raise AssertionError('quarantine must retain old position and require external recovery')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/pmp4010-bench-pty'))
    parser.add_argument('--mock-child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.mock_child:
        return mock_child(args.output_dir / 'mock-trace.json')
    for filename in ('arm-delay', 'delay-entered.json', 'delay-released'):
        (args.output_dir / filename).unlink(missing_ok=True)
    session = Session(args.output_dir)
    failure = None
    try:
        run_screens(session)
    except (AssertionError, OSError, UnicodeError) as exc:
        failure = str(exc)
        session.screens['failure'] = session.terminal.lines()
    finally:
        rc = session.finish()
    trace_path = args.output_dir / 'mock-trace.json'
    trace = json.loads(trace_path.read_text()) if trace_path.exists() else {}
    uploads_only = bool(trace.get('requests')) and all(item['data'].startswith(('40', '60', '70')) and item['id'] != 0 for item in trace.get('requests', []))
    if not uploads_only:
        failure = failure or 'mock trace contains no requests or a non-upload/NMT request'
    if rc != 0:
        failure = failure or 'child rc=' + str(rc)
    report = {'command': session.command, 'locale': 'zh_CN.UTF-8', 'term': 'xterm',
              'exit_code': rc, 'failure': failure, 'keys': session.keys,
              'screens': list(session.screens), 'raw_bytes': len(session.raw),
              'unknown_escapes': session.terminal.unknown, 'all_requests_upload_only': uploads_only,
              'mock_closed': trace.get('closed'), 'delay_proof': getattr(session, 'delay_proof', None)}
    (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
