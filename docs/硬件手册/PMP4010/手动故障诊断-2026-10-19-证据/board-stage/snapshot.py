"""Authorized before/after passive snapshot; never opens CAN or controls processes."""
from pathlib import Path
import datetime,hashlib,json,shlex,subprocess,sys
B=Path(__file__).resolve().parent
label=sys.argv[1]
assert label in ('before-passive','after-passive')
stage=sys.argv[2]
assert Path(stage).parent==Path('/var/tmp') and Path(stage).name.startswith('km3-pmp4010-diag-20261019-')
source=B/'passive.remote.py'
raw=source.read_bytes()
argv=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=5','root@192.168.0.188',shlex.join(['python3','-I','-B','-',stage])]
start=datetime.datetime.now(datetime.timezone.utc).isoformat()
try:
 p=subprocess.run(argv,input=raw,capture_output=True,timeout=35)
 rc,stdout,stderr=p.returncode,p.stdout,p.stderr
except subprocess.TimeoutExpired as e:
 rc,stdout,stderr=124,e.stdout or b'',(e.stderr or b'')+b'Local timeout; no retry.\n'
(B/(label+'.stdout')).write_bytes(stdout)
(B/(label+'.stderr')).write_bytes(stderr)
(B/(label+'.rc')).write_text(str(rc)+'\n')
(B/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,start_utc=start,end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),stdin_script=str(source),stdin_sha256=hashlib.sha256(raw).hexdigest(),rc=rc),indent=2)+'\n')
print(json.dumps(dict(label=label,rc=rc)),flush=True)
if rc:sys.exit(rc)
x=json.loads(stdout)
print(json.dumps({k:x[k] for k in ('utc','stage_exists','hostname','model','boot_id','device_id','config_sha','can_env_sha','current','services','related_processes_excluding_snapshot_pid','can_rcvlists','can_link')},ensure_ascii=False,indent=2))
