#!/usr/bin/env python3
"""Install the frozen redesigned UI Python payload and switch managed aliases after no-I/O checks."""
import hashlib,importlib.util,json,pathlib,shlex
E=pathlib.Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('record',E/'acceptance/record.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);r.OUT=E/'deployment'
info=json.loads((E/'package/summary.json').read_text());archive=pathlib.Path(info['archive'])
assert hashlib.sha256(archive.read_bytes()).hexdigest()==info['archive_sha256']
for name,digest in info['source_freeze'].items():assert hashlib.sha256((r.ROOT/name).read_bytes()).hexdigest()==digest
SSH=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=8','root@192.168.0.188']
target='/opt/km3/hwtools/manual-test-20261020-ui-'+info['archive_sha256'][:8]
common='''import hashlib,json,os,pathlib,platform,subprocess,tarfile,tempfile
old='/opt/km3/hwtools/manual-test-20261020-mmps-76f8db43'
root=pathlib.Path('/opt/km3/hwtools')
def aliases():
    result={}
    for name in ['pmp4010-manual-test','pmp4010-cpp-test']:
        p=root/name
        assert p.is_symlink() and os.readlink(p)==old,'Unexpected alias: '+str(p)
        result[name]=os.readlink(p)
    return result
'''
def remote(label,script):
    (r.OUT/(label+'.remote.py')).write_text(script)
    return r.run(label,SSH+['python3 -I -B -c '+shlex.quote(script)])
pre=remote('preflight',common+'\nTARGET='+repr(target)+'\n'+'''assert pathlib.Path('/etc/machine-id').read_text().strip()=='b4b158c71fc04ebeb83d3a79a528a1de'
assert platform.machine()=='aarch64'
print('hostname='+platform.node());print('machine_id='+pathlib.Path('/etc/machine-id').read_text().strip())
print('architecture='+platform.machine());print('aliases_before='+json.dumps(aliases()))
assert not pathlib.Path(TARGET).exists()
print('upload_stage='+tempfile.mkdtemp(prefix='.manual-ui-upload-',dir=str(root)))
''').decode()
stage=[s.split('=',1)[1] for s in pre.splitlines() if s.startswith('upload_stage=')][0]
assert stage.startswith('/opt/km3/hwtools/.manual-ui-upload-') and '\n' not in stage
r.run('upload',['scp','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=8',str(archive),'root@192.168.0.188:'+stage+'/payload.tar.gz'])
remote('install-and-manifest',common+'\nTARGET='+repr(target)+'\nSTAGE='+repr(stage)+'\nARCHIVE_SHA='+repr(info['archive_sha256'])+'\nMANIFEST_SHA='+repr(info['manifest_sha256'])+'\nARCHIVE_MEMBERS='+repr(info['archive_members'])+'\nMANIFEST_ENTRIES='+repr(info['manifest_entries'])+'\n'+'''stage=pathlib.Path(STAGE);archive=stage/'payload.tar.gz';target=pathlib.Path(TARGET)
assert hashlib.sha256(archive.read_bytes()).hexdigest()==ARCHIVE_SHA
aliases();assert not target.exists()
with tarfile.open(archive,'r:gz') as tar:
    members=tar.getmembers();assert len(members)==ARCHIVE_MEMBERS
    raw=tar.extractfile('payload-manifest.json').read()
    assert hashlib.sha256(raw).hexdigest()==MANIFEST_SHA
    manifest=json.loads(raw);entries={x['path']:x for x in manifest['files']}
    assert len(entries)==MANIFEST_ENTRIES and {m.name for m in members}==set(entries)|{'payload-manifest.json'}
    payload=stage/'payload';payload.mkdir()
    for m in members:
        p=pathlib.PurePosixPath(m.name);assert not p.is_absolute() and '..' not in p.parts
        dest=payload/m.name;dest.parent.mkdir(parents=True,exist_ok=True)
        if m.issym():
            assert m.name in ['drivers/pmp4010/libpmp4010_velocity.so','drivers/pmp4010/libpmp4010_velocity.so.1']
            assert entries[m.name]['symlink']==m.linkname and '/' not in m.linkname and '..' not in m.linkname
        else:
            assert m.isfile()
            data=tar.extractfile(m).read()
            if m.name!='payload-manifest.json':assert hashlib.sha256(data).hexdigest()==entries[m.name]['sha256'] and len(data)==entries[m.name]['size']
            with dest.open('xb') as out:out.write(data)
            dest.chmod(m.mode&0o777)
    for m in members:
        if m.issym():(payload/m.name).symlink_to(m.linkname)
for name,x in entries.items():
    p=payload/name
    if 'symlink' in x:assert p.is_symlink() and os.readlink(p)==x['symlink']
    else:assert not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==x['sha256']
assert hashlib.sha256((payload/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0').read_bytes()).hexdigest()=='59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
os.rename(payload,target)
print('archive_sha256='+ARCHIVE_SHA);print('manifest_sha256='+MANIFEST_SHA)
print('payload_entries_checked='+str(len(entries)));print('target='+str(target));print('arm_library_unchanged=True')
archive.unlink();stage.rmdir();print('temporary_upload_removed=True')
''')
r.run('arm-load-smoke',SSH+['python3 -I -B '+target+'/load_smoke.py --check-missing-library'])
r.run('launcher-syntax',SSH+['sh -n '+target+'/run-manual.sh'])
r.run('direct-help',SSH+['python3 -I -B '+target+'/drivers/pmp4010/pmp4010.py --help'])
remote('switch-aliases',common+'\nTARGET='+repr(target)+'\nMANIFEST_SHA='+repr(info['manifest_sha256'])+'\n'+'''print('aliases_before='+json.dumps(aliases()))
target=pathlib.Path(TARGET)
assert hashlib.sha256((target/'payload-manifest.json').read_bytes()).hexdigest()==MANIFEST_SHA
for name in ['pmp4010-manual-test','pmp4010-cpp-test']:
    link=root/name;temporary=root/('.manual-ui-link-'+os.urandom(6).hex())
    assert link.is_symlink() and os.readlink(link)==old
    temporary.symlink_to(TARGET);os.replace(temporary,link)
    assert link.resolve()==target
    print(name+'='+os.readlink(link))
    print(name+'_launcher_sha256='+hashlib.sha256((link/'run-manual.sh').read_bytes()).hexdigest())
print('temporary_links_remaining=0')
print('alias_update_semantics=each os.replace is atomic; the two aliases are not one transaction')
''')
(r.OUT/'result.json').write_text(json.dumps(dict(target=target,archive_sha256=info['archive_sha256'],manifest_sha256=info['manifest_sha256'],stable_entry='/opt/km3/hwtools/pmp4010-manual-test',compatibility_entry='/opt/km3/hwtools/pmp4010-cpp-test',hardware_acceptance=False),ensure_ascii=False,indent=2)+'\n')
