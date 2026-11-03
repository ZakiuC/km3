"""One passive OS/filesystem snapshot: no CAN socket, SDO, remote writes or process control."""
import datetime,hashlib,json,os,pathlib,socket,subprocess,sys
P=pathlib.Path
stage=P(sys.argv[1])
assert stage.parent==P("/var/tmp") and stage.name.startswith("km3-pmp4010-trigger-20261019-")
def run(args):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=5)
  return dict(argv=args,rc=p.returncode,stdout=p.stdout,stderr=p.stderr)
 except Exception as e:return dict(argv=args,error=str(e))
def read(p):
 try:return p.read_text()
 except (OSError,UnicodeError) as e:return dict(error=str(e))
def sha(p):
 try:return hashlib.sha256(p.read_bytes()).hexdigest()
 except OSError as e:return dict(error=str(e))
services={s:run(['systemctl','show',s,'--property=MainPID,ActiveState,SubState,ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic']) for s in ('km3-core','km3-rt-controller','km3-video')}
processes=[]
for p in P('/proc').iterdir():
 if not p.name.isdigit() or int(p.name)==os.getpid():continue
 try:cmd=(p/'cmdline').read_bytes().replace(b'\x00',b' ').decode(errors='replace')
 except OSError:continue
 if any(s in cmd for s in ('pmp4010','pty_runner','mock_app','km3-pp','drive_probe','board_app','candump','cansend','cangen','canplayer','km3-rt-controller')):
  fds={}
  try:
   for f in (p/'fd').iterdir():
    try:fds[f.name]=os.readlink(f)
    except OSError as e:fds[f.name]=dict(error=str(e))
  except OSError as e:fds=dict(error=str(e))
  processes.append(dict(pid=int(p.name),cmdline=cmd,fds=fds))
cfg=P('/etc/km3/device.json')
try:device_id=json.loads(cfg.read_text()).get('device',{}).get('id')
except Exception as e:device_id=dict(error=str(e))
try:model=P('/proc/device-tree/model').read_bytes().rstrip(b'\x00').decode()
except OSError as e:model=dict(error=str(e))
print(json.dumps(dict(stage_path=str(stage),stage_exists=stage.exists(),stage_realpath=os.path.realpath(stage),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),hostname=socket.gethostname(),model=model,boot_id=read(P('/proc/sys/kernel/random/boot_id')),uptime=read(P('/proc/uptime')),device_id=device_id,config_sha=sha(cfg),can_env_sha=sha(P('/etc/km3/can.env')),current=os.path.realpath('/opt/km3/hwtools/current'),services=services,related_processes_excluding_snapshot_pid=processes,snapshot_pid=os.getpid(),can_rcvlists={p.name:read(p) for p in P('/proc/net/can').glob('rcvlist*')},can_link=run(['ip','-details','-statistics','-json','link','show','can1']),remote_file_writes=0,CAN_socket_opens=0,CAN_sends=0,process_control_operations=0),ensure_ascii=False,indent=2))
