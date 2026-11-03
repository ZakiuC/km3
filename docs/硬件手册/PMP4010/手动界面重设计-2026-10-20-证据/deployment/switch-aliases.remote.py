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
MANIFEST_SHA='ce2dfed84643fd5ac30d83b194cf32ca6d25a5a2559d676e4710403696b20875'
print('aliases_before='+json.dumps(aliases()))
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
