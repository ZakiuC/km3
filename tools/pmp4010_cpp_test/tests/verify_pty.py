#!/usr/bin/env python3
"""Real 80x24 PTY acceptance for the standalone C++ UI; mock plus unconfirmed real entry.
Writes raw terminal captures, operation logs, frame traces and strace evidence.
No external Python packages, CAN sockets, SSH or hardware access are used.
"""
import argparse
import atexit
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import time
import unicodedata

ASSERTIONS = 0
ACTIVE = []
USE_STRACE = True


def cleanup_sessions():
    for session in ACTIVE:
        if session.process.poll() is None:
            try:
                os.write(session.master, b'q')
                session.pump(.5)
                session.process.wait(timeout=3)
            except Exception:
                session.process.kill()
                session.process.wait()
        try:
            (session.output / (session.name + '.terminal')).write_bytes(session.raw)
        except Exception:
            pass
atexit.register(cleanup_sessions)


def check(condition, message):
    global ASSERTIONS
    ASSERTIONS += 1
    if not condition:
        raise AssertionError(message)


def decode_screen(data):
    grid=[[' ']*80 for _ in range(24)]
    row=column=0
    text=data.decode(errors='replace')
    position=0
    while position < len(text):
        if text[position] == '\x1b':
            match=re.match(r'\x1b\[([?0-9;]*)([A-Za-z])',text[position:])
            if match:
                params,command=match.groups()
                if command=='H':
                    numbers=[int(v) if v else 1 for v in params.split(';')]
                    row=(numbers[0] if numbers else 1)-1
                    column=(numbers[1] if len(numbers)>1 else 1)-1
                elif command=='K' and params=='2' and 0<=row<24:
                    grid[row]=[' ']*80
                elif command=='J' and params=='2':
                    grid=[[' ']*80 for _ in range(24)]
                position += len(match.group(0));continue
        glyph=text[position];position+=1
        if glyph=='\r':column=0;continue
        if glyph=='\n':row=min(23,row+1);continue
        if ord(glyph)<32:continue
        width=2 if unicodedata.east_asian_width(glyph) in ('W','F') else 1
        if 0<=row<24 and 0<=column<80:
            grid[row][column]=glyph
            if width==2 and column+1<80:grid[row][column+1]=''
        column+=width
    return '\n'.join(''.join(line).rstrip() for line in grid)+'\n'


class Session:
    def __init__(self, binary, output, name, extra=(), mock=True, trace_syscalls=False):
        self.output = output
        self.name = name
        self.log_path = output / (name + '.log')
        self.trace_path = output / (name + '.frames')
        self.raw = bytearray()
        self.master, self.slave = pty.openpty()
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        self.saved = termios.tcgetattr(self.slave)
        command = [str(binary), '--log', str(self.log_path)]
        if mock:
            command += ['--mock', '--mock-trace', str(self.trace_path)]
        command += list(extra)
        if trace_syscalls and USE_STRACE:
            command = ['strace', '-f', '-qq', '-o', str(output / (name + '.strace')),
                       '-e', 'trace=socket,connect,execve'] + command
        def child_setup():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        self.process = subprocess.Popen(command, stdin=self.slave, stdout=self.slave,
                                        stderr=self.slave, close_fds=True, preexec_fn=child_setup)
        ACTIVE.append(self)
        self.pump(.12)

    def pump(self, duration=.1):
        end = time.monotonic() + duration
        while time.monotonic() < end:
            ready, _, _ = select.select([self.master], [], [], min(.025, max(0, end-time.monotonic())))
            if ready:
                try:
                    data = os.read(self.master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                self.raw.extend(data)

    def log(self):
        return self.log_path.read_text(errors='replace') if self.log_path.exists() else ''

    def wait(self, marker, *, count=1, timeout=10, source='log'):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.pump(.04)
            text = self.log() if source == 'log' else self.raw.decode(errors='replace')
            if text.count(marker) >= count:
                return text
            if self.process.poll() is not None:
                break
        raise AssertionError(f'{self.name}: wait {marker!r} count={count} failed; log_tail={self.log()[-4000:]}')

    def send(self, text):
        os.write(self.master, text.encode() if isinstance(text, str) else text)
        self.pump(.08)

    def action(self, key, number=None):
        reviews = self.log().count('审阅就绪:')
        results = self.log().count('结果:')
        self.send(key + (str(number) if number is not None else '') + '\r')
        self.wait('审阅就绪:', count=reviews+1)
        check(self.log().count('结果:') == results, f'{key}: first Enter did not execute')
        self.send('\r')
        self.wait('结果:', count=results+1)
        self.pump(.48)
        check('错误:' not in self.log(), f'{key}: no action errors: {self.log()}')

    def capture(self, label):
        self.pump(.06)
        (self.output / (self.name + '-' + label + '.screen.txt')).write_text(decode_screen(self.raw))

    def finish(self, key='q', expected=0):
        if self.process.poll() is None:
            self.send(key)
        end = time.monotonic()+12
        while self.process.poll() is None and time.monotonic()<end:
            self.pump(.1)
        if self.process.poll() is None:
            self.process.kill()
            raise AssertionError(self.name + ': exit timed out')
        self.pump(.1)
        status = self.process.wait()
        (self.output / (self.name + '.terminal')).write_bytes(self.raw)
        check(status == expected, f'{self.name}: exit {status}, expected {expected}')
        check(termios.tcgetattr(self.slave) == self.saved, f'{self.name}: exact termios restored')
        check(b'\x1b[?25h\x1b[?1049l' in self.raw, f'{self.name}: cursor/alternate screen restored')
        check('终端已恢复' in self.raw.decode(errors='replace'), f'{self.name}: final cleanup report retained')
        ACTIVE.remove(self)
        os.close(self.master)
        os.close(self.slave)
        return self.trace_path.read_text() if self.trace_path.exists() else ''


def frames(text):
    result=[]
    for line in text.splitlines():
        if line.startswith('0x'):
            fields=line.split()
            result.append([int(fields[0],16), int(fields[1]), *[int(x,16) for x in fields[2:]]])
    return result


def downloads(text):
    return [f for f in frames(text) if f[2] != 0x40]


def main():
    global USE_STRACE
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-strace', action='store_true', help='sanitizer only: omit ptrace syscall audit')
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/pmp4010-cpp-pty'))
    args=parser.parse_args()
    USE_STRACE = not args.no_strace
    binary=args.binary.resolve()
    output=args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    check(binary.is_file(), 'binary exists')
    if USE_STRACE:
        check(shutil.which('strace') is not None, 'strace available for syscall evidence')

    # Help/version/argument failure must never open a socket, even before TTY validation.
    for name, options, expected in [('help',['--help'],0),('version',['--version'],0),
                                    ('bad-args',['--rpm','nan'],1),('missing-interface',[],1)]:
        audit=output/(name+'.strace')
        command=[str(binary),*options]
        if USE_STRACE:
            command=['strace','-f','-qq','-o',str(audit),'-e','trace=socket,connect,execve',*command]
        result=subprocess.run(command, stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False)
        (output/(name+'.txt')).write_bytes(result.stdout)
        check(result.returncode == expected, name+' exit')
        if USE_STRACE:
            check('socket(' not in audit.read_text() and 'connect(' not in audit.read_text(), name+' zero sockets')

    s=Session(binary,output,'default-startup',trace_syscalls=True)
    s.wait('位置 0 mm',source='screen')
    s.capture('ready')
    check('本手动测试不要求填写OD理由，OD未核对状态未改变' in s.log(), 'default mock needs no OD reason')
    check('请输入接受上述' not in s.raw.decode(errors='replace'), 'default mock has no reason prompt')
    trace=s.finish()
    check(not downloads(trace), 'startup/exit before any action is upload-only')
    if USE_STRACE:
        audit=(output/'default-startup.strace').read_text()
        check('socket(' not in audit and 'connect(' not in audit, 'full mock PTY zero socket/connect calls')
        check(audit.count('execve(') == 1, 'mock launched no subprocess')

    # A nonexistent interface is never inspected or opened before session Enter.
    # Exit without confirming; this branch must not create the worker/factory.
    s=Session(binary,output,'real-unconfirmed',mock=False,
              extra=['--interface','pmp-no-can-od'],trace_syscalls=True)
    s.wait('Enter确认RT及其他CAN控制程序已停、独立停机已就绪；q退出')
    s.capture('session-confirm')
    s.send('abcx d')
    check('会话开始:' not in s.log(), 'unconfirmed real entry has no worker startup')
    check('错误:' not in s.log(), 'unconfirmed real entry never inspects nonexistent interface')
    trace=s.finish()
    check(not trace and '会话开始:' not in s.log(), 'quit before confirmation never starts CAN worker')
    if USE_STRACE:
        audit=(output/'real-unconfirmed.strace').read_text()
        check('socket(' not in audit and 'connect(' not in audit, 'unconfirmed real entry has zero sockets')
        check(audit.count('execve(') == 1, 'unconfirmed real entry launched no factory subprocess')

    s=Session(binary,output,'unconfirmed')
    s.wait('位置 0 mm',source='screen')
    s.send('r1\r')
    s.wait('审阅就绪:')
    s.capture('review')
    check('第二次Enter已确认执行:' not in s.log(), 'review requires independent second Enter')
    trace=s.finish()
    check(not downloads(trace), 'review plus exit sends zero downloads')

    s=Session(binary,output,'cancel-invalid')
    s.wait('位置 0 mm',source='screen')
    s.send('rnan\r')
    s.wait('输入无效')
    s.send('\x1b')
    s.send('p2\r')
    s.wait('审阅就绪:')
    s.send('\x1b')
    s.send('f\x1b')
    trace=s.finish()
    check(not downloads(trace), 'invalid input, Esc draft/review never downloads')

    s=Session(binary,output,'actions')
    s.wait('位置 0 mm',source='screen')
    s.action('z')
    s.action('s',12)
    s.action('a',.125)
    s.action('c',4)
    s.action('e')
    s.action('r',.25)
    s.action('j',-.125)
    s.action('p',.5)
    s.action('f')
    s.action('b')
    s.action('z')
    s.capture('software-zero')
    check('软件零点 16384' in decode_screen(s.raw), 'software zero captures nonzero raw feedback')
    # Stop remains active during numerical input; nothing entered is submitted.
    before=s.log().count('结果:')
    s.send('r999x')
    s.wait('停止请求已接收')
    s.wait('结果:',count=before+1)
    s.pump(.48)
    before=s.log().count('结果:')
    s.send('p888 ')
    s.wait('结果:',count=before+1)
    s.pump(.48)
    before=s.log().count('结果:')
    s.send('p456d')
    s.wait('结果:',count=before+1)
    s.pump(.48)
    s.send('t\r')
    s.wait('仅可人工复位')
    trace=s.finish()
    writes=downloads(trace)
    check(bool(writes), 'confirmed actions reach real Controller/MockCanBus downloads')
    check(all(not (f[3] == 0x7c and f[4] == 0x60) for f in writes), 'software zero never writes 607C')
    targets=[int.from_bytes(bytes(f[6:10]),'little',signed=True) for f in writes if f[3:6] == [0x7a,0x60,0]]
    check(8192 in targets and 16384 in targets and 20480 in targets,
          f'relative/absolute/f-b raw targets reflect 4mm/rev: {targets}')
    check('电气失能确认=是' in s.log() and '机械停稳未确认' in s.log(), 'disable and mechanical state differentiated')

    s=Session(binary,output,'reset-warning',extra=['--mock-warning-2312'])
    s.wait('603F=0x2312')
    s.action('t')
    s.wait('603F=0x0000')
    trace=s.finish()
    control=[int.from_bytes(bytes(f[6:8]),'little') for f in downloads(trace) if f[3:6] == [0x40,0x60,0]]
    check(control == [0,0x80,0], f'manual reset exactly 0/0080/0, no autoenable: {control}')

    s=Session(binary,output,'stop-during-review',extra=['--mock-delay-ms','35'])
    s.wait('位置 0 mm',source='screen')
    s.send('r1\r')
    s.wait('正在读取并审阅',source='screen')
    started=time.monotonic()
    s.send('x')
    s.wait('停止请求已接收',timeout=1)
    accepted=time.monotonic()-started
    s.wait('结果:',timeout=2)
    stopped=time.monotonic()-started
    check(accepted<.25, f'busy review stop key accepted in {accepted:.3f}s')
    check(stopped<1, f'busy review stop cleanup completes in {stopped:.3f}s')
    s.capture('stopped')
    trace=s.finish()
    (output/'stop-latency.json').write_text(json.dumps({'key_accepted_seconds':accepted,'stop_result_seconds':stopped},indent=2)+'\n')
    check('第二次Enter已确认执行:' not in s.log(), 'cancelled review never executes position')

    s=Session(binary,output,'escape-executing',extra=['--mock-delay-ms','2'])
    s.wait('位置 0 mm',source='screen')
    s.send('e\r')
    s.wait('审阅就绪:')
    s.send('\r')
    s.wait('后台执行中',source='screen')
    s.send('\x1b')
    s.wait('后台执行中；请用Space/x停止或d失能')
    s.wait('结果:')
    s.pump(.5)
    s.send('a0.2\r')
    s.wait('审阅就绪:',count=2)
    trace=s.finish()
    check('已取消输入/审阅' not in s.log(), 'Esc during execution does not invalidate pending result')

    # SIGINT/SIGTERM and keyboard EOF each go through join and terminal restoration.
    for name,sig,expected in [('sigint',signal.SIGINT,130),('sigterm',signal.SIGTERM,143),('eof',None,0)]:
        s=Session(binary,output,name)
        s.wait('位置 0 mm',source='screen')
        s.send('r123')
        if sig is not None:
            os.kill(s.process.pid,sig)
            s.wait('收到信号')
            s.finish(key='',expected=expected)
        else:
            s.finish(key=b'\x04',expected=expected)

    s=Session(binary,output,'isolated',extra=['--mock-timeout-after','1','--sdo-timeout-ms','80'])
    s.wait('节点已隔离')
    s.pump(.9)
    trace=s.finish(expected=1)
    check(len(frames(trace)) == 1, f'isolated node never polled/retried/cleanup-written: {trace}')

    s=Session(binary,output,'partial-alarm',extra=['--mock-partial-alarm-timeout','--sdo-timeout-ms','80'])
    s.wait('603F=0x0000')
    s.wait('节点已隔离')
    s.wait('603F 0x2312',source='screen')
    s.capture('partial')
    screen=decode_screen(s.raw)
    check('603F 0x2312' in screen and '样本不完整/已失效' in screen,
          'partial failure displays current 2312 with invalid marker')
    check('位置 -- mm' in screen and '速度 -- rpm' in screen,
          'incomplete calibration never reuses stale physical feedback')
    check('603F=0x2312' in s.log() and '不完整反馈:' in s.log(), 'partial fault persisted with raw error')
    trace=s.finish(expected=1)
    check(not downloads(trace), 'partial snapshot isolation never downloads cleanup')

    s=Session(binary,output,'log-failure',extra=['--log','/dev/full'])
    s.send('e\r\r')
    s.wait('操作日志写入失败',source='screen')
    trace=s.finish(key='',expected=1)
    check(not downloads(trace), 'real /dev/full startup log failure prevents enable/motion downloads')
    check('非通信隔离' in s.raw.decode(errors='replace'), 'logging failure is not mislabeled as communication isolation')

    report={'assertions'  :ASSERTIONS,'status':'passed','scope':'local PTY + protocol mock only; no hardware acceptance',
            'output_dir':str(output),'syscall_audit':USE_STRACE}
    (output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    main()
