import datetime,hashlib,json,pathlib,subprocess,sys,time
BASE=pathlib.Path(__file__).parent
label=sys.argv[1]
script=(BASE/'passive.py').read_bytes()
argv=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=8','root@192.168.0.188','python3 -I -B -']
started=datetime.datetime.now(datetime.timezone.utc).isoformat()
p=subprocess.run(argv,input=script,capture_output=True,timeout=25)
(BASE/(label+'.stdout')).write_bytes(p.stdout)
(BASE/(label+'.stderr')).write_bytes(p.stderr)
(BASE/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,stdin_script='passive.py',stdin_sha256=hashlib.sha256(script).hexdigest(),utc_start=started,utc_end=datetime.datetime.now(datetime.timezone.utc).isoformat(),rc=p.returncode),indent=2)+'\n')
print(json.dumps(dict(label=label,rc=p.returncode,stdout=p.stdout.decode(errors='replace'),stderr=p.stderr.decode(errors='replace')),ensure_ascii=False))
sys.exit(p.returncode)
