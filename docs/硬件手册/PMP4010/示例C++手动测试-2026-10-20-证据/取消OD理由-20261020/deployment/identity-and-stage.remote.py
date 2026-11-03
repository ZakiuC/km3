import hashlib, json, os, pathlib, platform, re, subprocess, tarfile, tempfile
stable=pathlib.Path('/opt/km3/hwtools/pmp4010-cpp-test')
def managed():
    if not stable.exists() and not stable.is_symlink(): return None
    assert stable.is_symlink(),'Stable entry exists and is not a managed symlink'
    old=os.readlink(stable)
    resolved=pathlib.Path(old) if old.startswith('/') else stable.parent/old
    assert str(resolved.parent)=='/opt/km3/hwtools' and re.fullmatch(r'pmp4010-cpp-test-20261020-[0-9a-f]{8}',resolved.name),'Unmanaged stable link target'
    return old
print('hostname='+platform.node())
print('machine_id='+pathlib.Path('/etc/machine-id').read_text().strip())
print('architecture='+platform.machine())
print('glibc='+os.confstr('CS_GNU_LIBC_VERSION'))
assert platform.machine()=='aarch64'
assert pathlib.Path('/etc/machine-id').read_text().strip()=='b4b158c71fc04ebeb83d3a79a528a1de'
assert tuple(map(int,os.confstr('CS_GNU_LIBC_VERSION').split()[-1].split('.'))) >= (2,34)
print('stable_previous='+str(managed()))
assert not pathlib.Path('/opt/km3/hwtools/pmp4010-cpp-test-20261020-488add26').exists()
print('upload_stage='+tempfile.mkdtemp(prefix='.pmp4010-no-od-upload-',dir='/opt/km3/hwtools'))
