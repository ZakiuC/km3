"""Actual production --manual curses entry, real keys, standard MockBus only.

python3 -I device/drivers/pmp4010/tools/_test_simple_pty.py --run /tmp/pmp4010-simple-pty
No action/controller method is mocked. socket.socket is hard forbidden.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))


def publish_snapshot(output, data, screen=None):
    """Commit a complete screen before making its observer state visible.

    The PTY parent can react at state.json's atomic rename immediately.
    Publishing state first allowed it to archive a screen not yet written.
    """
    output=Path(output)
    if screen is not None:
        screen_tmp=output/'screen.tmp'
        screen_tmp.write_text(screen)
        screen_tmp.replace(output/(data['view']+'-80x24.txt'))
    tmp=output/'state.tmp'
    tmp.write_text(json.dumps(data,ensure_ascii=False))
    tmp.replace(output/'state.json')


def child(output, linear=False):
    from contextlib import ExitStack
    from unittest import mock
    from _test_bench_support import DelayedBus, environment, requests, tool
    from _test_manual import EnabledEdgeNode
    import pmp4010_simple as simple
    output=Path(output)
    class MeasuringNode(EnabledEdgeNode):
        def handle(self, frame, now):
            # Accelerated mock mechanics keeps >1-degree measuring tests
            # bounded. Protocol replies/guards/controller remain production.
            if frame.data[:4] == bytes.fromhex('40646000') and self.trajectory_target is not None:
                current=self.value(0x6064,signed=True)
                delta=self.trajectory_target-current
                current+=min(100,abs(delta))*(1 if delta>0 else -1)
                self.set_value(0x6064,current)
            return super().handle(frame,now)
    node=MeasuringNode() if linear else EnabledEdgeNode()
    node.require_enabled_target=True  # Explicit test hypothesis, not firmware fact.
    bus=DelayedBus([node])
    original=simple.SimpleApp
    class ObservedApp(original):
        def poll(self):
            super().poll()
            values=requests(bus,True)
            data=dict(view=self.current_view,busy=self.busy,refresh_busy=self.refresh_worker is not None,
                draft=self.draft,edit=self.edit_kind,action=self.action,
                rt_ack=self.controller.rt_ack,od_ack=bool(self.controller.od_reason),
                next_velocity=self.controller.next_velocity,
                phases={str(k):v['phase'] for k,v in self.controller.proofs.items()},
                target=self.pending.get('target') if self.pending else None,
                position=node.value(0x6064,signed=True),origin=self.controller.origin,
                zero=self.controller.software_zero,coordinate_revision=self.controller.coordinate_revision,
                coefficient=str(self.controller.mm_per_motor_revolution),linear=self.controller.linear,
                proof_targets={str(k):v['target'] for k,v in self.controller.proofs.items()},
                proof_starts={str(k):v['start'] for k,v in self.controller.proofs.items()},
                settings=self.controller.settings,blocked=bool(self.controller.blocked),
                controls=[v for i,_,v in values if i==0x6040],
                requests=len(bus.sent),writes=len(values),progress=self.progress)
            screen=None
            if self.current_view in ('main','confirm','authorize','help','edit'):
                grid=self.build()
                screen='\n'.join(grid.to_lines())
            publish_snapshot(output,data,screen)
    with ExitStack() as stack:
        for patch in environment(bus):stack.enter_context(patch)
        stack.enter_context(mock.patch.object(simple,'SimpleApp',ObservedApp))
        rc=tool.main(['--manual']+(['--manual-linear'] if linear else [])+['--timeout','.03','--state-timeout','.3','--move-timeout','2',
            '--rows','24','--cols','80','--motion-log',str(output/'motion.jsonl')])
        (output/'trace.json').write_text(json.dumps(dict(rc=rc,requests=requests(bus),writes=requests(bus,True),
            controls=[v for i,_,v in requests(bus,True) if i==0x6040],
            status_reads=node.status_reads,enabled_target_writes=node.enabled_target_writes,
            enabled_edges=node.enabled_edges),ensure_ascii=False,indent=2))
        return rc


def run_pty(output, linear=False):
    import fcntl
    import pty
    import select
    import struct
    import subprocess
    import termios
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    master,slave=pty.openpty()
    fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,0,0))
    env=dict(os.environ,TERM='xterm-256color',LANG='C.UTF-8',LC_ALL='C.UTF-8')
    process=subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),'--child-linear' if linear else '--child',str(output)],
        stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
    os.close(slave)
    transcript=bytearray()
    checks=[]
    last={}
    def drain(delay=.02):
        if select.select([master],[],[],delay)[0]:
            try:transcript.extend(os.read(master,65536))
            except OSError:pass
    def wait(predicate,label,timeout=10):
        nonlocal last
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            drain()
            try:last=json.loads((output/'state.json').read_text())
            except (FileNotFoundError,json.JSONDecodeError):pass
            if predicate(last):
                checks.append(label)
                return last
            if process.poll() is not None:
                raise AssertionError('PTY exit {} waiting {}: {}'.format(process.returncode,label,last))
        raise AssertionError('PTY timeout {}: {}'.format(label,last))
    def send(data):os.write(master,data.encode() if isinstance(data,str) else data)
    def view(name):return wait(lambda s:s.get('view')==name and not s.get('busy'),name)
    def confirm(key,phase):
        before=list(last['controls'])
        send(key)
        state=view('confirm')
        assert state['controls']==before,'review wrote controlword'
        target=state['target']
        send('\r')
        state=wait(lambda s:s.get('phases',{}).get('1')==phase and not s.get('busy') and s.get('view')=='main',key+' confirmed')
        if phase=='running':
            state=wait(lambda s:s.get('position')==target and not s.get('busy'),key+' actual feedback reached frozen target')
        return state,target
    def stop(key=' '):
        count=len(last['controls'])
        send(key)
        return wait(lambda s:len(s.get('controls',[]))>count and s.get('controls',[])[-1:]==[2] and not s.get('busy'),'instant stop '+repr(key))
    try:
        state=view('rt')
        assert not state['rt_ack'] and state['requests']==0
        send('\r')
        state=view('authorize')
        assert state['writes']==0 and not state['od_ack']
        checks.append('connection/RT consent/scan upload only, default node1')
        send('\r')
        state=view('main')
        assert state['origin']==-1470 and state['writes']==0
        assert state['next_velocity']==1000,'production --manual default must be 1000U/s'
        if linear:
            assert state['linear'] and state['coefficient']=='None'
            limits=dict(state['settings'])
            send('f')
            wait(lambda s:not s.get('busy') and '换算未设置' in s.get('progress',''),'unset coefficient refuses f')
            assert last['writes']==0
            revision=last['coordinate_revision']
            send('z')
            wait(lambda s:s.get('coordinate_revision',0)>revision and not s.get('busy'),'unset coefficient software zero capture')
            assert last['writes']==0 and last['settings']==limits
            send('c')
            view('edit')
            send('360\r')
            state=wait(lambda s:s.get('coefficient')=='360' and not s.get('busy'),'c positive exact mm/rev conversion')
            assert state['writes']==0
            send('s')
            view('edit')
            assert '速度 mm/s' in (output/'edit-80x24.txt').read_text()
            (output/'edit-80x24.txt').replace(output/'linear-speed-edit-80x24.txt')
            send('1\r')
            state=wait(lambda s:s.get('next_velocity')==364 and not s.get('busy'),'s1mm/s quantizes364U/s at mock360mm/rev')
            assert state['writes']==0
            assert '实际 0.999756 mm/s（364 U/s）' in (output/'main-80x24.txt').read_text()
            (output/'main-80x24.txt').replace(output/'linear-speed-main-80x24.txt')
            checks.append('linear s input and defaultU/s draft display mm/s; no velocity download before confirmation')
            def move(key,text):
                before=last['writes']
                start=last['position']
                zero=last['zero']
                send(key)
                view('edit')
                send(text+'\r')
                state=view('confirm')
                assert state['writes']==before,'numeric review must be read only'
                assert '实际 0.999756 mm/s（364 U/s）' in (output/'confirm-80x24.txt').read_text()
                from fractions import Fraction
                base=start if key in ('r','j') else zero
                target=base+int(Fraction(text)/Fraction(360,131072))
                assert state['target']==target,(key,text,state['target'],target)
                if text=='+1.2':
                    assert target-start>364,'explicit linear measuring must permit >1degree'
                    (output/'confirm-80x24.txt').replace(output/'linear-confirm-80x24.txt')
                send('\r')
                state=wait(lambda s:s.get('phases',{}).get('1')=='running' and not s.get('busy') and
                    s.get('position')==target and s.get('view')=='main',key+' '+text+' frozen607A reached')
                checks.append(key+' '+text+' confirms exactly quantified raw target '+str(target))
                return state
            state=move('r','+1.2')
            writes=state['writes']
            oldproofs=state['proof_targets'],state['proof_starts']
            revision=state['coordinate_revision']
            send('z')
            state=wait(lambda s:s.get('coordinate_revision',0)>revision and not s.get('busy'),'z under running reached proof')
            captured=state['zero']
            assert captured==state['position'] and state['writes']==writes and state['settings']==limits
            assert (state['proof_targets'],state['proof_starts'])==oldproofs
            checks.append('z fresh reached sample: zero downloads, same raw physical proof and drive envelope')
            move('j','-.2')
            move('p','+.1')
            move('p','-.1')
            state=move('p','0')
            assert state['position']==captured
            (output/'main-80x24.txt').replace(output/'linear-main-80x24.txt')
            before=last['writes']
            send('r')
            view('edit')
            send('999999999999999999\r')
            wait(lambda s:not s.get('busy') and '行程' in s.get('progress',''),'drive boundary refuses numeric review')
            assert last['writes']==before
            stop()
            checks.append('out-of-range relative zero motion downloads; Space stops during numeric entry')
            send('r')
            view('edit')
            send('.1\r')
            view('confirm')
            before=list(last['controls'])
            stop('x')
            send('\r')
            drain(.1)
            assert last['controls']==before+[2]
            checks.append('x during position review discards target; Enter cannot replay')
            send('?')
            view('help')
            assert 's：直线mm/s' in (output/'help-80x24.txt').read_text()
            stop('x')
            send('q')
            end=time.monotonic()+10
            while process.poll() is None and time.monotonic()<end:drain(.03)
            assert process.poll()==0,'linear PTY did not exit normally'
            trace=json.loads((output/'trace.json').read_text())
            assert len(trace['enabled_edges'])==5
            assert trace['enabled_target_writes']==[edge[2] for edge in trace['enabled_edges']]
            assert trace['controls'][-1]==6
            assert not any(i in (0x607c,0x6098,0x1010) for i,_,_ in trace['writes'])
            checks.append('all five moves preserve enabled target rewrite/new ACK0/1/0; no home/offset/save; exit cleanup6')
            summary=dict(passed=len(checks),checks=checks,controls=trace['controls'],rc=process.returncode,
                terminal='80x24',transport='standard MockBus only; real socket creation forbidden',
                mode='--manual --manual-linear',coefficient='360 mock mm/motor revolution; not machine calibration')
            (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
            print(json.dumps(summary,ensure_ascii=False))
            return summary
        for key,kind,value in (('s','speed','1.001'),('a','step','0.1')):
            send(key)
            view('edit')
            send(value+'\r')
            wait(lambda s:s.get('view')=='main' and not s.get('draft'),kind+' normal numeric Enter commit')
        state,target=confirm('f','running')
        assert state['position']==target and state['controls']==[0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f]
        (output/'main-80x24.txt').replace(output/'connected-main-80x24.txt')
        (output/'confirm-80x24.txt').replace(output/'jog-confirm-80x24.txt')
        checks.append('f Enter real SDO enabled bit4 edge/newACK0/1/0 and progressive feedback reached target, no token/F5')
        # A completed zero-speed jog permits a new direction immediately.
        # The confirmed reverse itself must send stop2/disable0 first.
        state,target=confirm('b','running')
        assert state['position']==target and state['origin']==-1470
        assert state['controls'][9:11]==[2,0]
        stop('x')
        checks.append('continuous f then b without manual Space/d; internal2/0 before resubmission')
        before=list(last['controls'])
        send('f')
        view('confirm')
        stop('x')
        send('\r')
        drain(.2)
        assert last['controls']==before+[2]
        checks.append('x in confirmation discards action; Enter cannot revive')
        send('d')
        view('confirm')
        send('\r')
        wait(lambda s:s.get('phases')=={} and s.get('controls',[])[-1:]==[0] and not s.get('busy'),'d Enter disables')
        # An unfinished draft must never swallow a safety key.
        send('s')
        view('edit')
        send('1')
        wait(lambda s:s.get('draft')=='1','draft entered')
        stop()
        assert last['draft']==''
        send('?')
        view('help')
        stop('x')
        checks.append('Space in edit and x in help reach actual quick-stop writes')
        send('d')
        view('confirm')
        send('\r')
        wait(lambda s:s.get('controls',[])[-1:]==[0] and not s.get('busy'),'second d')
        state,target=confirm('e','enabled')
        assert state['controls'][-6:]==[0x106,0x107,0x127,0x137,0x127,0x12f]
        assert 0x2f not in state['controls'][-6:]
        checks.append('e explicit frozen current target, fresh ACK, Halt12F no release')
        send('q')
        end=time.monotonic()+10
        while process.poll() is None and time.monotonic()<end:drain(.03)
        assert process.poll()==0,'PTY child did not exit normally'
        drain(.01)
        trace=json.loads((output/'trace.json').read_text())
        assert len(trace['enabled_target_writes'])==2
        assert trace['enabled_target_writes']==[edge[2] for edge in trace['enabled_edges']]
        assert trace['controls'][-1]==6
        assert [raw for word,raw in trace['status_reads'] if word==0x106][:3]==[0x260,0x260,0x231]
        assert [raw for word,raw in trace['status_reads'] if word==0x107][:3]==[0x231,0x231,0x233]
        checks.append('actual SDO delays:0106 ACK then0260/0260/0231;0107 ACK then0231/0231/0233; full f Enter succeeds')
        summary=dict(passed=len(checks),checks=checks,controls=trace['controls'],rc=process.returncode,
            terminal='80x24',transport='standard MockBus only; real socket creation forbidden')
        (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        print(json.dumps(summary,ensure_ascii=False))
        return summary
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(3)
        drain(.01)
        os.close(master)
        (output/'terminal.ansi').write_bytes(transcript)


class SnapshotPublicationTests(unittest.TestCase):
    def test_visible_state_has_complete_corresponding_screen(self):
        from unittest import mock
        with tempfile.TemporaryDirectory(prefix='pmp4010-publication-') as directory:
            output=Path(directory)
            replace=Path.replace
            expected=None
            observed=[]
            def observe_commit(path,target):
                result=replace(path,target)
                if Path(target)==output/'state.json':
                    # Observe immediately at the atomic publication boundary,
                    # before the producer executes its next instruction.
                    state=json.loads((output/'state.json').read_text())
                    screen=output/(state['view']+'-80x24.txt')
                    self.assertTrue(screen.exists(),'state exposed before its screen exists')
                    self.assertEqual(screen.read_text(),expected,'state exposed with a stale/partial screen')
                    observed.append(state['target'])
                return result
            with mock.patch.object(Path,'replace',observe_commit):
                for target in (-1034,-1106):
                    expected='冻结607A={} U\nEnter 确认本次动作'.format(target)
                    publish_snapshot(output,dict(view='confirm',target=target),expected)
            self.assertEqual(observed,[-1034,-1106])


class SimplePtyTests(unittest.TestCase):
    def test_real_curses_production_manual_entry(self):
        with tempfile.TemporaryDirectory(prefix='pmp4010-simple-pty-') as output:
            result=run_pty(output)
            self.assertEqual(result['rc'],0)
            self.assertGreaterEqual(result['passed'],12)

    def test_real_curses_public_linear_measuring_entry(self):
        with tempfile.TemporaryDirectory(prefix='pmp4010-linear-pty-') as output:
            result=run_pty(output,linear=True)
            self.assertEqual(result['rc'],0)
            self.assertGreaterEqual(result['passed'],20)


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--child':raise SystemExit(child(sys.argv[2]))
    if len(sys.argv)==3 and sys.argv[1]=='--child-linear':raise SystemExit(child(sys.argv[2],linear=True))
    if len(sys.argv)==3 and sys.argv[1]=='--run':run_pty(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=='--run-linear':run_pty(sys.argv[2],linear=True)
    else:unittest.main()
