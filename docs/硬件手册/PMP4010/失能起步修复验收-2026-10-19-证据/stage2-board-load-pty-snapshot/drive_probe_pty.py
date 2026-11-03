"""Real curses keyboard driver: Ready -> D two confirmations -> B two confirmations."""
import importlib.util,json,pathlib,sys,time
B=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('pty_runner',B/'pty_runner.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
scenario=sys.argv[1];out=B/('pty-probe-'+scenario);s=r.Session(out,scenario)
def cmd(item):return s.command_input(item)
def rows():
 p=out/'app-audit.jsonl'
 return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
def state():
 states=[x['state'] for x in rows() if x['event']=='ui_state'];return states[-1] if states else {}
def wait_for(predicate,label,seconds=12):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  s.read(.1)
  if predicate():return
  if s.process.poll() is not None:raise RuntimeError('app exited '+label)
 raise RuntimeError('wait timeout '+label+' '+str(state()))
def key(name):cmd({'action':'key','name':name})
def text(value):cmd({'action':'text','text':str(value)})
def capture(label):return cmd({'action':'capture','label':label})
def result_count():return sum(x['event']=='job_event' and x['name']=='bench_result' for x in rows())
def freeze_and_confirm(action,prefix):
 wait_for(lambda:state().get('view')=='action',prefix+' action')
 capture(prefix+'-preview');key('f5')
 wait_for(lambda:state().get('view')=='confirm' and state().get('pending'),prefix+' confirm')
 review=state()['pending'];assert review['action']==action
 capture(prefix+'-frozen-before-token')
 token=review['token'];assert token in (out/(prefix+'-frozen-before-token.screen.txt')).read_text()
 (out/(prefix+'-frozen-review.json')).write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
 text(token);key('f5');return review
try:
 wait_for(lambda:state().get('view')=='rt','RT')
 capture('initial-rt-risk');key('f5')
 wait_for(lambda:state().get('view')=='overview' and not state().get('busy') and state().get('row_values'),'scan')
 assert state()['row_values']['statusword']==0x231 and state()['row_values']['controlword']==6
 key('enter');capture('initial-Ready-detail')
 key('f2')
 for value in (-1506,-1434,36):
  wait_for(lambda:state().get('view')=='setting','settings');text(value);key('f5')
 wait_for(lambda:state().get('view')=='detail','settings done')
 text('D');wait_for(lambda:state().get('view')=='od','OD')
 capture('OD-session-token');text('OD mock-bench 1');key('f5')
 disable=freeze_and_confirm('disable','D-disable')
 assert disable['token']=='DISABLE mock-bench 1 -1470'
 wait_for(lambda:result_count()==1 and not state().get('busy'),'D result')
 capture('after-D-strictdisabled')
 wait_for(lambda:state().get('row_values',{}).get('statusword')==0x240,'D refreshed')
 assert state()['row_values']['controlword']==0
 text('B');probe=freeze_and_confirm('probe_pp_start','B-probe')
 assert probe['start']==probe['target']==-1470
 assert probe['token']=='PROBE_PP_START mock-bench 1 -1470'
 wait_for(lambda:result_count()==2 and not state().get('busy'),'B result',seconds=15)
 capture('B-final-result-before-exit')
 result=cmd({'action':'finish'});assert result['finished'] and result['child_rc']==0
 print(json.dumps(dict(result='DRIVER_COMPLETE',scenario=scenario,driver_rc=0,child_rc=0,tokens=[disable['token'],probe['token']],output=str(out))))
except BaseException as exc:
 capture('driver-failure')
 (out/'driver-error.json').write_text(json.dumps(dict(type=type(exc).__name__,message=str(exc),state=state()),ensure_ascii=False,indent=2)+'\n')
 if s.process.poll() is None:
  key('esc')
  for _ in range(20):
   s.read(.1)
   if not state().get('busy'):break
  print(json.dumps({'normal_UI_cleanup':cmd({'action':'finish'})}),flush=True)
 raise
finally:s.close_files()
