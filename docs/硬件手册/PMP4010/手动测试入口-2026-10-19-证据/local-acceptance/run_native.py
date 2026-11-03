from pathlib import Path
import datetime,hashlib,json,subprocess,time
ROOT=Path('/home/zakiu/km3');OUT=ROOT/'docs/硬件手册/PMP4010/手动测试入口-2026-10-19-证据/local-acceptance';rows=[]
def record(name,args):
    t=time.monotonic();p=subprocess.run(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    row={'name':name,'argv':args,'cwd':str(ROOT),'returncode':p.returncode,'duration_seconds':time.monotonic()-t}
    for kind,data in [('stdout',p.stdout),('stderr',p.stderr)]:
        file=name+'.'+kind+'.log';(OUT/file).write_bytes(data);row[kind]=file;row[kind+'_sha256']=hashlib.sha256(data).hexdigest()
    rows.append(row);(OUT/'native-command-index.json').write_text(json.dumps(rows,indent=2)+'\n');print(name+' rc='+str(p.returncode),flush=True)
record('host-build',['cmake','--build','build-host','-j4'])
record('host-ctest',['ctest','--test-dir','build-host','--output-on-failure'])
record('asan-build',['cmake','--build','build-asan','-j4'])
record('asan-ctest',['ctest','--test-dir','build-asan','--output-on-failure'])
record('tsan-build',['cmake','--build','build-tsan','-j4'])
record('tsan-ctest',['setarch','-R','ctest','--test-dir','build-tsan','--output-on-failure'])
