"""Stage the approved historical archive into a new /tmp; do not execute payload."""
import hashlib,io,json,os,pathlib,posixpath,sys,tarfile,tempfile
raw=sys.stdin.buffer.read()
sha=hashlib.sha256(raw).hexdigest()
assert sha=='1aa97f73db9299d950bfa7e7b4b701f06998b3cdfd99c0c18d8dc7b251bf270f','archive sha mismatch'
t=tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz')
members=t.getmembers()
assert len(members)==21
names=[m.name for m in members]
assert len(names)==len(set(names))
for m in members:
 p=pathlib.PurePosixPath(m.name)
 assert not p.is_absolute() and str(p)==m.name and '..' not in p.parts and '.' not in p.parts
 assert m.isfile() or m.issym()
 if m.issym():
  assert not pathlib.PurePosixPath(m.linkname).is_absolute()
  resolved=posixpath.normpath(posixpath.join(str(p.parent),m.linkname))
  assert resolved in names and not resolved.startswith('../')
manifest=json.loads(t.extractfile('payload-manifest.json').read())
assert set(names)=={x['path'] for x in manifest['files']}|{'payload-manifest.json'}
for item in manifest['files']:
 member=t.getmember(item['path'])
 if 'symlink' in item:
  assert member.issym() and member.linkname==item['symlink']
 else:
  data=t.extractfile(member).read()
  assert member.isfile() and len(data)==item['size'] and hashlib.sha256(data).hexdigest()==item['sha256']
base=pathlib.Path(tempfile.mkdtemp(prefix='km3-pp-disabled-start-stage2-20261019-',dir='/tmp'))
(base/'frozen-payload.tar.gz').write_bytes(raw)
root=base/'payload';root.mkdir()
for member in members:
 if member.isfile():
  dest=root/member.name;dest.parent.mkdir(parents=True,exist_ok=True)
  with dest.open('xb') as f:f.write(t.extractfile(member).read())
  dest.chmod(0o644)
for member in members:
 if member.issym():os.symlink(member.linkname,root/member.name)
checks=[]
for item in manifest['files']:
 dest=root/item['path']
 if 'symlink' in item:assert dest.is_symlink() and os.readlink(dest)==item['symlink']
 else:assert dest.stat().st_size==item['size'] and hashlib.sha256(dest.read_bytes()).hexdigest()==item['sha256']
 checks.append(item['path'])
print(json.dumps(dict(result='PASS',base=str(base),payload_root=str(root),archive_sha256=sha,safe_members=len(members),manifest_members_verified=len(checks),source_head=manifest['source_head'],CAN_opens=0,CAN_sends=0,payload_executed=False),ensure_ascii=False))
