import base64,datetime,hashlib,json,pathlib
p=pathlib.Path('/var/tmp/km3-pp-disabled-start-stage3-recovery-20261019-b2/move-session-1')
files={}
for name in ('app-audit.jsonl','pmp4010-motion-168074b07518464b8aeb300f13b26c1f.jsonl'):
 f=p/name
 if f.is_file():
  b=f.read_bytes();files[name]=dict(size=len(b),sha256=hashlib.sha256(b).hexdigest(),base64=base64.b64encode(b).decode())
 else:files[name]=dict(exists=False)
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=files)))
