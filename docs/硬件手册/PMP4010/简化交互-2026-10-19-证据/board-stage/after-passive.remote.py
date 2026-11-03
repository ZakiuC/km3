"""One passive OS/filesystem snapshot: no CAN socket, SDO, remote writes or process control."""
import datetime,hashlib,json,os,pathlib,socket,subprocess
P=pathlib.Path
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

root=P('/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload')
manifest_file=root/'payload-manifest.json'
manifest_bytes=manifest_file.read_bytes()
assert hashlib.sha256(manifest_bytes).hexdigest()=='9c62ac397ecc7c03c8d1f0bc69330d4092c8867f64b699a20ac8011ddbb2947f'
manifest=json.loads(manifest_bytes)
verified=[]
for item in manifest['files']:
 p=root/item['path']
 assert p.resolve(strict=True).is_relative_to(root.resolve(strict=True))
 if 'symlink' in item:assert p.is_symlink() and os.readlink(p)==item['symlink']
 else:assert p.stat().st_size==item['size'] and sha(p)==item['sha256']
 verified.append(item['path'])
payload=dict(root=str(root),manifest_sha256=sha(manifest_file),verified_manifest_entries=len(verified),simple_sha256=sha(root/'drivers/pmp4010/pmp4010_simple.py'),manual_sha256=sha(root/'drivers/pmp4010/pmp4010_manual.py'),native_library_sha256=sha(root/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0'),launcher_sha256=sha(root/'run-manual.sh'),launcher_mode=oct((root/'run-manual.sh').stat().st_mode),launcher_executable=os.access(root/'run-manual.sh',os.X_OK),archive_sha256=sha(root.parent/'frozen-payload.tar.gz'),launcher_log_directory_exists=(root/'logs').exists(),older_manual_stage_exists=P('/var/tmp/km3-pmp4010-manual-20261019-fd97308d751f').is_dir(),withdrawn_eaeb_stage_exists=P('/var/tmp/km3-pmp4010-simple-20261019-eaeb23d5844e').exists())

print(json.dumps(dict(payload=payload,utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),hostname=socket.gethostname(),model=model,boot_id=read(P('/proc/sys/kernel/random/boot_id')),uptime=read(P('/proc/uptime')),device_id=device_id,config_sha=sha(cfg),can_env_sha=sha(P('/etc/km3/can.env')),current=os.path.realpath('/opt/km3/hwtools/current'),services=services,related_processes_excluding_snapshot_pid=processes,snapshot_pid=os.getpid(),simple_stage_exists=P('/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab').exists(),simple_stage_realpath=os.path.realpath('/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab'),can_rcvlists={p.name:read(p) for p in P('/proc/net/can').glob('rcvlist*')},can_link=run(['ip','-details','-statistics','-json','link','show','can1']),remote_file_writes=0,CAN_socket_opens=0,CAN_sends=0,process_control_operations=0),ensure_ascii=False,indent=2))
