"""Real local curses PTY, standard MockBus only; drives normal Form keystrokes.

Reproduce with retained trace/screens:
  python3 -I device/drivers/pmp4010/tools/_test_manual_pty.py --run /tmp/pmp4010-manual-pty
No consent/review is injected. All RT/OD/settings/target/action confirmations are
entered through the PTY. The child forbids real socket creation via the standard
fixture and never runs an SSH command.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def child(output):
    from contextlib import ExitStack
    from unittest import mock
    from _test_bench_support import DelayedBus, environment, requests, tool
    from _test_manual import ManualNode
    import pmp4010_manual as manual
    import pmp4010_simple as simple
    output = Path(output)
    node = ManualNode()
    bus = DelayedBus([node])
    original = manual.ManualApp
    class ObservedApp(original):
        def poll(self):
            super().poll()
            data = dict(view=self.current_view, action=self.action, busy=self.busy,
                        rt_ack=self.controller.rt_ack, od_ack=bool(self.controller.od_reason),
                        phases={str(k):v['phase'] for k,v in self.controller.proofs.items()},
                        token=self.pending['token'] if self.pending else None,
                        settings=self.controller.settings, controls=[v for i,_,v in requests(bus,True) if i==0x6040],
                        position=node.value(0x6064,signed=True),
                        recovery=self.recovery, progress=self.progress,
                        blocked=bool(self.controller.blocked))
            tmp = output / 'state.tmp'
            tmp.write_text(json.dumps(data,ensure_ascii=False))
            tmp.replace(output / 'state.json')
    with ExitStack() as stack:
        for patch in environment(bus):
            stack.enter_context(patch)
        # Explicit legacy internal UI test. Production --manual uses SimpleApp;
        # this preserves the old protocol-stage keyboard regression coverage.
        stack.enter_context(mock.patch.object(simple,'SimpleApp',ObservedApp))
        rc = tool.main(['--manual','--node-range','1','--timeout','.03','--state-timeout','.3',
                        '--move-timeout','2','--rows','40','--cols','150',
                        '--motion-log',str(output/'motion.jsonl')])
        (output/'trace.json').write_text(json.dumps(dict(rc=rc,requests=requests(bus),
            writes=requests(bus,True),controls=[v for i,_,v in requests(bus,True) if i==0x6040]),ensure_ascii=False,indent=2))
        return rc


def run_pty(output):
    import fcntl
    import pty
    import select
    import struct
    import subprocess
    import termios
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    master, slave = pty.openpty()
    fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',40,150,0,0))
    env = dict(os.environ,TERM='xterm-256color',LANG='C.UTF-8',LC_ALL='C.UTF-8')
    process = subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),'--child',str(output)],
                               stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
    os.close(slave)
    transcript = bytearray()
    checks = []
    last = {}
    def drain(delay=.02):
        if select.select([master],[],[],delay)[0]:
            try:
                transcript.extend(os.read(master,65536))
            except OSError:
                pass
    def wait(predicate,label,timeout=8):
        nonlocal last
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            drain()
            try:
                last=json.loads((output/'state.json').read_text())
            except (FileNotFoundError,json.JSONDecodeError):
                pass
            if predicate(last):
                checks.append(label)
                return last
            if process.poll() is not None:
                raise AssertionError('PTY child exited {} waiting {}: {}'.format(process.returncode,label,last))
        raise AssertionError('PTY timeout {}: {}'.format(label,last))
    def send(data):
        os.write(master,data.encode() if isinstance(data,str) else data)
    f5=b'\x1b[15~'
    def view(name):
        return wait(lambda s:s.get('view')==name and not s.get('busy'),name)
    def confirmed_action(key, action, controls):
        send(key)
        view('action')
        before=list(last['controls'])
        send(f5)
        state=view('confirm')
        assert state['controls']==before, 'review secretly wrote controlword'
        send(state['token'])
        send(f5)
        state=wait(lambda s:s.get('view')=='detail' and not s.get('busy') and s.get('controls')==controls,action)
        return state
    try:
        state=view('rt')
        assert state['controls']==[] and not state['rt_ack']
        send(f5)
        state=view('overview')
        assert state['controls']==[]
        checks.append('startup/RT confirmation/scan have zero controlword writes')
        send('\r')
        view('detail')
        send(b'\x1bOQ')  # xterm F2, normal shared Form path
        for value in ('-2000','500','364'):
            view('setting')
            send(value)
            send(f5)
            # Give the ordinary key loop one turn before the next field.
            drain(.1)
        wait(lambda s:s.get('view')=='detail' and s.get('settings',{}).get('max_displacement')==364,'three settings submitted by Form')
        send('a')
        view('od')
        send('OD mock-bench 1')
        send(f5)
        view('action')
        send('-1420')
        send(f5)
        state=view('confirm')
        assert state['controls']==[]
        send(state['token'])
        send(f5)
        expected=[0x106,0x107,0x127,0x137,0x127]
        state=wait(lambda s:s.get('phases',{}).get('1')=='submitted' and not s.get('busy'),'A submits final target only')
        assert state['controls']==expected
        checks.append('A stops at submitted, no enable or release')
        state=confirmed_action('e','E holds Halt, +13U allowed',expected+[0x12f])
        assert state['position']==-1457 and state['phases']['1']=='enabled'
        drain(.65)
        state=wait(lambda s:s.get('phases',{}).get('1')=='enabled' and not s.get('blocked'),'background monitor preserves +13U envelope')
        state=confirmed_action('g','G triggers fresh enabled ACK0/1/0',expected+[0x12f,0x2f,0x3f,0x2f])
        assert state['phases']['1']=='running'
        state=confirmed_action('x','X explicitly software quick stops',expected+[0x12f,0x2f,0x3f,0x2f,2])
        state=confirmed_action('d','D explicitly disables',expected+[0x12f,0x2f,0x3f,0x2f,2,0])
        send('q')
        end=time.monotonic()+8
        while process.poll() is None and time.monotonic()<end:
            drain(.03)
        assert process.poll()==0, 'PTY child did not exit normally'
        drain(.01)
        trace=json.loads((output/'trace.json').read_text())
        assert trace['controls']==expected+[0x12f,0x2f,0x3f,0x2f,2,0,6],trace
        checks.append('exit Shutdown0006 after operator D, exact final controlword sequence')
        summary=dict(passed=len(checks),checks=checks,controls=trace['controls'],rc=process.returncode,
                     transport='standard MockBus only; real socket creation forbidden')
        (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        print(json.dumps(summary,ensure_ascii=False))
        return summary
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(3)
        drain(.01)
        os.close(master)
        (output/'terminal.ansi').write_bytes(transcript)


class ManualPtyTests(unittest.TestCase):
    def test_real_curses_normal_operator_keys_standard_mockbus(self):
        with tempfile.TemporaryDirectory(prefix='pmp4010-manual-pty-') as output:
            result=run_pty(output)
            self.assertEqual(result['rc'],0)
            self.assertGreaterEqual(result['passed'],10)


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--child':
        raise SystemExit(child(sys.argv[2]))
    if len(sys.argv)==3 and sys.argv[1]=='--run':
        run_pty(sys.argv[2])
    else:
        unittest.main()
