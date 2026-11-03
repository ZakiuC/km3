"""Extra Python audit fence: package pure load only, forbid writes and I/O."""
import os,pathlib,runpy,sys
sys.dont_write_bytecode=True
attempts=[]
def audit(event,args):
 forbidden=event in ('socket.__new__','socket.connect','socket.bind','subprocess.Popen','os.system','os.fork','os.forkpty','os.posix_spawn','os.exec','pty.spawn','os.remove','os.rename','os.mkdir','os.rmdir','os.symlink','os.link','os.truncate','os.chmod','os.chown')
 if event=='open':
  mode=args[1];flags=args[2]
  forbidden=bool(isinstance(mode,str) and any(x in mode for x in 'wax+')) or bool(isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
 if forbidden:
  attempts.append(event);raise RuntimeError('pure load audit fence rejected '+event)
root=pathlib.Path(sys.argv[1]).resolve(strict=True)
sys.addaudithook(audit)
sys.argv=[str(root/'load_smoke.py'),'--check-missing-library']
runpy.run_path(str(root/'load_smoke.py'),run_name='__main__')
assert not attempts
print('outer_audit_forbidden_write_or_io_attempts=0')
