#!/usr/bin/env python3
import datetime,hashlib,json,pathlib,shlex,subprocess,sys,tarfile
from zoneinfo import ZoneInfo
ROOT=pathlib.Path('/home/zakiu/km3')
OUT=pathlib.Path(__file__).resolve().parent
SSH=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=8','root@192.168.0.188']
BASE='/opt/km3/hwtools/manual-test-20261020-e137c603'
def stamp():return datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
def run(label,argv):
    assert not (OUT/(label+'.command.json')).exists()
    start=stamp();p=subprocess.run(argv,cwd=ROOT,capture_output=True)
    (OUT/(label+'.stdout')).write_bytes(p.stdout);(OUT/(label+'.stderr')).write_bytes(p.stderr)
    (OUT/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,cwd=str(ROOT),start=start,end=stamp(),rc=p.returncode),ensure_ascii=False,indent=2)+'\n')
    print(label,'rc='+str(p.returncode));print(p.stdout.decode(errors='replace'));print(p.stderr.decode(errors='replace'))
    if p.returncode:raise SystemExit(p.returncode)
    return p.stdout

def remote(label,script):
    (OUT/(label+'.remote.py')).write_text(script)
    return run(label,SSH+['python3 -I -B -c '+shlex.quote(script)])

VERIFY='''import hashlib,json,os,pathlib,platform
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
'''
if __name__=='__main__':
    if sys.argv[1]=='restore':
        archive=ROOT/'docs/硬件手册/PMP4010/抱闸手动测试-2026-10-19-证据/package/pmp4010-brake-offline-payload.tar.gz'
        assert hashlib.sha256(archive.read_bytes()).hexdigest()=='e137c603d57c584134f14f006bcae482d3b999ce1d2dda8dd25b19005a80b237'
        with tarfile.open(archive) as tar:
            sources={name:tar.extractfile(name).read().decode() for name in ['run-manual.sh','load_smoke.py','drivers/pmp4010/pmp4010_simple.py']}
        (OUT/'frozen-source-review.json').write_text(json.dumps(dict(archive=str(archive),archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),sources=sources),ensure_ascii=False,indent=2)+'\n')
        remote('before-and-manifest',VERIFY+'''assert pathlib.Path('/etc/machine-id').read_text().strip()=='b4b158c71fc04ebeb83d3a79a528a1de'
print('hostname='+platform.node());print('architecture='+platform.machine())
for name in ['pmp4010-manual-test','pmp4010-cpp-test']:
    p=base.parent/name
    print(name+'='+str(os.readlink(p) if p.is_symlink() else 'ABSENT' if not p.exists() else 'NON_SYMLINK'))
cpp=base.parent/'pmp4010-cpp-test'
assert cpp.is_symlink() and os.readlink(cpp)=='/opt/km3/hwtools/pmp4010-cpp-test-20261020-488add26'
neutral=base.parent/'pmp4010-manual-test'
assert not neutral.exists() and not neutral.is_symlink() or neutral.is_symlink() and os.readlink(neutral)==str(base)
''')
        run('arm-load-smoke',SSH+['python3 -I -B '+BASE+'/load_smoke.py --check-missing-library'])
        run('launcher-syntax',SSH+['sh -n '+BASE+'/run-manual.sh'])
        run('direct-help',SSH+['python3 -I -B '+BASE+'/drivers/pmp4010/pmp4010.py --help'])
        remote('restore-links',VERIFY+'''cpp=base.parent/'pmp4010-cpp-test';neutral=base.parent/'pmp4010-manual-test'
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
''')
