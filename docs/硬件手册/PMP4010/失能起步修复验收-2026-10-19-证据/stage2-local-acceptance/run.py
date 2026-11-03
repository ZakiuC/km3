from pathlib import Path
import datetime, hashlib, json, shlex, subprocess, sys, time
ROOT=Path('/home/zakiu/km3')
OUT=Path('/tmp/km3-pp-disabled-start-20261019-acceptance/stage2')
name=sys.argv[1]; args=sys.argv[2:]
stem=OUT/name
if stem.with_suffix('.command.json').exists():
    raise SystemExit('record already exists; use distinct retry name')
started=datetime.datetime.now(datetime.timezone.utc).isoformat(); t=time.monotonic()
r=subprocess.run(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
for suffix,data in [('stdout.log',r.stdout),('stderr.log',r.stderr),('exitcode',(str(r.returncode)+'\n').encode())]:
    (OUT/(name+'.'+suffix)).write_bytes(data)
entry={'name':name,'argv':args,'shell_command':shlex.join(args),'cwd':str(ROOT),'started_utc':started,'duration_seconds':time.monotonic()-t,'returncode':r.returncode,'stdout':name+'.stdout.log','stderr':name+'.stderr.log','stdout_sha256':hashlib.sha256(r.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(r.stderr).hexdigest()}
(OUT/(name+'.command.json')).write_text(json.dumps(entry,ensure_ascii=False,indent=2)+'\n')
with (OUT/'index.jsonl').open('a') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
sys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr)
print('recorded rc='+str(r.returncode),flush=True)
sys.exit(r.returncode)
