import hashlib,json,os,pathlib,platform,subprocess,tarfile,tempfile
old='/opt/km3/hwtools/manual-test-20261020-mmps-76f8db43'
root=pathlib.Path('/opt/km3/hwtools')
def aliases():
    result={}
    for name in ['pmp4010-manual-test','pmp4010-cpp-test']:
        p=root/name
        assert p.is_symlink() and os.readlink(p)==old,'Unexpected alias: '+str(p)
        result[name]=os.readlink(p)
    return result

TARGET='/opt/km3/hwtools/manual-test-20261020-ui-3970cf62'
STAGE='/opt/km3/hwtools/.manual-ui-upload-nqra8sna'
ARCHIVE_SHA='3970cf6241a05348d5abe5efde59039178f2b41144de5c7fb24358538f3d57bd'
MANIFEST_SHA='ce2dfed84643fd5ac30d83b194cf32ca6d25a5a2559d676e4710403696b20875'
ARCHIVE_MEMBERS=29
MANIFEST_ENTRIES=28
stage=pathlib.Path(STAGE);archive=stage/'payload.tar.gz';target=pathlib.Path(TARGET)
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
