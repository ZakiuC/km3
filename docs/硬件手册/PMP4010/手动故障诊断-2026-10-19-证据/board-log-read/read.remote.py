"""Read existing files and process argv only; no package imports or device IO."""
import datetime,hashlib,json,os,pathlib,re,stat,sys
P=pathlib.Path
base=P('/var/tmp/km3-pmp4010-linear-20261019-c91126d7f56a/payload/logs')
assert base.is_dir() and not base.is_symlink()
def metadata(p):
 s=p.lstat()
 return dict(path=str(p),size=s.st_size,mtime_ns=s.st_mtime_ns,mode=oct(s.st_mode),symlink=p.is_symlink())
entries=sorted(base.iterdir(),key=lambda p:p.lstat().st_mtime_ns,reverse=True)
processes=[]
for d in P('/proc').iterdir():
 if not d.name.isdigit() or int(d.name)==os.getpid():continue
 try:argv=(d/'cmdline').read_bytes().split(b'\0');argv=[a.decode(errors='replace') for a in argv if a]
 except OSError:continue
 if any('pmp4010.py' in a or 'run-manual.sh' in a for a in argv):processes.append(dict(pid=int(d.name),argv=argv))
print(json.dumps(dict(type='snapshot',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),base=str(base),entries=[metadata(p) for p in entries[:20]],matching_processes=processes,remote_writes=0,CAN_opens=0,CAN_sends=0),ensure_ascii=False))
runs=[p for p in entries if p.is_dir() and not p.is_symlink()]
assert runs,'no existing run directory'
run=runs[0]
files=sorted(run.iterdir(),key=lambda p:p.name)
print(json.dumps(dict(type='latest_run',path=str(run),files=[metadata(p) for p in files]),ensure_ascii=False))
pattern=re.compile(r'failure|manual_result|603[fF]|error_code_object|cleanup|stop_confirm|actual_position|target_position|statusword|controlword|6041|6040|velocity|rpm|sdo.*recv',re.I)
remaining=500000
for p in files[:20]:
 if p.is_symlink() or not stat.S_ISREG(p.lstat().st_mode):continue
 digest=hashlib.sha256();chunks=[]
 with p.open('rb') as f:
  while True:
   chunk=f.read(65536)
   if not chunk:break
   digest.update(chunk);chunks.append(chunk)
 data=b''.join(chunks)
 if b'\x00' in data[:4096]:
  print(json.dumps(dict(type='file',**metadata(p),sha256=digest.hexdigest(),text_read=False),ensure_ascii=False));continue
 lines=data.decode(errors='replace').splitlines()
 matched=[i for i,line in enumerate(lines) if pattern.search(line)]
 indices=set(range(max(0,len(lines)-100),len(lines)))
 for i in matched[-120:]:indices.update(range(max(0,i-1),min(len(lines),i+2)))
 excerpt=[]
 for i in sorted(indices):
  text=lines[i];size=len(text.encode())
  if size>remaining:break
  excerpt.append(dict(line=i+1,text=text));remaining-=size
 print(json.dumps(dict(type='file',**metadata(p),sha256=digest.hexdigest(),total_lines=len(lines),matched_lines=len(matched),excerpt=excerpt,excerpt_truncated=len(excerpt)<len(lines),raw_603f_lines=[dict(line=i+1,text=line) for i,line in enumerate(lines) if re.search(r'603[fF]|error_code_object',line)][-50:]),ensure_ascii=False))
 if remaining<=0:break
