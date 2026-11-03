"""One passive post-session snapshot; no CAN socket or device requests."""
import datetime,json,subprocess
from pathlib import Path
r=Path('/tmp/km3-pp-motion-20261017')
b=json.loads(json.loads(Path('/tmp/km3-pp-prepare-load-20261017/07-after.json').read_text())['stdout'])
code='''import hashlib,json,pathlib,subprocess,socket,os
paths=PATHS
hashes={}
for name in paths:
 p=pathlib.Path(name)
 hashes[name]=hashlib.sha256(p.read_bytes()).hexdigest()
services={}
for service in ['km3-rt-controller','km3-core','km3-video']:
 r=subprocess.run(['systemctl','show',service,'--property=ActiveState,SubState,MainPID,ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic'],capture_output=True,text=True)
 if r.returncode: raise RuntimeError(r.stderr)
 services[service]=dict(line.split('=',1) for line in r.stdout.splitlines())
config=json.loads(pathlib.Path('/etc/km3/device.json').read_text())
pid=int(services['km3-rt-controller']['MainPID'])
proc=pathlib.Path('/proc')/str(pid)
args=[x.decode(errors='replace') for x in (proc/'cmdline').read_bytes().split(b'\\0') if x]
expected=['/opt/km3/current/km3-rt-controller','--config','/etc/km3/device.json','--require-config']
owner={'pid':pid,'argv_matches_expected':args==expected,'enable_sync':'--enable-sync' in args,'exe':os.readlink(proc/'exe'),'fds':{},'CAN_receive_lists':{}}
for p in sorted((proc/'fd').iterdir(),key=lambda p:int(p.name)):
 try: link=os.readlink(p)
 except FileNotFoundError:continue
 owner['fds'][p.name]=link if link.startswith(('socket:[','pipe:[','anon_inode:')) or link=='/dev/null' else '<path-redacted>'
for name in ('rcvlist_all','rcvlist_eff','rcvlist_err','rcvlist_fil','rcvlist_inv','rcvlist_sff'):
 p=proc/'net/can'/name
 if p.is_file():owner['CAN_receive_lists'][name]=p.read_text()
result={'device_id':config.get('device',{}).get('id'),'hostname':socket.gethostname(),'model':pathlib.Path('/proc/device-tree/model').read_bytes().rstrip(b'\\0').decode(),'current':str(pathlib.Path('/opt/km3/hwtools/current').resolve(strict=True)),'file_sha256':hashes,'services':services,'owner':owner,'actual_CAN_opens':0}
print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
'''.replace('PATHS',repr(sorted(b['file_sha256'])))
argv=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5','-o','ConnectionAttempts=1','root@192.168.0.188','python3 -I -B -']
(r/'board-post-passive.command.json').write_text(json.dumps(dict(argv=argv,stdin=code,utc=datetime.datetime.now(datetime.timezone.utc).isoformat()),ensure_ascii=False,indent=2)+'\n')
p=subprocess.run(argv,input=code,text=True,capture_output=True)
(r/'board-post-passive.stdout').write_text(p.stdout)
(r/'board-post-passive.stderr').write_text(p.stderr)
(r/'board-post-passive.rc').write_text(str(p.returncode)+'\n')
assert p.returncode==0
x=json.loads(p.stdout)
checks={k:x[k]==b[k] for k in ('device_id','hostname','model','current','file_sha256','services')}
assert all(checks.values()),checks
assert x['owner']['argv_matches_expected'] and not x['owner']['enable_sync']
assert all('(can1: no entry)' in raw for raw in x['owner']['CAN_receive_lists'].values())
(r/'board-post-passive-comparison.json').write_text(json.dumps(dict(result='PASS',compared_to='/tmp/km3-pp-prepare-load-20261017/07-after.json',file_count=len(b['file_sha256']),checks=checks,owner=x['owner'],actual_CAN_opens=0),ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(result='PASS',exit_code=p.returncode,file_count=len(b['file_sha256']),checks=checks,RT_pid=x['owner']['pid'],RT_fds=x['owner']['fds'],CAN_receive_lists_empty=True),ensure_ascii=False))
