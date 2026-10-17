#!/usr/bin/env python3
"""Round-two isolated MockBus PTY evidence; no hardware transport allowed."""
import argparse,json,os,pathlib,sys,time,signal,pty,fcntl,termios,struct,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[4]
assert (ROOT/'CLAUDE.md').is_file() and (ROOT/'device/drivers/pmp4010/tools').is_dir(), 'run the delivered script from its repository evidence location'
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'tools/hwtui'))
from verify_pmp4010_bench_pty import Session

def child(out,scenario):
 from contextlib import ExitStack
 sys.path.insert(0,str(ROOT/'device/drivers/pmp4010/tools'))
 from _test_bench_support import BenchNode,environment,mockbus,options,tool
 from pmp4010_tui import PmpApp
 nodes=[BenchNode(i) for i in ([] if scenario=='batch-zero' else [1,2,3] if scenario.startswith('batch') else [1])]
 events=[]
 def record(kind,**kw): events.append(dict(sequence=len(events),kind=kind,**kw))
 class TraceBus(mockbus.MockBus):
  def send(self,frame):
   record('send',id=frame.arbitration_id,data=frame.data.hex())
   assert frame.arbitration_id!=0,'NMT forbidden'
   if scenario=='batch-cancel' and frame.arbitration_id==0x601 and frame.data[:6]==bytes.fromhex('2b4060000000') and not (out/'delay-entered').exists():
    (out/'delay-entered').write_text('worker blocked during first member disable');time.sleep(.8)
   if (out/'arm-timeout').exists() and frame.data[:6]==bytes.fromhex('2b4060000600'):
    nodes[0].drop_reads.add((0x6041,0));record('inject-status-read-timeout')
   return super().send(frame)
  def recv(self,timeout=None):
   frame=super().recv(timeout)
   if frame: record('recv',id=frame.arbitration_id,data=frame.data.hex())
   return frame
  def close(self):
   record('close');return super().close()
 bus=TraceBus(nodes,loopback=False)
 with ExitStack() as stack:
  for patch in environment(bus):stack.enter_context(patch)
  args=options(out/'motion.jsonl');args.ascii=True
  app=PmpApp(tool,args)
  original_poll=app.poll
  def poll():
   original_poll()
   if (out/'isolate-two').exists() and not (out/'isolated-two').exists():
    with app.controller.lock:
     row=app.controller.row(2)
     if row:
      reason='PTY 人工注入隔离；需外部恢复'
      app.controller.blocked[(app.controller.context[0],2)]=reason
      row.update(quarantined=True,quarantine_reason=reason,lost=True)
      app.controller._put(row)
      record('isolate',node=2)
      (out/'isolated-two').write_text('isolated before batch review')
  app.poll=poll
  try:return app.run()
  finally:
   (out/'ui-log.json').write_text(json.dumps(list(app.log._entries),ensure_ascii=False,indent=2)+'\n')
   (out/'trace.json').write_text(json.dumps({'transport':'MockBus','closed':bus.closed,'events':events},ensure_ascii=False,indent=2)+'\n')

class EvidenceSession(Session):
 def __init__(self,out,scenario):
  from verify_pmp4010_pty import Terminal
  self.out=out;self.terminal=Terminal(30,120);self.raw=bytearray();self.keys=[];self.screens={}
  self.master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',30,120,0,0))
  self.command=[sys.executable,'-I',str(pathlib.Path(__file__).resolve()),'--child','--scenario',scenario,'--output-dir',str(out)]
  env=dict(os.environ,LANG='zh_CN.UTF-8',LC_ALL='zh_CN.UTF-8',TERM='xterm',ESCDELAY='25');env.pop('LINES',None);env.pop('COLUMNS',None)
  self.process=subprocess.Popen(self.command,stdin=slave,stdout=slave,stderr=slave,cwd=ROOT,env=env,start_new_session=True);os.close(slave)
 def finish(self):
  if self.process.poll() is None:self.send('q')
  try:rc=self.process.wait(timeout=5)
  except subprocess.TimeoutExpired:
   self.process.terminate();rc=self.process.wait(timeout=2)
  self.read(.2);os.close(self.master)
  (self.out/'terminal.raw').write_bytes(self.raw)
  (self.out/'terminal-decoded.txt').write_text(self.raw.decode('utf-8',errors='replace'))
  (self.out/'screens.txt').write_text('\n\n'.join('=== '+k+' ===\n'+'\n'.join(v) for k,v in self.screens.items())+'\n')
  return rc

def main():
 p=argparse.ArgumentParser();p.add_argument('--child',action='store_true');p.add_argument('--scenario',default='exit');p.add_argument('--output-dir',type=pathlib.Path,required=True)
 a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=True)
 if a.child:return child(a.output_dir,a.scenario)
 for marker in ('arm-timeout','isolate-two','isolated-two','delay-entered'):
  (a.output_dir/marker).unlink(missing_ok=True)
 s=EvidenceSession(a.output_dir,a.scenario);failure=None
 try:
  s.until('RT 控制器可能正在占用总线');s.key('f5');s.until('扫描完成');s.capture('overview')
  if a.scenario=='batch-zero':
   s.send('d');s.until('无在线节点');s.capture('zero-target-rejected')
  elif a.scenario.startswith('batch'):
   (a.output_dir/'isolate-two').write_text('inject')
   deadline=time.monotonic()+3
   while not (a.output_dir/'isolated-two').exists() and time.monotonic()<deadline:s.read(.05)
   assert (a.output_dir/'isolated-two').exists()
   key='x' if a.scenario=='batch-stop' else 'd';verb='STOP' if key=='x' else 'DISABLE'
   s.send(key);s.until('OD 闸门');s.send('OD mock-bench ALL 1,3');s.key('f5');s.until('实时预览');s.capture('batch-preview')
   s.key('f5');s.until('确认执行');s.capture('batch-confirmation');s.send(verb+' ALL 1,3');s.key('f5')
   if a.scenario=='batch-cancel':
    s.until('动作执行中');s.key('esc')
   s.until('动作已完成');s.capture('batch-results')
  else:
   s.key('enter');s.until('固件 V3.3');s.send('e');s.until('OD 闸门');s.send('OD mock-bench 1');s.key('f5')
   s.until('安全设置 1/3');s.key('f5');s.until('安全设置 2/3');s.key('f5');s.until('安全设置 3/3');s.send('364');s.key('f5')
   s.until('使能：实时预览');s.capture('enable-preview');s.key('f5');s.until('确认执行');s.capture('enable-confirmation')
   s.send('ENABLE mock-bench 1 -1464');s.key('f5');s.until('动作已完成');s.capture('enabled')
   if a.scenario=='review':
    s.send('a');s.until('绝对位置：实时预览');s.send('\x1bOH'+'\x1b[3~'*16+'-1414');s.key('f5');s.until('确认执行');s.capture('absolute-raw-review');s.key('esc');s.key('esc')
   if a.scenario=='exit-timeout':(a.output_dir/'arm-timeout').write_text('only drop after exit Shutdown sent')
   if a.scenario=='sigint':os.kill(s.process.pid,signal.SIGINT)
   else:s.send('q')
 except BaseException as exc:
  failure=str(exc);s.screens['failure']=s.terminal.lines()
 finally:rc=s.finish()
 trace=json.loads((a.output_dir/'trace.json').read_text()) if (a.output_dir/'trace.json').exists() else {}
 events=trace.get('events',[]);writes=[e for e in events if e['kind']=='send' and e['data'][:2] in ('2f','2b','23')]
 text=s.raw.decode('utf-8',errors='replace')
 try:
  assert trace.get('closed'),'bus not closed'
  assert events[-1]['kind']=='close','bus close must be last transport event'
  assert all(e['id']!=0 for e in events if e['kind']=='send'),'NMT sent'
  if a.scenario=='batch-zero':assert not writes,'zero targets wrote frames'
  elif a.scenario.startswith('batch'):
   isolated=next(e['sequence'] for e in events if e['kind']=='isolate')
   assert not any(e['kind']=='send' and e['id']==0x602 and e['sequence']>isolated for e in events)
   assert sorted({e['id']-0x600 for e in writes})==([1] if a.scenario=='batch-cancel' else [1,3])
   assert '节点 2：已隔离，跳过' in (a.output_dir/'screens.txt').read_text()
  else:
   shutdown=[e for e in writes if e['data'].startswith('2b4060000600')]
   assert shutdown,'Shutdown 0x06 not sent'
   if a.scenario=='exit-timeout':
    assert '节点 1 失能未确认，状态未知，须独立急停' in text
    injected=next(e['sequence'] for e in events if e['kind']=='inject-status-read-timeout')
    later=[e for e in events if e['kind']=='send' and e['sequence']>injected]
    assert len(later)==1 and later[0]['data'].startswith('40416000'),'quarantined client retried'
   else:
    assert 'operation enabled=0，失能已确认' in text
    shutdown_sequence=shutdown[-1]['sequence']
    later=[e for e in events if e['sequence']>shutdown_sequence]
    upload=next(e for e in later if e['kind']=='send' and e['data'].startswith('40416000'))
    reply=next(e for e in later if e['kind']=='recv' and e['sequence']>upload['sequence'] and e['data'].startswith('4b416000'))
    assert not int.from_bytes(bytes.fromhex(reply['data'])[4:6],'little')&4,'operation enabled remained set'
    assert events[-1]['sequence']>reply['sequence'],'close preceded status readback'
   if a.scenario=='review':assert '写 607Ah = -1414' in (a.output_dir/'screens.txt').read_text()
  if a.scenario=='sigint':assert rc==-signal.SIGINT,'SIGINT child rc='+str(rc)
  else:assert rc==0,'child rc='+str(rc)
 except BaseException as exc:failure=failure or str(exc)
 report={'scenario':a.scenario,'command':s.command,'child_exit_code':rc,'failure':failure,'keys':s.keys,'mock_closed':trace.get('closed'),'write_frames':writes}
 (a.output_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(report,ensure_ascii=False,indent=2));print('TERMINAL TAIL:',text[-1600:]);return 1 if failure else 0
if __name__=='__main__':sys.exit(main())
