import base64,hashlib,json,pathlib,subprocess,sys
P=pathlib.Path
expected={'reset_only_once.py':'6176b8d66ab93fcb232aed8bb15e095bfc05bb27fe7b43de0be392dfe5ec021d','pinned_payload.py':'e8a98830191da64cce22422b3bb9540f68049b5907ae35b9ad259268855c6205'}
files=json.loads(sys.stdin.buffer.read());assert set(files)==set(expected)
data={name:base64.b64decode(value,validate=True) for name,value in files.items()}
for name,h in expected.items():assert hashlib.sha256(data[name]).hexdigest()==h
parent=P('/var/tmp/km3-pmp4010-diag-20261019-0218ab4207eb');assert parent.resolve(strict=True)==parent
stage=parent/'reset-only-plan';assert not stage.exists() and not stage.is_symlink()
stage.mkdir(mode=0o700,exist_ok=False)
for name,raw in data.items():
 with (stage/name).open('xb') as stream:stream.write(raw)
 assert hashlib.sha256((stage/name).read_bytes()).hexdigest()==expected[name]
argv=['python3','-I','-B',str(stage/'reset_only_once.py'),'--execute-reset-only-once','--manual-session-reason',sys.argv[1]]
print(json.dumps(dict(event='exact_reset_execution',argv=argv,source_sha256=expected,timeout_seconds=10),ensure_ascii=False),flush=True)
try:
 p=subprocess.run(argv,capture_output=True,timeout=10);rc,stdout,stderr=p.returncode,p.stdout,p.stderr
except subprocess.TimeoutExpired as exc:rc,stdout,stderr=124,exc.stdout or b'',(exc.stderr or b'')+b'\nRemote10s timeout; no retry.\n'
sys.stdout.buffer.write(stdout);sys.stdout.buffer.flush();sys.stderr.buffer.write(stderr);sys.stderr.buffer.flush()
print(json.dumps(dict(event='reset_process_exit',rc=rc,automatic_retries=0,motion_executed=False)),flush=True)
sys.exit(rc)
