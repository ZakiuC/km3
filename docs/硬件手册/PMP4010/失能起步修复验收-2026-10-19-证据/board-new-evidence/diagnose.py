#!/usr/bin/env python3
"""One bounded node1 upload-only diagnostic; no recovery, retries or downloads."""
import argparse, datetime, hashlib, json, pathlib, sys, time
sys.dont_write_bytecode=True
P=pathlib.Path
TABLE=[(0x1000,0,'uint32')]+[(0x1018,s,'uint32') for s in range(1,5)]+[(0x2002,1,'uint16'),(0x6060,0,'int8'),(0x6061,0,'int8'),(0x6040,0,'uint16'),(0x6041,0,'uint16'),(0x605D,0,'int16'),(0x6064,0,'int32'),(0x607A,0,'int32'),(0x6062,0,'int32'),(0x6067,0,'uint32'),(0x6068,0,'uint16'),(0x606C,0,'int32'),(0x2006,0x12,'uint16'),(0x607C,0,'int32'),(0x6081,0,'uint32')]
SAMPLES=[row for index in [0x6040,0x6041,0x6064,0x607A,0x6062,0x606C] for row in TABLE if row[0]==index]
TYPES={'uint32':(4,False),'uint16':(2,False),'int32':(4,True),'int8':(1,True),'int16':(2,True)}
ALLOW={(i,s) for i,s,t in TABLE}
def emit(event,**data):print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),monotonic=time.monotonic(),event=event,**data),ensure_ascii=False),flush=True)
class Guard:
 def __init__(self,bus,module):self.bus=bus;self.module=module;self.pending=None;self.sent=False;self.count=0;self.start=time.monotonic()
 def send(self,frame):
  d=frame.data
  if self.count>=38:raise RuntimeError('38 upload hard cap exceeded')
  if time.monotonic()-self.start>15:raise RuntimeError('15s total deadline exceeded')
  if (frame.arbitration_id!=0x601 or frame.extended or frame.remote or frame.frame_type!=self.module.FRAME_CLASSIC or len(d)!=8 or d[0]!=0x40 or d[4:]!=bytes(4) or (d[1]|d[2]<<8,d[3]) not in ALLOW or self.pending is None or (d[1]|d[2]<<8,d[3])!=self.pending[:2] or self.sent):
   raise RuntimeError('upload-only exact-pending send guard rejected frame')
  self.sent=True;emit('tx',cob_id='601',raw_hex=d.hex());self.bus.send(frame);self.count+=1
 def recv(self,timeout):
  frame=self.bus.recv(timeout)
  if frame is None:return None
  emit('rx',cob_id=f'{frame.arbitration_id:03X}',raw_hex=frame.data.hex(),extended=frame.extended,remote=frame.remote,frame_type=frame.frame_type)
  if frame.arbitration_id==0x581:
   d=frame.data;i,s,n=self.pending
   if frame.extended or frame.remote or frame.frame_type!=self.module.FRAME_CLASSIC or len(d)!=8 or (d[1]|d[2]<<8,d[3])!=(i,s):raise RuntimeError('unexpected SDO response frame')
   if d[0] not in (0x80,{1:0x4f,2:0x4b,4:0x43}[n]):raise RuntimeError('unexpected/non-expedited SDO response CS')
  return frame

def main():
 args=argparse.ArgumentParser();args.add_argument('--source-root',required=True);args.add_argument('--baseline',required=True);args.add_argument('--live-read-only',action='store_true');a=args.parse_args()
 root=P(a.source_root).resolve(strict=True)
 baseline=json.loads(P(a.baseline).read_text())
 if not baseline.get('payload_exists') or baseline.get('payload_root')!=str(root):raise RuntimeError('fresh baseline does not pin an existing approved payload root')
 if not baseline.get('source_hashes') or any(want is None for want in baseline['source_hashes'].values()):raise RuntimeError('fresh nonmissing source hashes required')
 for rel,want in baseline['source_hashes'].items():
  path=root/rel;got=hashlib.sha256(path.read_bytes()).hexdigest()
  if got!=want:raise RuntimeError('source hash changed: '+rel)
  emit('source',path=str(path),sha256=got)
 sys.path.insert(0,str(root/'hwtui'));import canopen,canbus
 for m in (canopen,canbus):
  if P(m.__file__).resolve().parent!=root/'hwtui':raise RuntimeError('module not from frozen source')
 emit('plan',table=TABLE,samples=SAMPLES,sample_groups=3,interval=.2,max_uploads=38,timeout=.25,total_deadline=15,writes=0,nmt=0,retries=0)
 if not a.live_read_only:emit('offline_complete',can_opens=0,can_sends=0);return 0
 from passive import snapshot
 before=snapshot(root);emit('passive_before',snapshot=before)
 for k in baseline:
  if k!='utc' and before[k]!=baseline[k]:raise RuntimeError('passive baseline changed: '+k)
 if before['hostname']!='RK3588-Tronlong' or before['model']!='TL3588-EVM' or before['device_id']!='km3-test-001':raise RuntimeError('identity mismatch')
 if any(any(line.strip() and not line.startswith('receive list') and 'no entry' not in line for line in text.splitlines()) for text in before['can_rcvlists'].values()):raise RuntimeError('CAN receive user present')
 bus=canbus.CanBus('can1',loopback=False,receive_own_messages=False);client=None;guard=None;rc=0
 try:
  bus.open();guard=Guard(bus,canopen);client=canopen.SdoClient(guard,1,timeout=.25)
  def read(row,phase):
   i,s,t=row;n,signed=TYPES[t];guard.pending=(i,s,n);guard.sent=False
   emit('upload_begin',index=f'{i:04X}',sub=s,type=t,phase=phase)
   raw=client.read(i,s)
   if len(raw)!=n:raise RuntimeError('typed width mismatch')
   v=int.from_bytes(raw,'little',signed=signed)
   emit('value',index=f'{i:04X}',sub=s,type=t,raw_hex=raw.hex(),value=v,phase=phase)
   if i==0x1018 and v!={1:1289,2:50331649,3:808475175,4:226440}[s]:raise RuntimeError('device 1018 identity changed')
   return v
  values={f'{i:04X}:{s:02X}':read((i,s,t),'snapshot') for i,s,t in TABLE}
  emit('snapshot',values=values)
  for group in range(1,4):
   if group>1:time.sleep(.2)
   sample={f'{i:04X}:{s:02X}':read((i,s,t),f'sample{group}') for i,s,t in SAMPLES}
   emit('sample',group=group,values=sample)
  emit('complete',uploads=guard.count,writes=0,nmt=0,retries=0,quarantined=client.quarantined)
 except BaseException as exc:
  # Host policy: any failed transaction terminates and preserves quarantine.
  if client is not None:client.quarantined=True
  emit('failed',exception=type(exc).__name__,message=str(exc),quarantined=bool(client and client.quarantined),later_requests=0)
  rc=130 if isinstance(exc,KeyboardInterrupt) else 1
 finally:
  bus.close();emit('closed',is_open=bus.is_open,tx_count=bus.tx_count,rx_count=bus.rx_count,quarantined=bool(client and client.quarantined))
  after=snapshot(root);emit('passive_after',snapshot=after)
  changes=[k for k in before if k!='utc' and before[k]!=after[k]];emit('passive_comparison',changed_keys=changes)
  if changes:rc=1
 return rc
if __name__=='__main__':sys.exit(main())
