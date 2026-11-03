import datetime,json,os,pathlib,socket
P=pathlib.Path
root=P('/var/tmp/km3-pmp4010-simple-20261019-b579f3d5d3ab/payload/logs')
sessions=[]
if root.is_dir():
 for d in sorted(root.glob('manual-*')):
  if not d.is_dir() or d.is_symlink():continue
  files=[]
  for p in sorted(d.iterdir()):
   try:
    s=p.lstat();files.append(dict(name=p.name,is_regular=p.is_file() and not p.is_symlink(),is_symlink=p.is_symlink(),size=s.st_size,mtime_ns=s.st_mtime_ns,mtime_utc=datetime.datetime.fromtimestamp(s.st_mtime,datetime.timezone.utc).isoformat()))
   except OSError as e:files.append(dict(name=p.name,error=str(e)))
  sessions.append(dict(path=str(d),files=files))
processes=[]
for p in P('/proc').iterdir():
 if not p.name.isdigit() or int(p.name)==os.getpid():continue
 try:cmd=(p/'cmdline').read_bytes().replace(b'\x00',b' ').decode(errors='replace')
 except OSError:continue
 if any(s in cmd for s in ('pmp4010','pty_runner','board_app','candump','cansend','cangen','canplayer')):
  try:status=(p/'status').read_text()
  except OSError as e:status=str(e)
  try:cwd=os.readlink(p/'cwd')
  except OSError as e:cwd=str(e)
  processes.append(dict(pid=int(p.name),cmdline=cmd,status=status,cwd=cwd))
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),hostname=socket.gethostname(),boot_id=P('/proc/sys/kernel/random/boot_id').read_text(),logs_root=str(root),logs_root_exists=root.is_dir(),sessions=sessions,related_processes=processes,remote_file_writes=0,CAN_socket_opens=0,CAN_sends=0,process_control_operations=0),ensure_ascii=False,indent=2))
