import datetime,json,pathlib,subprocess,hashlib
b=pathlib.Path(__file__).parent
argv=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=10','root@192.168.0.188','python3 -B -']
script=(b/'passive.py').read_bytes()
(b/'passive.command.json').write_text(json.dumps(dict(argv=argv,stdin_script=str(b/'passive.py'),stdin_sha256=hashlib.sha256(script).hexdigest(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat()),indent=2)+'\n')
p=subprocess.run(argv,input=script,capture_output=True,timeout=40)
(b/'passive.stdout').write_bytes(p.stdout);(b/'passive.stderr').write_bytes(p.stderr);(b/'passive.rc').write_text(str(p.returncode)+'\n')
print(p.returncode);print(p.stdout.decode());print(p.stderr.decode())
