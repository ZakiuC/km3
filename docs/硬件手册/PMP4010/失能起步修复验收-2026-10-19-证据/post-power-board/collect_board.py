import base64,datetime,hashlib,json,pathlib,shlex,subprocess,sys
b=pathlib.Path(__file__).resolve().parent;label=sys.argv[1]
code="""import base64,hashlib,json,pathlib
root=pathlib.Path('/tmp/km3-pp-disabled-start-stage3-recovery-20261019-0213/move-session-1')
files={}
for p in root.iterdir():
 assert p.is_file() and not p.is_symlink()
 data=p.read_bytes();files[p.name]={'sha256':hashlib.sha256(data).hexdigest(),'data_base64':base64.b64encode(data).decode()}
print(json.dumps({'root':str(root),'files':files}))
"""
argv=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=10','root@192.168.0.188',shlex.join(['python3','-I','-B','-c',code])]
p=subprocess.run(argv,capture_output=True,timeout=30)
(b/(label+'.collect.command.json')).write_text(json.dumps(dict(argv=argv,rc=p.returncode,utc=datetime.datetime.now(datetime.timezone.utc).isoformat()),indent=2)+'\n');(b/(label+'.collect.stderr')).write_bytes(p.stderr);assert p.returncode==0
result=json.loads(p.stdout);out=b/label;out.mkdir(exist_ok=False);manifest={}
for name,item in result['files'].items():
 assert pathlib.Path(name).name==name
 data=base64.b64decode(item['data_base64']);assert hashlib.sha256(data).hexdigest()==item['sha256'];(out/name).write_bytes(data);manifest[name]=dict(sha256=item['sha256'],bytes=len(data))
(b/(label+'.collect.manifest.json')).write_text(json.dumps(dict(source=result['root'],files=manifest),indent=2)+'\n');print(json.dumps(manifest,indent=2))
