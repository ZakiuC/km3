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
assert pathlib.Path('/etc/machine-id').read_text().strip()=='b4b158c71fc04ebeb83d3a79a528a1de'
assert platform.machine()=='aarch64'
print('hostname='+platform.node());print('machine_id='+pathlib.Path('/etc/machine-id').read_text().strip())
print('architecture='+platform.machine());print('aliases_before='+json.dumps(aliases()))
assert not pathlib.Path(TARGET).exists()
print('upload_stage='+tempfile.mkdtemp(prefix='.manual-ui-upload-',dir=str(root)))
