import base64,datetime,hashlib,json,pathlib
P=pathlib.Path
paths=['/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs/manual-5BQ8TdzB/motion-absolute-d4b6ca7077e948cba2ae49428eb6f273.jsonl', '/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs/manual-IgxS6Xwl/motion-absolute-27ee2c1af9d7430a9c49730cccbf49c8.jsonl', '/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs/manual-JpJbKdTb/motion-absolute-fc4866f836a64297bebd203407f99ad1.jsonl']
root=P('/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs')
logs=[]
for name in paths:
 p=P(name)
 assert root in p.resolve().parents and not p.is_symlink()
 b=p.read_bytes()
 assert len(b)<20000000
 logs.append(dict(path=name,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),base64=base64.b64encode(b).decode()))
current=P('/var/tmp/km3-pmp4010-state-20261019-43b0b2355c3b/payload')
hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [current/'drivers/pmp4010/pmp4010_manual.py',current/'drivers/pmp4010/pmp4010_simple.py',current/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0',current/'payload-manifest.json']}
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),logs=logs,stage_source_hashes=hashes,remote_writes=0,CAN_socket_opens=0,CAN_sends=0,process_control_operations=0),indent=2))
