import base64,datetime,hashlib,json,pathlib,subprocess,sys
B=pathlib.Path(__file__).resolve().parent
label=sys.argv[1]
remote=r"""import base64,datetime,hashlib,json,pathlib
p=pathlib.Path('/var/tmp/km3-pp-disabled-start-stage3-recovery-20261019-b2/move-session-1')
files={}
for name in ('app-audit.jsonl','pmp4010-motion-168074b07518464b8aeb300f13b26c1f.jsonl'):
 f=p/name
 if f.is_file():
  b=f.read_bytes();files[name]=dict(size=len(b),sha256=hashlib.sha256(b).hexdigest(),base64=base64.b64encode(b).decode())
 else:files[name]=dict(exists=False)
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=files)))
"""
argv=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=5','root@192.168.0.188','python3 -I -B -']
(B/(label+'.remote.py')).write_text(remote)
p=subprocess.run(argv,input=remote.encode(),capture_output=True,timeout=12)
(B/(label+'.stdout')).write_bytes(p.stdout);(B/(label+'.stderr')).write_bytes(p.stderr)
(B/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,stdin_file=label+'.remote.py',rc=p.returncode,utc=datetime.datetime.now(datetime.timezone.utc).isoformat()),indent=2)+'\n')
print('ssh_rc',p.returncode)
assert p.returncode==0
raw=json.loads(p.stdout);parsed={}
for name,item in raw['files'].items():
 if 'base64' in item:
  data=base64.b64decode(item['base64']);assert hashlib.sha256(data).hexdigest()==item['sha256']
  (B/(label+'-'+name)).write_bytes(data)
  lines=data.decode().splitlines();rows=[]
  for index,line in enumerate(lines):
   try:rows.append(json.loads(line))
   except json.JSONDecodeError:
    assert index==len(lines)-1,'nonfinal malformed JSON line'
  parsed[name]=rows
 else:parsed[name]=[]
a=parsed['app-audit.jsonl'];states=[r['state'] for r in a if r['event']=='ui_state'];state=states[-1] if states else None
motion=parsed['pmp4010-motion-168074b07518464b8aeb300f13b26c1f.jsonl']
result=dict(utc=raw['utc'],state=state,audit_job_events=[r for r in a if r['event']=='job_event'],motion_tail=motion[-5:],motion_event_names=[r.get('event') for r in motion],download_begin=sum(r['event']=='sdo_download_begin' for r in a),download_done=sum(r['event']=='sdo_download_done' for r in a))
(B/(label+'.state.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
