#!/usr/bin/env python3
"""Evidence-only real 80x24 curses session; child transport is isolated MockBus."""
import errno, fcntl, json, os, pathlib, pty, struct, subprocess, sys, termios, time
ROOT = pathlib.Path.cwd()
OUT = pathlib.Path(__file__).resolve().parent/'raw'/'pty-final'
OUT.mkdir(exist_ok=True)
sys.dont_write_bytecode = True
sys.path.insert(0,str(ROOT/'tools/hwtui'))
from verify_pmp4010_bench_pty import Session
from verify_pmp4010_pty import Terminal, width

def child():
    from contextlib import ExitStack
    sys.path.insert(0,str(ROOT/'device/drivers/pmp4010/tools'))
    from _test_bench_support import environment, options, DelayedBus, requests, tool
    from _test_manual import EnabledEdgeNode
    from pmp4010_brake import BrakeApp
    node=EnabledEdgeNode(); node.enable_drift=0
    bus=DelayedBus([node])
    def deny(event,args):
        if event.startswith(('socket.','subprocess.')) or event in ('os.system','os.posix_spawn','os.posix_spawnp'):
            raise AssertionError('real socket/subprocess forbidden: '+event)
    sys.addaudithook(deny)
    with ExitStack() as stack:
        for patch in environment(bus): stack.enter_context(patch)
        args=options(OUT/'motion.jsonl'); args.rows=24; args.cols=80
        args.manual_velocity=100
        class EvidenceApp(BrakeApp):
            def on_key(self,name,key):
                count=len(bus.sent)
                result=super().on_key(name,key)
                if name in ('f','b','s','a','j','p','z','9'):
                    invalid.append(dict(key=name,frames_before=count,frames_after=len(bus.sent),view=self.current_view))
                return result
        invalid=[]
        app=EvidenceApp(tool,args)
        try: return app.run()
        finally:
            controls=[v for i,s,v in requests(bus,True) if i==0x6040]
            data=dict(closed=bus.closed,controls=controls,final_statusword=node.value(0x6041),shutdown_results=app.controller.shutdown_results,invalid_keys=invalid,enabled_edges=node.enabled_edges,
                      movement_samples=node.movement_samples,requests=[dict(id=f.arbitration_id,data=f.data.hex()) for f in bus.sent])
            (OUT/'trace.json').write_text(json.dumps(data,indent=2)+'\n')

class BrakeSession(Session):
    def __init__(self):
        self.out=OUT; self.terminal=Terminal(24,80); self.raw=bytearray(); self.keys=[]; self.screens={}
        self.master,slave=pty.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,0,0))
        self.before=termios.tcgetattr(slave)
        env=dict(os.environ,LANG='C.UTF-8',LC_ALL='C.UTF-8',TERM='xterm',ESCDELAY='25')
        env.pop('LINES',None); env.pop('COLUMNS',None)
        self.process=subprocess.Popen([sys.executable,'-I',str(pathlib.Path(__file__).resolve()),'--child'],stdin=slave,stdout=slave,stderr=slave,cwd=ROOT,env=env,start_new_session=True)
        self.slave=slave
    def capture(self,name):
        lines=self.terminal.lines()
        assert not self.terminal.unknown, self.terminal.unknown
        assert len(lines)==24 and all(sum(width(c) for c in line)==80 for line in lines)
        self.screens[name]=lines
        return '\n'.join(lines)
    def save(self):
        self.read(.1)
        (OUT/'terminal.raw').write_bytes(self.raw)
        (OUT/'screens.txt').write_text('\n\n'.join('=== '+k+' ===\n'+'\n'.join(v) for k,v in self.screens.items())+'\n')
        restored=termios.tcgetattr(self.slave)==self.before
        os.close(self.slave); os.close(self.master)
        return restored

def parent():
    s=BrakeSession(); result={}
    try:
        s.until('Enter仅连接并读取'); s.capture('startup-rt')
        s.key('enter'); s.until('本会话接受以下OD'); s.capture('authorization')
        s.key('enter'); s.until('会话授权完成'); s.capture('main')
        for key in ('f','b','s','a','j','p','z','9'): s.send(key)
        assert '1/e松闸须Enter' in s.capture('invalid-motion-keys-noop')
        s.send('1'); s.until('Enter 仅确认这一次松闸请求'); s.capture('hold-review-1')
        s.key('enter'); s.until('松闸请求：已使能保持'); s.capture('held-1')
        s.send('2'); s.until('抱闸请求：已失能'); s.capture('disabled-immediately')
        s.send('e'); s.until('Enter 仅确认这一次松闸请求'); s.capture('hold-review-e')
        s.key('enter'); s.until('松闸请求：已使能保持'); s.capture('held-e')
        s.send('q'); rc=s.process.wait(timeout=5)
        trace=json.loads((OUT/'trace.json').read_text())
        assert rc==0 and trace['closed']
        assert trace['controls'].count(0x12f)==2
        assert trace['controls'][-1]==6  # inherited Shutdown: bit2 clear
        assert trace['final_statusword'] & 0x6f != 0x27
        assert trace['shutdown_results'] and all(r['disabled_confirmed'] for r in trace['shutdown_results'])
        assert len(trace['invalid_keys'])==8 and all(r['frames_before']==r['frames_after'] and r['view']=='main' for r in trace['invalid_keys'])
        assert trace['controls'][6]==0  # key2 immediate disable before second hold
        assert 0x2f not in trace['controls'] and 0x3f not in trace['controls']
        assert not trace['enabled_edges'] and not trace['movement_samples']
        result=dict(rc=rc,passed=True,controls=trace['controls'],screens=list(s.screens),size=[24,80],transport='MockBus',socket_subprocess_audit_block=True)
    finally:
        if s.process.poll() is None:
            s.process.terminate(); s.process.wait(timeout=3)
        result['termios_restored']=s.save()
        (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    assert result['termios_restored']
    print(json.dumps(result))

if __name__=='__main__':
    if '--child' in sys.argv: sys.exit(child())
    parent()
