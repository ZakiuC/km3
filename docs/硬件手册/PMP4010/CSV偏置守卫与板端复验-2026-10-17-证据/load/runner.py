import datetime, hashlib, json, pathlib, shlex, subprocess, sys

BASE = pathlib.Path('/tmp/km3-pmp4010-board-20261017-reload-evidence')
BASE.mkdir(exist_ok=False)
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'root@192.168.0.188']
PACKAGE = pathlib.Path('/tmp/km3-pmp4010-board-20261017-csvguard-final-package/pmp4010-board-payload.tar.gz')
EXPECTED = 'ab853ce5565e55cc76c9782617f826a60c547d05339dc5c252f16e85eb1c3c39'
records = json.loads((BASE / 'command-index.json').read_text()) if (BASE / 'command-index.json').exists() else []
def run(name, argv, stdin=None):
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    p = subprocess.run(argv, input=stdin, text=True, capture_output=True, timeout=90)
    record = dict(name=name, argv=argv, stdin=stdin, started_utc=started, exitcode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    assert not (BASE / (name + '.json')).exists(), 'refuse log overwrite'
    (BASE / (name + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    (BASE / (name + '.stdout')).write_text(p.stdout)
    (BASE / (name + '.stderr')).write_text(p.stderr)
    records.append(record)
    (BASE / 'command-index.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    print(name, 'exitcode=', p.returncode, flush=True)
    print(p.stdout, end='', flush=True)
    print(p.stderr, end='', file=sys.stderr, flush=True)
    if p.returncode: raise RuntimeError(name + ' failed')
    return p.stdout

assert PACKAGE.stat().st_size == 182701
assert hashlib.sha256(PACKAGE.read_bytes()).hexdigest() == EXPECTED
snapshot = r'''
import hashlib,json,pathlib,subprocess,socket
config=pathlib.Path('/etc/km3/device.json')
device=json.loads(config.read_text())
current=pathlib.Path('/opt/km3/hwtools/current')
root=current.resolve(strict=True)
hashes={}
for p in sorted(root.rglob('*')):
 if p.is_file(): hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
for p in sorted(pathlib.Path('/etc/km3').rglob('*')):
 if p.is_file(): hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
services={}
for service in ['km3-rt-controller','km3-core','km3-video']:
 r=subprocess.run(['systemctl','show',service,'--property=ActiveState,SubState,MainPID,ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic'],capture_output=True,text=True)
 if r.returncode: raise RuntimeError(r.stderr)
 services[service]=dict(line.split('=',1) for line in r.stdout.splitlines())
model=pathlib.Path('/proc/device-tree/model').read_bytes().rstrip(b'\0').decode()
result={'device_id':device.get('device',{}).get('id'),'hostname':socket.gethostname(),'model':model,'current':str(root),'file_sha256':hashes,'services':services}
print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
if result['device_id']!='km3-test-001' or result['hostname']!='RK3588-Tronlong' or model!='TL3588-EVM': raise RuntimeError('identity mismatch')
if str(root)!='/opt/km3/hwtools/releases/0.1.5+zh-5e31e29': raise RuntimeError('current mismatch')
if any(v.get('ActiveState')!='active' or v.get('SubState')!='running' for v in services.values()): raise RuntimeError('service baseline mismatch')
'''
before = json.loads(run('01-before', SSH + ['python3 -I -B -'], snapshot))
remote = run('02-mktemp', SSH + ['mktemp -d /tmp/km3-pmp4010-reload-20261017-XXXXXXXX']).strip()
import re
assert re.fullmatch(r'/tmp/km3-pmp4010-reload-20261017-[A-Za-z0-9]{8}', remote), remote
(BASE / 'remote-directory.txt').write_text(remote+'\n')
run('03-scp', ['scp','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',str(PACKAGE),'root@192.168.0.188:'+remote+'/pmp4010-board-payload.tar.gz'])
extract = r'''
import hashlib,json,pathlib,sys,tarfile,posixpath,os
root=pathlib.Path(sys.argv[1]); archive=root/'pmp4010-board-payload.tar.gz'
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
assert archive.stat().st_size==182701
assert digest=='ab853ce5565e55cc76c9782617f826a60c547d05339dc5c252f16e85eb1c3c39'
dest=root/'payload'
assert not dest.exists()
with tarfile.open(archive,'r:gz') as tar:
 members=tar.getmembers(); seen=set(); symlinks=set()
 for m in members:
  p=pathlib.PurePosixPath(m.name)
  assert not p.is_absolute() and '..' not in p.parts and str(p) not in ('','.','/')
  assert m.name not in seen; seen.add(m.name)
  assert m.isfile() or m.isdir() or m.issym()
  if m.issym():
   target=pathlib.PurePosixPath(m.linkname)
   assert not target.is_absolute()
   resolved=posixpath.normpath(posixpath.join(str(p.parent),m.linkname))
   assert resolved!='..' and not resolved.startswith('../')
   symlinks.add(p)
 for m in members:
  assert not any(parent in symlinks for parent in pathlib.PurePosixPath(m.name).parents)
 dest.mkdir(mode=0o700)
 for m in members:
  p=dest/m.name; p.parent.mkdir(parents=True,exist_ok=True)
  if m.isdir(): p.mkdir(exist_ok=True)
  elif m.issym(): p.symlink_to(m.linkname)
  else:
   with p.open('xb') as out: out.write(tar.extractfile(m).read())
 for p in dest.rglob('*'):
  if p.is_symlink(): assert p.resolve(strict=True).is_relative_to(dest.resolve())
print(json.dumps({'archive_size':archive.stat().st_size,'archive_sha256':digest,'members':len(members),'safe_extract':True,'destination':str(dest)},indent=2))
'''
run('04-verify-extract',SSH + ['python3 -I -B - '+shlex.quote(remote)],extract)
library=remote+'/payload/drivers/pmp4010/libpmp4010_velocity.so.1.0.0'
inspect = r'''
import hashlib,json,pathlib,shutil,subprocess,sys
p=pathlib.Path(sys.argv[1])
assert hashlib.sha256(p.read_bytes()).hexdigest()=='59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
for tool,args in [('readelf',['-h','-d','-V']),('ldd',[])]:
 found=shutil.which(tool)
 print(json.dumps({'tool':tool,'path':found,'argv':[tool]+args+[str(p)]}),flush=True)
 if found:
  r=subprocess.run([found]+args+[str(p)],capture_output=True,text=True)
  print(r.stdout,end=''); print(r.stderr,end='',file=sys.stderr); print('exitcode='+str(r.returncode),flush=True)
  if r.returncode: raise RuntimeError(tool+' failed')
print(json.dumps({'library':str(p),'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))
'''
run('05-elf-ldd',SSH+['python3 -I -B - '+shlex.quote(library)],inspect)
run('06-load-smoke',SSH+['python3 -I -B '+shlex.quote(remote+'/payload/load_smoke.py')+' --check-missing-library'])
after=json.loads(run('07-after',SSH+['python3 -I -B -'],snapshot))
equal=before==after
(BASE/'comparison.json').write_text(json.dumps({'identical':equal,'hardware_acceptance':False,'remote_directory':remote},indent=2)+'\n')
assert equal,'before/after differed; inspect raw snapshots'
print('COMPLETE before_after_identical=true hardware_acceptance=false remote_directory='+remote)
