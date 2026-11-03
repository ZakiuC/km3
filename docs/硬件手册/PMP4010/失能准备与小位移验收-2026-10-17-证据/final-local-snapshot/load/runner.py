import datetime, hashlib, json, pathlib, shlex, subprocess, sys

BASE = pathlib.Path('/tmp/km3-pp-prepare-load-20261017')
BASE.mkdir(exist_ok=False)
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'root@192.168.0.188']
PACKAGE = pathlib.Path('/tmp/km3-pp-prepare-package-20261017/pmp4010-board-payload.tar.gz')
EXPECTED = 'e5e6be59dbed7611f3b4e5f6d5c53e38e2f7e1527afa1831e7b511e4a0fcacb6'
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

assert PACKAGE.stat().st_size == 184426
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
remote = run('02-mktemp', SSH + ['mktemp -d /tmp/km3-pp-prepare-load-20261017-XXXXXXXX']).strip()
import re
assert re.fullmatch(r'/tmp/km3-pp-prepare-load-20261017-[A-Za-z0-9]{8}', remote), remote
(BASE / 'remote-directory.txt').write_text(remote+'\n')
run('03-scp', ['scp','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',str(PACKAGE),'root@192.168.0.188:'+remote+'/pmp4010-board-payload.tar.gz'])
extract = r'''
import hashlib,json,pathlib,sys,tarfile,posixpath,os
root=pathlib.Path(sys.argv[1]); archive=root/'pmp4010-board-payload.tar.gz'
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
assert archive.stat().st_size==184426
assert digest=='e5e6be59dbed7611f3b4e5f6d5c53e38e2f7e1527afa1831e7b511e4a0fcacb6'
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

run("08-owner", SSH + ["python3 - <<'REMOTE_PY'\nimport os,pathlib,subprocess,shlex,re,json,hashlib\nunit='km3-rt-controller.service'\ndef run(args):\n r=subprocess.run(args,capture_output=True,text=True)\n if r.returncode: print(json.dumps({'inner_command':args,'rc':r.returncode,'stderr':r.stderr}))\n return r.stdout.strip()\ndef clean_args(args):\n out=[]; i=0\n flags={'--enable-sync','--require-config','--require-realtime','--validate-config-only'}\n vals={'--config','--period-us','--duration-s'}\n while i<len(args):\n  a=args[i]\n  if i==0: out.append(a)\n  elif a in flags: out.append(a)\n  elif a in vals and i+1<len(args):\n   v=args[i+1]\n   out.extend([a,v if (a=='--config' and v.startswith('/')) or (a!='--config' and re.fullmatch(r'[0-9]+',v)) else '<redacted>']);i+=1\n  else: out.append('<unrecognized-argument-redacted>')\n  i+=1\n return out\npid_s=run(['systemctl','show',unit,'--property=MainPID','--value'])\nprint('MainPID='+pid_s)\nraw=run(['systemctl','show',unit,'--property=ExecStart','--value'])\nm=re.search(r'argv\\[\\]=(.*?)(?: ; ignore_errors=| ; start_time=| })',raw)\nprint('ExecStart.filtered='+json.dumps(clean_args(shlex.split(m.group(1))) if m else ['<format-unparsed-redacted>']))\npid=int(pid_s); root=pathlib.Path('/proc')/str(pid)\nargs=[x.decode(errors='replace') for x in (root/'cmdline').read_bytes().split(b'\\0') if x]\nprint('argv.filtered='+json.dumps(clean_args(args)))\nprint('argv.enable_sync='+str('--enable-sync' in args))\nprint('exe='+os.readlink(root/'exe'))\nprint('exe.sha256='+hashlib.sha256((root/'exe').read_bytes()).hexdigest())\nsockets=[]\nfor f in sorted((root/'fd').iterdir(),key=lambda x:int(x.name)):\n try: link=os.readlink(f)\n except FileNotFoundError: continue\n if link.startswith(('socket:[','pipe:[','anon_inode:')) or link in ('/dev/null','/dev/console'):\n  print('fd.'+f.name+'='+link)\n else: print('fd.'+f.name+'=<filesystem-path-redacted>')\n if link.startswith('socket:['): sockets.append(link[8:-1])\nfor inode in sockets:\n types=[]\n for table in ('unix','tcp','tcp6','udp','udp6','raw','raw6','packet','netlink'):\n  q=root/'net'/table\n  if q.is_file():\n   for line in q.read_text().splitlines()[1:]:\n    parts=line.split()\n    idx=6 if table=='unix' else 8 if table=='packet' else 9\n    if len(parts)>idx and parts[idx]==inode: types.append(table)\n print('socket.'+inode+'.table_matches='+json.dumps(types))\nprint('MainPID.after='+run(['systemctl','show',unit,'--property=MainPID','--value']))\nfor name in ('rcvlist_all','rcvlist_eff','rcvlist_err','rcvlist_fil','rcvlist_inv','rcvlist_sff'):\n q=root/'net'/'can'/name\n if q.is_file(): print('\\n/proc/'+str(pid)+'/net/can/'+name+'\\n'+q.read_text())\nREMOTE_PY"])
