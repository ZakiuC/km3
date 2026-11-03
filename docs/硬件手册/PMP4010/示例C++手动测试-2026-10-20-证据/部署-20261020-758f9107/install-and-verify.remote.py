STAGE='/opt/km3/hwtools/.pmp4010-cpp-upload-758f9107-i6n259dk'
TARGET='/opt/km3/hwtools/pmp4010-cpp-test-20261020-758f9107'
WRAPPER='#!/bin/sh\nset -eu\nscript_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$script_dir/pmp4010_manual_test" --interface can1 --node 1 --mm-per-rev 4 "$@"\n'
import hashlib, pathlib, tarfile, subprocess, json, os
stage=pathlib.Path(STAGE)
archive=stage/'package.tar.gz'
target=pathlib.Path(TARGET)
old=pathlib.Path('/opt/km3/hwtools/manual-test-20261020-e137c603/run-manual.sh')
old_before=hashlib.sha256(old.read_bytes()).hexdigest() if old.exists() else None
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='62a92542963d6c5ad9d71b16498e7f98e259762ef3a16204c9c7bfaf78d5f04e'
files={}
with tarfile.open(archive,'r:gz') as tar:
    members=tar.getmembers()
    assert len(members)==7
    for member in members:
        path=pathlib.PurePosixPath(member.name)
        assert not path.is_absolute() and '..' not in path.parts and path.parts[0]=='pmp4010-cpp-manual-arm64'
        assert member.isdir() or member.isfile()
        if member.isfile():
            assert len(path.parts)==2 and path.parts[1] not in files
            files[path.parts[1]]=(tar.extractfile(member).read(),member.mode & 0o777)
assert set(files)=={'pmp4010_manual_test','README.md','使用说明.md','ELF_DEPENDENCIES.txt','SOURCE_SHA256.json','SHA256SUMS'}
assert hashlib.sha256(files['pmp4010_manual_test'][0]).hexdigest()=='758f9107d372622f1104fd2d4972bcb22191f4da6d851913bbcae3ada15a26c2'
target.mkdir(mode=0o755,exist_ok=False)
for name,(data,mode) in files.items():
    p=target/name
    with p.open('xb') as f: f.write(data)
    p.chmod(mode)
print('remote_directory='+str(target),flush=True)
p=subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=target)
print('sha256sum_rc='+str(p.returncode),flush=True)
p.check_returncode()
with (target/'run-manual.sh').open('x') as f: f.write(WRAPPER)
(target/'run-manual.sh').chmod(0o755)
subprocess.run(['sh','-n',str(target/'run-manual.sh')],check=True)
print('wrapper_sha256='+hashlib.sha256((target/'run-manual.sh').read_bytes()).hexdigest())
print('binary_sha256='+hashlib.sha256((target/'pmp4010_manual_test').read_bytes()).hexdigest())
old_after=hashlib.sha256(old.read_bytes()).hexdigest() if old.exists() else None
assert old_before==old_after
print('old_python_entry_sha256_before='+str(old_before))
print('old_python_entry_sha256_after='+str(old_after))
archive.unlink()
stage.rmdir()
print('upload_stage_removed=True')
