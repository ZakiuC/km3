import hashlib,json,os,pathlib,platform,subprocess,tarfile,tempfile
old='/opt/km3/hwtools/manual-test-20261020-e137c603'
root=pathlib.Path('/opt/km3/hwtools')
def aliases():
    result={}
    for name in ['pmp4010-manual-test','pmp4010-cpp-test']:
        p=root/name
        assert p.is_symlink() and os.readlink(p)==old,'Unexpected alias: '+str(p)
        result[name]=os.readlink(p)
    return result

TARGET='/opt/km3/hwtools/manual-test-20261020-mmps-76f8db43'
MANIFEST_SHA='2903b456b07412c3cc4a69a2f018f786fa0bbebfc931862dfc407571ea4526b6'
print('aliases_before='+json.dumps(aliases()))
target=pathlib.Path(TARGET)
assert hashlib.sha256((target/'payload-manifest.json').read_bytes()).hexdigest()==MANIFEST_SHA
for name in ['pmp4010-manual-test','pmp4010-cpp-test']:
    link=root/name;temporary=root/('.manual-mmps-link-'+os.urandom(6).hex())
    assert link.is_symlink() and os.readlink(link)==old
    temporary.symlink_to(TARGET);os.replace(temporary,link)
    assert link.resolve()==target
    print(name+'='+os.readlink(link))
    print(name+'_launcher_sha256='+hashlib.sha256((link/'run-manual.sh').read_bytes()).hexdigest())
print('temporary_links_remaining=0')
