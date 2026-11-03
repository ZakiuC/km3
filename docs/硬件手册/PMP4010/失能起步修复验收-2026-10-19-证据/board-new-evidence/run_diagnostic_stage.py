import contextlib,hashlib,importlib.util,io,json,pathlib,shlex,sys
b=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('remote_record',b/'remote_record.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
stage=json.loads((b/'stage-frozen.stdout').read_text());dest=stage['base']
(b/'baseline-38.json').write_bytes((b/'baseline-38.stdout').read_bytes())
(b/'baseline-38.script.py').write_bytes((b/'passive.py').read_bytes())
if sys.argv[1]=='transfer':
 files={n:(b/n).read_text() for n in ('diagnose.py','passive.py','baseline-38.json')}
 data=json.dumps(files,ensure_ascii=False).encode()
 code="import hashlib,json,pathlib,sys; b=pathlib.Path("+repr(dest)+"); files=json.load(sys.stdin); assert set(files)=={'diagnose.py','passive.py','baseline-38.json'}; result={};\nfor n,s in files.items():\n p=b/n\n with p.open('x') as f:f.write(s)\n result[n]=hashlib.sha256(p.read_bytes()).hexdigest()\nprint(json.dumps(result))"
 p=r.run('transfer-diagnostic','python3 -I -B -c '+shlex.quote(code),data)
 if p.returncode:raise SystemExit(p.returncode)
 expected={n:hashlib.sha256(v.encode()).hexdigest() for n,v in files.items()}
 assert json.loads(p.stdout)==expected
 print(json.dumps({'transfer_hash_match':True,'hashes':expected}))
else:
 assert sys.argv[1] in ('offline','live')
 code='import sys,runpy;sys.path.insert(0,'+repr(dest)+');runpy.run_path('+repr(dest+'/diagnose.py')+',run_name="__main__")'
 command='python3 -I -B -c '+shlex.quote(code)+' --source-root '+shlex.quote(stage['payload_root'])+' --baseline '+shlex.quote(dest+'/baseline-38.json')
 if sys.argv[1]=='live':command+=' --live-read-only'
 with contextlib.redirect_stdout(io.StringIO()):p=r.run('diagnostic-'+sys.argv[1],command,timeout=30)
 print(json.dumps({'mode':sys.argv[1],'rc':p.returncode,'stdout_bytes':len(p.stdout),'stderr':p.stderr.decode(),'record':'diagnostic-'+sys.argv[1]+'.command.json'}))
 if sys.argv[1]=='offline':print(p.stdout.decode())
 raise SystemExit(p.returncode)
