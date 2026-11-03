import os,pathlib,runpy,sys
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
