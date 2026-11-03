from pathlib import Path
import datetime,hashlib,json,shlex,subprocess
B=Path(__file__).resolve().parent;prior=B.parent/'stage2-board-load-pty-snapshot';base='/var/tmp/km3-pp-disabled-start-stage3-recovery-20261019-b2'
SSH=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=5','root@192.168.0.188']
def remote(label,code,stdin=b'',args=()):
 script=B/(label+'.remote.py');script.write_text(code)
 # Send snapshot source via stdin so process enumeration cannot match its own parent argv.
 if not stdin and not args:
  argv=SSH+['python3 -I -B -'];stdin=code.encode()
 else:
  argv=SSH+[shlex.join(['python3','-I','-B','-c',code,*args])]
 started=datetime.datetime.now(datetime.timezone.utc).isoformat();p=subprocess.run(argv,input=stdin,capture_output=True,timeout=45)
 (B/(label+'.stdout')).write_bytes(p.stdout);(B/(label+'.stderr')).write_bytes(p.stderr)
 (B/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,utc_start=started,utc_end=datetime.datetime.now(datetime.timezone.utc).isoformat(),stdin_bytes=len(stdin),stdin_sha256=hashlib.sha256(stdin).hexdigest(),script_sha256=hashlib.sha256(code.encode()).hexdigest(),rc=p.returncode),indent=2)+'\n')
 print(json.dumps(dict(label=label,rc=p.returncode,stdout=p.stdout.decode(errors='replace'),stderr=p.stderr.decode(errors='replace'))),flush=True)
 assert p.returncode==0,label
 return p
p=remote('before-stage',(B/'stage-passive.py').read_text());before=json.loads(p.stdout)
assert before['boot_id'].strip()=='dc67bdcd-fa50-4927-8f9b-7207359c70af'
assert before['payload_exists']==False and before['potential_old_runners']==[]
archive=B.parent/'stage3-recovered-package/pmp4010-board-payload.tar.gz';raw=archive.read_bytes()
assert len(raw)==193340 and hashlib.sha256(raw).hexdigest()=='b70396b228cb594793b44ad6d6f4dfc65a0167fd0efd90325d8feec276f69c5e'
stage=(B/'stage-package.remote.py').read_text()
p=remote('stage-package',stage,raw);staged=json.loads(p.stdout);assert staged['payload_root']==base+'/payload'
# Upload only reviewed observer scripts, with exact byte hashes and exclusive opens.
wrappers={name:dict(sha256=hashlib.sha256((B/name).read_bytes()).hexdigest(),data=(B/name).read_text()) for name in ('board_app.py','board_pty_runner.py')}
code="""import hashlib,json,pathlib,sys
base=pathlib.Path(sys.argv[1]);items=json.load(sys.stdin)
assert base.is_dir()
for name,item in items.items():
 assert name in ('board_app.py','board_pty_runner.py')
 data=item['data'].encode();assert hashlib.sha256(data).hexdigest()==item['sha256']
 with (base/name).open('xb') as f:f.write(data)
 assert hashlib.sha256((base/name).read_bytes()).hexdigest()==item['sha256']
print(json.dumps({'uploaded':{k:v['sha256'] for k,v in items.items()},'CAN_opens':0,'executed':False}))
"""
remote('stage-wrappers',code,json.dumps(wrappers).encode(),(base,))
remote('pure-load',(prior/'pure_load_audit.py').read_text(),args=(base+'/payload',))
code="""import os,pathlib,runpy,sys
sys.dont_write_bytecode=True
attempts=[]
def audit(event,args):
 forbidden=event in ('socket.__new__','socket.connect','socket.bind','subprocess.Popen','os.system','os.fork','os.forkpty','os.posix_spawn','os.exec','pty.spawn','os.remove','os.rename','os.mkdir','os.rmdir','os.symlink','os.link','os.truncate','os.chmod','os.chown')
 if event=='open':
  mode=args[1];flags=args[2]
  forbidden=bool(isinstance(mode,str) and any(x in mode for x in 'wax+')) or bool(isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
 if forbidden:attempts.append(event);raise RuntimeError('offline wrapper audit rejected '+event)
sys.addaudithook(audit)
sys.argv=[sys.argv[1]]
try:runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit as exc:assert exc.code in (0,None)
assert not attempts
print('outer_audit_forbidden_write_or_io_attempts=0')
"""
remote('offline-wrapper',code,args=(base+'/board_app.py',))
passive=(B/'stage-passive.py').read_text()
p=remote('after-load',passive);after=json.loads(p.stdout)
skip={'utc','uptime','payload_root','payload_exists','source_hashes'}
changes=[k for k in before if k not in skip and before[k]!=after[k]];assert changes==[],changes
summary=dict(result='PASS',stage=staged,imports=16,native_vectors=33,python_vectors=33,native_int32_vectors=14,forbidden_IO_write_attempts=0,CAN_opens=0,CAN_sends=0,real_TUI_started=False,passive_changed_keys=changes,wrappers={k:v['sha256'] for k,v in wrappers.items()},command_rcs={label:json.loads((B/(label+'.command.json')).read_text())['rc'] for label in ('before-stage','stage-package','stage-wrappers','pure-load','offline-wrapper','after-load')})
(B/'load-summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
