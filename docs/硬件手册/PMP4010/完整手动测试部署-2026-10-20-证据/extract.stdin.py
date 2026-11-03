import hashlib,json,os,pathlib,sys,tarfile
stage=pathlib.Path(sys.argv[1])
assert stage.parent == pathlib.Path('/opt/km3/hwtools') and stage.name.startswith('.manual-test-e137c603-')
archive=stage/'payload.tar.gz'
assert archive.stat().st_size==235068
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='e137c603d57c584134f14f006bcae482d3b999ce1d2dda8dd25b19005a80b237'
final=stage.parent/'manual-test-20261020-e137c603'
assert not os.path.lexists(final), 'destination already exists; no clobber'
with tarfile.open(archive) as tf:
 members=tf.getmembers();seen=set();regular=[];links=[];dirs=[]
 for m in members:
  p=pathlib.PurePosixPath(m.name)
  assert not p.is_absolute() and '..' not in p.parts and m.name not in seen and m.mode & 0o7000==0
  seen.add(m.name)
  if m.isfile(): regular.append(m)
  elif m.isdir(): dirs.append(m)
  elif m.issym():
   assert m.name in ('drivers/pmp4010/libpmp4010_velocity.so','drivers/pmp4010/libpmp4010_velocity.so.1')
   expected={'drivers/pmp4010/libpmp4010_velocity.so':'libpmp4010_velocity.so.1','drivers/pmp4010/libpmp4010_velocity.so.1':'libpmp4010_velocity.so.1.0.0'}
   assert m.linkname==expected[m.name]
   links.append(m)
  else: raise RuntimeError('unsupported tar type '+m.name)
 assert len(regular)==26 and len(links)==2
 for m in regular+dirs:
  assert not any(pathlib.PurePosixPath(x.name) in pathlib.PurePosixPath(m.name).parents for x in links)
 payload=stage/'payload';payload.mkdir(mode=0o755)
 for m in dirs: (payload/m.name).mkdir(parents=True,exist_ok=True)
 for m in regular:
  dst=payload/m.name;dst.parent.mkdir(parents=True,exist_ok=True)
  with dst.open('xb') as f: f.write(tf.extractfile(m).read())
  dst.chmod(m.mode & 0o777)
 for m in links: os.symlink(m.linkname,payload/m.name)
manifest=payload/'payload-manifest.json'
assert hashlib.sha256(manifest.read_bytes()).hexdigest()=='8cf6fa0d6efdfb3831ff7e5397fd67314aa3ea433969e07d51478296634caa51'
data=json.loads(manifest.read_text())
for item in data['files']:
 p=payload/item['path']
 if 'symlink' in item: assert p.is_symlink() and os.readlink(p)==item['symlink']
 else: assert not p.is_symlink() and p.stat().st_size==item['size'] and hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
assert os.access(payload/'run-manual.sh',os.X_OK)
assert not os.path.lexists(final)
os.rename(payload,final)
print(json.dumps({'result':'PASS','destination':str(final),'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'manifest_sha256':hashlib.sha256((final/'payload-manifest.json').read_bytes()).hexdigest(),'regular_files':26,'symlinks':2,'atomic_rename':True,'CAN_opens':0}))
