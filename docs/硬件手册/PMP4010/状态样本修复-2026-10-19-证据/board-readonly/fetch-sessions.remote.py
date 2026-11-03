import base64,datetime,hashlib,json,pathlib
P=pathlib.Path
root=P('/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs').resolve(strict=True)
paths=['/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs/manual-5BQ8TdzB/motion-absolute-d4b6ca7077e948cba2ae49428eb6f273.jsonl', '/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs/manual-IgxS6Xwl/motion-absolute-27ee2c1af9d7430a9c49730cccbf49c8.jsonl', '/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs/manual-JpJbKdTb/motion-absolute-fc4866f836a64297bebd203407f99ad1.jsonl']
files=[]
for name in paths:
 p=P(name);assert p.parent.parent==root and p.parent.name.startswith('manual-');assert p.is_file() and not p.is_symlink() and p.resolve(strict=True).is_relative_to(root)
 s0=p.stat();b=p.read_bytes();s1=p.stat()
 files.append(dict(path=name,size=len(b),sha256=hashlib.sha256(b).hexdigest(),mtime_ns=s1.st_mtime_ns,mtime_utc=datetime.datetime.fromtimestamp(s1.st_mtime,datetime.timezone.utc).isoformat(),changed_during_read=(s0.st_size!=s1.st_size or s0.st_mtime_ns!=s1.st_mtime_ns),base64=base64.b64encode(b).decode()))
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=files,CAN_socket_opens=0,CAN_sends=0,remote_file_writes=0)))
