#!/usr/bin/env python3
import datetime, hashlib, json, pathlib, shlex, subprocess, sys, tarfile
from zoneinfo import ZoneInfo
ROOT=pathlib.Path('/home/zakiu/km3')
OUT=pathlib.Path(__file__).resolve().parent
SSH=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=8','root@192.168.0.188']
TARGET='/opt/km3/hwtools/pmp4010-cpp-test-20261020-758f9107'
ARCHIVE=ROOT/'build-pmp4010-cpp-arm64/pmp4010-cpp-manual-arm64.tar.gz'
PKG=ROOT/'docs/硬件手册/PMP4010/示例C++手动测试-2026-10-20-证据/package'
def stamp(): return datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
def run(label,argv):
    start=stamp(); p=subprocess.run(argv,cwd=ROOT,capture_output=True)
    (OUT/(label+'.stdout')).write_bytes(p.stdout); (OUT/(label+'.stderr')).write_bytes(p.stderr)
    (OUT/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,cwd=str(ROOT),start=start,end=stamp(),rc=p.returncode),ensure_ascii=False,indent=2)+'\n')
    print(label, 'rc='+str(p.returncode)); print(p.stdout.decode(errors='replace')); print(p.stderr.decode(errors='replace'))
    if p.returncode: raise SystemExit(p.returncode)
    return p.stdout

def remote(label,script):
    (OUT/(label+'.remote.py')).write_text(script)
    return run(label,SSH+['python3 -I -B -c '+shlex.quote(script)])

def verify():
    assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()=='62a92542963d6c5ad9d71b16498e7f98e259762ef3a16204c9c7bfaf78d5f04e'
    manifest=json.loads((PKG/'SOURCE_SHA256.json').read_text())
    for name,digest in manifest.items():
        path=pathlib.PurePosixPath(name)
        assert not path.is_absolute() and '..' not in path.parts and path.parts[0]!='harness' and not name.startswith('docs/架构设计/')
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    with tarfile.open(ARCHIVE,'r:gz') as tar:
        members=tar.getmembers()
        for member in members:
            path=pathlib.PurePosixPath(member.name)
            assert not path.is_absolute() and '..' not in path.parts and path.parts[0]=='pmp4010-cpp-manual-arm64'
            assert member.isdir() or member.isfile()
            if member.isfile(): assert tar.extractfile(member).read()==(PKG/pathlib.Path(*path.parts[1:])).read_bytes(),member.name
        assert len(members)==7
    result=dict(time=stamp(),archive_sha256=hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),binary_sha256=hashlib.sha256((PKG/'pmp4010_manual_test').read_bytes()).hexdigest(),source_entries_checked=len(manifest),safe_archive_members=len(members),archive_member_bytes_match_package=True)
    assert result['binary_sha256']=='758f9107d372622f1104fd2d4972bcb22191f4da6d851913bbcae3ada15a26c2'
    (OUT/'local-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(result))
    run('local-package-check',['bash','-c','cd '+shlex.quote(str(PKG))+' && sha256sum -c SHA256SUMS'])

if __name__=='__main__':
    if sys.argv[1]=='preflight':
        verify()
        run('known-host-fingerprint',['ssh-keygen','-F','192.168.0.188','-l'])
        remote('board-identity', '''import os, platform, pathlib, subprocess, json
print('hostname='+platform.node())
print('machine_id='+pathlib.Path('/etc/machine-id').read_text().strip())
print('architecture='+platform.machine())
print('glibc='+os.confstr('CS_GNU_LIBC_VERSION'))
print(pathlib.Path('/etc/os-release').read_text())
print(subprocess.check_output(['df','-h','/opt/km3/hwtools'],text=True))
print('target_exists='+str(pathlib.Path('/opt/km3/hwtools/pmp4010-cpp-test-20261020-758f9107').exists()))
assert platform.machine()=='aarch64'
version=tuple(map(int,os.confstr('CS_GNU_LIBC_VERSION').split()[-1].split('.')))
assert version >= (2,34)
''')
    elif sys.argv[1]=='deploy':
        verify()
        stage=remote('create-upload-stage', '''import tempfile
print(tempfile.mkdtemp(prefix='.pmp4010-cpp-upload-758f9107-',dir='/opt/km3/hwtools'))
''').decode().strip()
        assert stage.startswith('/opt/km3/hwtools/.pmp4010-cpp-upload-758f9107-') and '\n' not in stage
        run('upload-archive',['scp','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=8',str(ARCHIVE),'root@192.168.0.188:'+stage+'/package.tar.gz'])
        wrapper='''#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/pmp4010_manual_test" --interface can1 --node 1 --mm-per-rev 4 "$@"
'''
        (OUT/'run-manual.sh').write_text(wrapper)
        script='''import hashlib, pathlib, tarfile, subprocess, json, os
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
'''
        script='STAGE='+repr(stage)+'\nTARGET='+repr(TARGET)+'\nWRAPPER='+repr(wrapper)+'\n'+script
        remote('install-and-verify',script)
        run('board-version',SSH+[shlex.quote(TARGET+'/pmp4010_manual_test')+' --version'])
        run('board-help',SSH+[shlex.quote(TARGET+'/pmp4010_manual_test')+' --help'])
        run('board-wrapper-help',SSH+[shlex.quote(TARGET+'/run-manual.sh')+' --help'])
