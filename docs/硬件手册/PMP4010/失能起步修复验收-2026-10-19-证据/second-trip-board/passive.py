"""Read-only OS/filesystem evidence; no CAN socket, transmission, state mutation, or remote file creation."""
import base64,datetime,hashlib,json,os,pathlib,socket,subprocess
P=pathlib.Path
STAGE=P('/tmp/km3-pp-disabled-start-stage3-recovery-20261019-0213')
def read(p):
 try:return p.read_text()
 except (OSError,UnicodeError) as e:return {'error':str(e)}
def sha(p):
 try:return hashlib.sha256(p.read_bytes()).hexdigest()
 except OSError as e:return {'error':str(e)}
def run(args):
 try:
  x=subprocess.run(args,capture_output=True,text=True,timeout=5)
  return dict(argv=args,rc=x.returncode,stdout=x.stdout,stderr=x.stderr)
 except Exception as e:return dict(argv=args,error=str(e))
services={s:run(['systemctl','show',s,'--property=MainPID,ActiveState,SubState,ExecMainStartTimestamp,ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic']) for s in ('km3-core','km3-rt-controller','km3-video')}
processes=[]
for p in P('/proc').iterdir():
 if not p.name.isdigit() or int(p.name)==os.getpid():continue
 try:cmd=(p/'cmdline').read_bytes().replace(b'\x00',b' ').decode(errors='replace')
 except OSError:continue
 if any(s in cmd for s in ('pmp4010','pty_runner','mock_app','km3-pp-disabled','drive_probe','km3-rt-controller')):
  fds={}
  try:
   for fd in (p/'fd').iterdir():
    try:fds[fd.name]=os.readlink(fd)
    except OSError as e:fds[fd.name]={'error':str(e)}
  except OSError as e:fds={'error':str(e)}
  processes.append(dict(pid=int(p.name),cmdline=cmd,status=read(p/'status'),fds=fds))
config=P('/etc/km3/device.json')
try:device_id=json.loads(config.read_text()).get('device',{}).get('id')
except Exception as e:device_id={'error':str(e)}
logs={}
for rel in ('move-session-1/app-audit.jsonl','move-session-1/pmp4010-motion-650a90d3d1544412ad5c152ef6566fed.jsonl','pmp4010-motion-650a90d3d1544412ad5c152ef6566fed.jsonl'):
 p=STAGE/rel
 if p.is_file():
  b=p.read_bytes();logs[rel]=dict(exists=True,size=len(b),sha256=hashlib.sha256(b).hexdigest(),base64=base64.b64encode(b).decode())
 else:logs[rel]=dict(exists=False)
listing=[]
if STAGE.is_dir():
 for root,dirs,files in os.walk(STAGE):
  rel=P(root).relative_to(STAGE)
  if len(rel.parts)>=2:dirs[:]=[]
  for name in dirs+files:
   p=P(root)/name
   try:listing.append(dict(path=str(p.relative_to(STAGE)),is_dir=p.is_dir(),size=p.stat().st_size))
   except OSError as e:listing.append(dict(path=str(p.relative_to(STAGE)),error=str(e)))
model=P('/proc/device-tree/model')
try:board_model=model.read_bytes().rstrip(b'\x00').decode()
except OSError as e:board_model={'error':str(e)}
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),hostname=socket.gethostname(),model=board_model,boot_id=read(P('/proc/sys/kernel/random/boot_id')),uptime=read(P('/proc/uptime')),device_id=device_id,config_sha=sha(config),can_env_sha=sha(P('/etc/km3/can.env')),current=os.path.realpath('/opt/km3/hwtools/current'),services=services,related_processes_excluding_snapshot_pid=processes,snapshot_pid=os.getpid(),can_rcvlists={p.name:read(p) for p in P('/proc/net/can').glob('rcvlist*')},can_link=run(['ip','-details','-statistics','-json','link','show','can1']),stage_path=str(STAGE),stage_exists=STAGE.is_dir(),stage_listing_max_depth_2=listing,logs=logs,remote_file_writes=0,CAN_socket_opens=0,CAN_sends=0),ensure_ascii=False,indent=2))
