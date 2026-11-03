import hashlib,json,os,pathlib,platform
base=pathlib.Path('/opt/km3/hwtools/manual-test-20261020-e137c603')
manifest=base/'payload-manifest.json'
assert hashlib.sha256(manifest.read_bytes()).hexdigest()=='8cf6fa0d6efdfb3831ff7e5397fd67314aa3ea433969e07d51478296634caa51'
items=json.loads(manifest.read_text())['files']
for item in items:
    p=base/item['path']
    if 'symlink' in item:assert p.is_symlink() and os.readlink(p)==item['symlink'],item['path']
    else:assert not p.is_symlink() and p.stat().st_size==item['size'] and hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],item['path']
assert hashlib.sha256((base/'run-manual.sh').read_bytes()).hexdigest()=='56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1'
print('payload_manifest=PASS entries='+str(len(items)))
print('launcher_sha256='+hashlib.sha256((base/'run-manual.sh').read_bytes()).hexdigest())
assert pathlib.Path('/etc/machine-id').read_text().strip()=='b4b158c71fc04ebeb83d3a79a528a1de'
print('hostname='+platform.node());print('architecture='+platform.machine())
for name in ['pmp4010-manual-test','pmp4010-cpp-test']:
    p=base.parent/name
    print(name+'='+str(os.readlink(p) if p.is_symlink() else 'ABSENT' if not p.exists() else 'NON_SYMLINK'))
cpp=base.parent/'pmp4010-cpp-test'
assert cpp.is_symlink() and os.readlink(cpp)=='/opt/km3/hwtools/pmp4010-cpp-test-20261020-488add26'
neutral=base.parent/'pmp4010-manual-test'
assert not neutral.exists() and not neutral.is_symlink() or neutral.is_symlink() and os.readlink(neutral)==str(base)
