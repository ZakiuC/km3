"""Passive OS/filesystem snapshot only: no CAN socket, sends or mutations."""
import datetime, hashlib, json, os, pathlib, socket, subprocess
P=pathlib.Path
ROOT=P('/tmp/km3-pp-prepare-load-20261017-1YfFxQ5O/payload')
SERVICES=('km3-core','km3-rt-controller','km3-video')
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=5)
 return dict(argv=args,rc=p.returncode,stdout=p.stdout,stderr=p.stderr)
def hash_file(p):
 return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def snapshot():
 config=P('/etc/km3/device.json')
 cfg=json.loads(config.read_text())
 services={s:run(['systemctl','show',s,'--property=MainPID,ActiveState,SubState,ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic']) for s in SERVICES}
 pid=int(next(x.split('=',1)[1] for x in services['km3-rt-controller']['stdout'].splitlines() if x.startswith('MainPID=')))
 rt=P('/proc')/str(pid)
 paths=['drivers/pmp4010/pmp4010.py','drivers/pmp4010/pmp4010_bench.py','drivers/pmp4010/pmp4010_position.py','drivers/pmp4010/pmp4010_tui.py','drivers/pmp4010/pmp4010_params.py','drivers/pmp4010/pmp4010_math.py','drivers/pmp4010/od.json','drivers/pmp4010/libpmp4010_velocity.so.1.0.0','hwtui/canopen.py','hwtui/canbus.py','hwtui/canopen_wire.py','hwtui/canopen_consts.py','hwtui/mockbus.py','hwtui/tui.py']
 return dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),hostname=socket.gethostname(),model=P('/proc/device-tree/model').read_bytes().rstrip(b'\x00').decode(),device_id=cfg['device']['id'],config_sha=hash_file(config),can_env_sha=hash_file(P('/etc/km3/can.env')),current=os.path.realpath('/opt/km3/hwtools/current'),services=services,rt_cmdline=(rt/'cmdline').read_bytes().replace(b'\x00',b' ').decode() if pid else None,rt_fds={p.name:os.readlink(p) for p in (rt/'fd').iterdir()} if pid else {},can_rcvlists={p.name:p.read_text() for p in P('/proc/net/can').glob('rcvlist*')},can_link=run(['ip','-details','-json','link','show','can1']),payload_root=str(ROOT),payload_exists=ROOT.is_dir(),source_hashes={rel:hash_file(ROOT/rel) for rel in paths})
if __name__=='__main__': print(json.dumps(snapshot(),ensure_ascii=False,indent=2))
