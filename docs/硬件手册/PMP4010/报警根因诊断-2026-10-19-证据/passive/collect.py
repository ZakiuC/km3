import base64,datetime,hashlib,json,subprocess,pathlib
P=pathlib.Path
root=P(__file__).parent
script=root/'passive.remote.py'
argv=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=5','root@192.168.0.188','python3 -I -B - /var/tmp/km3-pmp4010-alarm-20261019-735538978204']
start=datetime.datetime.now(datetime.timezone.utc).isoformat()
p=subprocess.run(argv,input=script.read_bytes(),capture_output=True,timeout=35)
(root/'passive.stdout').write_bytes(p.stdout);(root/'passive.stderr').write_bytes(p.stderr);(root/'passive.rc').write_text(str(p.returncode)+'\n')
(root/'passive.command.json').write_text(json.dumps(dict(argv=argv,script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),start_utc=start,end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),rc=p.returncode,ssh_calls=1,automatic_retries=0),indent=2)+'\n')
if p.returncode:raise SystemExit(p.returncode)
data=json.loads(p.stdout)
for i,item in enumerate(data['logs']):
 if 'content_base64' in item:
  raw=base64.b64decode(item.pop('content_base64'),validate=True)
  assert hashlib.sha256(raw).hexdigest()==item['sha256']
  local=root/('fresh-log-%03d-'%i+P(item['path']).name)
  local.write_bytes(raw);item['local_file']=str(local)
(root/'passive-summary.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(rc=p.returncode,utc=data['utc'],boot_id=data['boot_id'],processes=data['related_processes_excluding_snapshot_pid'],logs=data['logs'],can_link=data['can_link'],config_sha=data['config_sha'],current=data['current']),ensure_ascii=False,indent=2))
