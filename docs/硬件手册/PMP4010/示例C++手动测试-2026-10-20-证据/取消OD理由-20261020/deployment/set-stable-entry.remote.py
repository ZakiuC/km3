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
old=managed()
print('stable_previous='+str(old))
assert pathlib.Path(TARGET,'pmp4010_manual_test').is_file()
link=stable.parent/('.pmp4010-cpp-test-link-'+os.urandom(6).hex())
link.symlink_to(TARGET)
os.replace(link,stable)
print('stable_entry='+str(stable));print('stable_target='+os.readlink(stable))
