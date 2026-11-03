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
cpp=base.parent/'pmp4010-cpp-test';neutral=base.parent/'pmp4010-manual-test'
assert cpp.is_symlink() and os.readlink(cpp)=='/opt/km3/hwtools/pmp4010-cpp-test-20261020-488add26'
assert not neutral.exists() and not neutral.is_symlink() or neutral.is_symlink() and os.readlink(neutral)==str(base)
for p in [neutral,cpp]:
    print('before '+str(p)+'='+str(os.readlink(p) if p.is_symlink() else 'ABSENT'))
    temporary=p.parent/('.restore-manual-link-'+os.urandom(6).hex())
    temporary.symlink_to(str(base));os.replace(temporary,p)
    assert p.resolve()==base
    print('after '+str(p)+'='+os.readlink(p))
    assert hashlib.sha256((p/'run-manual.sh').read_bytes()).hexdigest()=='56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1'
print('resolved_launcher_sha256=56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1')
print('temporary_links_remaining=0')
