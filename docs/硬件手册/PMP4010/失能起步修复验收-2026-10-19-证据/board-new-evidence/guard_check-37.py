"""Pure in-memory tests of the send fence; no socket module or CAN open."""
from pathlib import Path
import contextlib,importlib.util,io,json,types
base=Path(__file__).parent
spec=importlib.util.spec_from_file_location('diagnose',base/'diagnose.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Bus:
 def __init__(self):self.sent=[]
 def send(self,f):self.sent.append(f)
module=types.SimpleNamespace(FRAME_CLASSIC=0)
def frame(index=0x6041,sub=0,cs=0x40,cob=0x601,tail=bytes(4),extended=False,remote=False,frame_type=0):
 return types.SimpleNamespace(arbitration_id=cob,data=bytes([cs,index&255,index>>8,sub])+tail,extended=extended,remote=remote,frame_type=frame_type)
def guard():
 b=Bus();g=m.Guard(b,module);g.pending=(0x6041,0,2);return b,g
def rejected(g,f):
 try:g.send(f)
 except RuntimeError:return True
 return False
checks=[]
with contextlib.redirect_stdout(io.StringIO()):
 for label,kw in [('download',{'cs':0x2b}),('NMT',{'cob':0}),('PDO',{'cob':0x201}),('node2',{'cob':0x602}),('not_pending',{'index':0x6064}),('not_allowlisted',{'index':0x2000}),('nonzero_reserved',{'tail':b'\0\0\0\1'}),('extended',{'extended':True}),('remote',{'remote':True}),('FD',{'frame_type':1})]:
  b,g=guard();assert rejected(g,frame(**kw)) and not b.sent;checks.append(label)
 b,g=guard();g.send(frame());assert rejected(g,frame()) and len(b.sent)==1;checks.append('duplicate')
 b,g=guard();g.pending=None;assert rejected(g,frame()) and not b.sent;checks.append('no_pending')
 b,g=guard();g.start-=16;assert rejected(g,frame()) and not b.sent;checks.append('deadline')
 b,g=guard()
 for _ in range(37):g.sent=False;g.send(frame())
 g.sent=False;assert rejected(g,frame()) and len(b.sent)==37;checks.append('37_hard_cap')
assert len(m.TABLE)+3*len(m.SAMPLES)==37
result=dict(result='PASS',checks=len(checks),labels=checks,table_rows=len(m.TABLE),sample_rows=len(m.SAMPLES),sample_groups=3,max_uploads=37,real_CAN_opens=0,real_CAN_sends=0,real_SSH_calls=0)
(base/'guard-check.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
