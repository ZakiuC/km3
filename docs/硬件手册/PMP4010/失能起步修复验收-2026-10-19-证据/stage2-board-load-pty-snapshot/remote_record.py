import datetime,hashlib,json,pathlib,subprocess,sys
BASE=pathlib.Path(__file__).parent
SSH=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=8','root@192.168.0.188']
def run(label,command,data=None,timeout=35):
 argv=SSH+[command]
 start=datetime.datetime.now(datetime.timezone.utc).isoformat()
 p=subprocess.run(argv,input=data,capture_output=True,timeout=timeout)
 (BASE/(label+'.stdout')).write_bytes(p.stdout);(BASE/(label+'.stderr')).write_bytes(p.stderr)
 (BASE/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,stdin_sha256=hashlib.sha256(data).hexdigest() if data is not None else None,stdin_bytes=len(data) if data is not None else 0,utc_start=start,utc_end=datetime.datetime.now(datetime.timezone.utc).isoformat(),rc=p.returncode),indent=2)+'\n')
 print(json.dumps(dict(label=label,rc=p.returncode,stdout=p.stdout.decode(errors='replace'),stderr=p.stderr.decode(errors='replace')),ensure_ascii=False),flush=True)
 return p
