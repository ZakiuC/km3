"""Keyboard-only local driver. Reads observer evidence; never calls UI actions."""
import hashlib,importlib.util,json,pathlib,sys,time
B=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('pty_runner',B/'pty_runner.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
scenario=sys.argv[1]
out=B/('pty-probe-'+scenario)
s=r.Session(out,scenario)
def cmd(item):return s.command_input(item)
def rows():
 p=out/'app-audit.jsonl'
 return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
def state():
 a=[x['state'] for x in rows() if x['event']=='ui_state']
 return a[-1] if a else {}
def wait_for(predicate,label,seconds=10):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  s.read(.1)
  if predicate():return
  if s.process.poll() is not None:raise RuntimeError('app exited: '+label)
 raise RuntimeError('wait timed out: '+label+' state='+str(state()))
def key(k):cmd({'action':'key','name':k})
def text(t):cmd({'action':'text','text':str(t)})
def capture(label):return cmd({'action':'capture','label':label})
try:
 wait_for(lambda:state().get('view')=='rt','initial rt')
 capture('initial-rt-risk')
 key('f5')
 wait_for(lambda:state().get('view')=='overview' and not state().get('busy') and state().get('row_values'),'scan')
 capture('after-scan')
 key('enter');text('B')
 wait_for(lambda:state().get('view')=='od','OD Form')
 capture('od-session-confirmation')
 text('OD mock-bench 1');key('f5')
 for value in (-1506,-1434,36):
  wait_for(lambda:state().get('view')=='setting','setting Form')
  text(value);key('f5')
 wait_for(lambda:state().get('view')=='action','probe action')
 capture('probe-action-preview')
 key('f5')
 wait_for(lambda:state().get('view')=='confirm' and state().get('pending'),'frozen review')
 frozen=state()['pending']
 assert frozen['action']=='probe_pp_start'
 assert frozen['start']==frozen['target']==-1470
 assert not [x for x in rows() if x['event']=='sdo_download_begin']
 # Token comes from original frozen UI pending, cross-checked with actual rendered screen.
 capture('probe-frozen-review')
 token=frozen['token']
 assert token=='PROBE_PP_START mock-bench 1 -1470'
 screen=(out/'probe-frozen-review.screen.txt').read_text()
 assert token in screen
 (out/'frozen-review.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2)+'\n')
 text(token);key('f5')
 wait_for(lambda:any(x['event']=='job_event' and x['name']=='bench_result' for x in rows()) and not state().get('busy'),'result',seconds=15)
 capture('final-probe-result')
 result=cmd({'action':'finish'})
 assert result['finished'] and result['child_rc']==0,result
 print(json.dumps(dict(scenario=scenario,driver_rc=0,child_rc=result['child_rc'],output_dir=str(out),token=token)))
except BaseException as exc:
 capture('driver-failure')
 (out/'driver-error.json').write_text(json.dumps(dict(type=type(exc).__name__,message=str(exc),state=state()),ensure_ascii=False,indent=2)+'\n')
 # An explicit normal UI quit records original cleanup; never signal/kill.
 if s.process.poll() is None:
  key('esc')
  for _ in range(20):
   s.read(.1)
   if not state().get('busy'):break
  result=cmd({'action':'finish'})
  print(json.dumps({'cleanup_after_driver_failure':result}),flush=True)
 raise
finally:
 s.close_files()
