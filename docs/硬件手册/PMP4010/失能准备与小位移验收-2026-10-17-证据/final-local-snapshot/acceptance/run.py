from pathlib import Path
import subprocess,json,sys,time
out=Path(__file__).parent
name=sys.argv[1]; cmd=sys.argv[2:]; start=time.time()
(out/(name+'.command.json')).write_text(json.dumps({'argv':cmd,'cwd':'/home/zakiu/km3'},ensure_ascii=False,indent=2)+'\n')
r=subprocess.run(cmd,cwd='/home/zakiu/km3',stdout=subprocess.PIPE,stderr=subprocess.PIPE)
(out/(name+'.stdout.log')).write_bytes(r.stdout);(out/(name+'.stderr.log')).write_bytes(r.stderr)
(out/(name+'.result.json')).write_text(json.dumps({'returncode':r.returncode,'duration_seconds':time.time()-start},indent=2)+'\n')
print(name+' rc='+str(r.returncode));print(r.stdout.decode());print(r.stderr.decode());sys.exit(r.returncode)
