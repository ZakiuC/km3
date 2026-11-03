import base64,datetime,hashlib,json,pathlib,subprocess
p=pathlib.Path('/var/tmp/km3-pmp4010-accept-20261019-260e7f4b/run-01')
files=[]
for f in sorted(p.iterdir()):
 if not f.is_file() or f.is_symlink():continue
 b=f.read_bytes()
 assert len(b)<10000000
 files.append(dict(path=str(f),bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),base64=base64.b64encode(b).decode()))
x=subprocess.run(['ip','-details','-statistics','-json','link','show','can1'],capture_output=True,text=True,timeout=5)
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=files,can_link=dict(rc=x.returncode,stdout=x.stdout,stderr=x.stderr),can_stats=pathlib.Path('/proc/net/can/stats').read_text(),protocols=pathlib.Path('/proc/net/protocols').read_text(),can_rcvlists={f.name:f.read_text() for f in pathlib.Path('/proc/net/can').glob('rcvlist*')},CAN_socket_opens=0,CAN_sends=0,remote_file_writes=0,process_control=0),indent=2))
