#!/usr/bin/env python3
"""Deploy a verified artifact; run only version/help, never the hardware session."""
import hashlib, importlib.util, json, pathlib, shlex, tarfile
E=pathlib.Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('record',E/'acceptance/record.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);r.OUT=E/'deployment'
info=json.loads((E/'package-result.json').read_text())
archive=pathlib.Path(info['archive']);package=E/'package'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==info['archive_sha256']
assert hashlib.sha256((package/'pmp4010_manual_test').read_bytes()).hexdigest()==info['binary_sha256']
assert json.loads((package/'SOURCE_SHA256.json').read_text())==r.hashes()
with tarfile.open(archive,'r:gz') as tar:
    members=tar.getmembers()
    assert len(members)==8
    for m in members:
        p=pathlib.PurePosixPath(m.name)
        assert not p.is_absolute() and '..' not in p.parts and p.parts[0]=='pmp4010-cpp-no-od-manual-arm64'
        assert m.isdir() or m.isfile()
        if m.isfile(): assert len(p.parts)==2 and tar.extractfile(m).read()==(package/p.parts[1]).read_bytes()
SSH=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=8','root@192.168.0.188']
target='/opt/km3/hwtools/pmp4010-cpp-test-20261020-'+info['binary_sha256'][:8]
common='''import hashlib, json, os, pathlib, platform, re, subprocess, tarfile, tempfile
stable=pathlib.Path('/opt/km3/hwtools/pmp4010-cpp-test')
def managed():
    if not stable.exists() and not stable.is_symlink(): return None
    assert stable.is_symlink(),'Stable entry exists and is not a managed symlink'
    old=os.readlink(stable)
    resolved=pathlib.Path(old) if old.startswith('/') else stable.parent/old
    assert str(resolved.parent)=='/opt/km3/hwtools' and re.fullmatch(r'pmp4010-cpp-test-20261020-[0-9a-f]{8}',resolved.name),'Unmanaged stable link target'
    return old
'''
def remote(label,script):
    (r.OUT/(label+'.remote.py')).write_text(script)
    return r.run(label,SSH+['python3 -I -B -c '+shlex.quote(script)])
pre=remote('identity-and-stage',common+'''print('hostname='+platform.node())
print('machine_id='+pathlib.Path('/etc/machine-id').read_text().strip())
print('architecture='+platform.machine())
print('glibc='+os.confstr('CS_GNU_LIBC_VERSION'))
assert platform.machine()=='aarch64'
assert pathlib.Path('/etc/machine-id').read_text().strip()=='b4b158c71fc04ebeb83d3a79a528a1de'
assert tuple(map(int,os.confstr('CS_GNU_LIBC_VERSION').split()[-1].split('.'))) >= (2,34)
print('stable_previous='+str(managed()))
assert not pathlib.Path(TARGET).exists()
print('upload_stage='+tempfile.mkdtemp(prefix='.pmp4010-no-od-upload-',dir='/opt/km3/hwtools'))
'''.replace('TARGET',repr(target))).decode()
stage=[line.split('=',1)[1] for line in pre.splitlines() if line.startswith('upload_stage=')][0]
assert stage.startswith('/opt/km3/hwtools/.pmp4010-no-od-upload-') and '\n' not in stage
r.run('upload',['scp','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=8',str(archive),'root@192.168.0.188:'+stage+'/package.tar.gz'])
script=common+'\nTARGET='+repr(target)+'\nSTAGE='+repr(stage)+'\nARCHIVE_SHA='+repr(info['archive_sha256'])+'\nBINARY_SHA='+repr(info['binary_sha256'])+'\n'+'''stage=pathlib.Path(STAGE); target=pathlib.Path(TARGET); archive=stage/'package.tar.gz'
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
'''
remote('install-and-verify',script)
r.run('board-version',SSH+[shlex.quote(target+'/pmp4010_manual_test')+' --version'])
r.run('board-help',SSH+[shlex.quote(target+'/pmp4010_manual_test')+' --help'])
r.run('board-wrapper-help',SSH+[shlex.quote(target+'/run-manual.sh')+' --help'])
remote('set-stable-entry',common+'\nTARGET='+repr(target)+'\n'+'''old=managed()
print('stable_previous='+str(old))
assert pathlib.Path(TARGET,'pmp4010_manual_test').is_file()
link=stable.parent/('.pmp4010-cpp-test-link-'+os.urandom(6).hex())
link.symlink_to(TARGET)
os.replace(link,stable)
print('stable_entry='+str(stable));print('stable_target='+os.readlink(stable))
''')
result=dict(target=target,stable_entry='/opt/km3/hwtools/pmp4010-cpp-test',archive_sha256=info['archive_sha256'],binary_sha256=info['binary_sha256'],hardware_session_started=False)
(r.OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
