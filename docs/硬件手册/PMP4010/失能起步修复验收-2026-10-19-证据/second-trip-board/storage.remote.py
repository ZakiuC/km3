import datetime,json,os,pathlib,subprocess
P=pathlib.Path
def run(args):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=5)
  return dict(argv=args,rc=p.returncode,stdout=p.stdout,stderr=p.stderr)
 except Exception as e:return dict(argv=args,error=str(e))
def read(p):
 try:return p.read_text()
 except OSError as e:return dict(error=str(e))
paths={}
for name in ('/var','/var/tmp','/tmp','/var/tmp/km3-pp-disabled-start-stage3-recovery-20261019-b2'):
 p=P(name)
 try:
  s=p.lstat();v=os.statvfs(p)
  paths[name]=dict(exists=True,is_symlink=p.is_symlink(),realpath=os.path.realpath(name),mode=oct(s.st_mode),device=s.st_dev,inode=s.st_ino,free_bytes=v.f_bavail*v.f_frsize,mount=run(['findmnt','-T',name,'-J','-o','TARGET,SOURCE,FSTYPE,OPTIONS']))
 except OSError as e:paths[name]=dict(exists=False,realpath=os.path.realpath(name),error=str(e))
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),boot_id=read(P('/proc/sys/kernel/random/boot_id')),uptime=read(P('/proc/uptime')),paths=paths,mountinfo=read(P('/proc/self/mountinfo')),fstab=read(P('/etc/fstab')),tmpfiles_var_conf=read(P('/usr/lib/tmpfiles.d/var.conf')),tmpfiles_tmp_conf=read(P('/usr/lib/tmpfiles.d/tmp.conf')),CAN_socket_opens=0,CAN_sends=0,remote_file_writes=0),ensure_ascii=False,indent=2))
