import hashlib, json, os, pathlib, platform, re, subprocess, tarfile, tempfile
stable=pathlib.Path('/opt/km3/hwtools/pmp4010-cpp-test')
def managed():
    if not stable.exists() and not stable.is_symlink(): return None
    assert stable.is_symlink(),'Stable entry exists and is not a managed symlink'
    old=os.readlink(stable)
    resolved=pathlib.Path(old) if old.startswith('/') else stable.parent/old
    assert str(resolved.parent)=='/opt/km3/hwtools' and re.fullmatch(r'pmp4010-cpp-test-20261020-[0-9a-f]{8}',resolved.name),'Unmanaged stable link target'
    return old

TARGET='/opt/km3/hwtools/pmp4010-cpp-test-20261020-488add26'
STAGE='/opt/km3/hwtools/.pmp4010-no-od-upload-l0wn8c7a'
ARCHIVE_SHA='5987ca9a5e4adee4971a151407f3ab47660864f779007a7dbb564e779cbc0d90'
BINARY_SHA='488add26caf31eedfed1dfe0a71868e296eb27c20a06b38f58a04f8aea063b99'
stage=pathlib.Path(STAGE); target=pathlib.Path(TARGET); archive=stage/'package.tar.gz'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==ARCHIVE_SHA
files={}
with tarfile.open(archive,'r:gz') as tar:
    members=tar.getmembers(); assert len(members)==8
    for m in members:
        p=pathlib.PurePosixPath(m.name)
        assert not p.is_absolute() and '..' not in p.parts and p.parts[0]=='pmp4010-cpp-no-od-manual-arm64'
        assert m.isdir() or m.isfile()
        if m.isfile():
            assert len(p.parts)==2 and p.parts[1] not in files
            files[p.parts[1]]=(tar.extractfile(m).read(),m.mode&0o777)
assert set(files)=={'pmp4010_manual_test','run-manual.sh','README.md','使用说明.md','ELF_DEPENDENCIES.txt','SOURCE_SHA256.json','SHA256SUMS'}
assert hashlib.sha256(files['pmp4010_manual_test'][0]).hexdigest()==BINARY_SHA
target.mkdir(mode=0o755,exist_ok=False)
for name,(data,mode) in files.items():
    p=target/name
    with p.open('xb') as f: f.write(data)
    p.chmod(mode)
subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=target,check=True)
subprocess.run(['sh','-n',str(target/'run-manual.sh')],check=True)
print('target='+str(target)); print('binary_sha256='+hashlib.sha256((target/'pmp4010_manual_test').read_bytes()).hexdigest())
archive.unlink();stage.rmdir();print('upload_stage_removed=True')
