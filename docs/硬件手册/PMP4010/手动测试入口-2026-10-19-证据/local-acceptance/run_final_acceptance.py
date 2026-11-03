from pathlib import Path
import datetime,hashlib,json,subprocess,time,shutil
ROOT=Path('/home/zakiu/km3'); OUT=ROOT/'docs/硬件手册/PMP4010/手动测试入口-2026-10-19-证据/local-acceptance'; OLD=ROOT/'docs/硬件手册/PMP4010/失能起步修复验收-2026-10-19-证据'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def freeze():
    paths=list((ROOT/'device/drivers/pmp4010/tools').glob('*.py'))+list((ROOT/'tools/hwtui').glob('*.py'))
    paths += [ROOT/'device/drivers/pmp4010/driver/od.json',ROOT/'device/drivers/pmp4010/tests/velocity_vectors.json']
    return {str(p.relative_to(ROOT)):sha(p) for p in sorted(paths)}
rows=[]
def record(name,argv):
    start=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic()
    p=subprocess.run(argv,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    row={'name':name,'argv':argv,'cwd':str(ROOT),'started_utc':start,'duration_seconds':time.monotonic()-t,'returncode':p.returncode}
    for kind,data in [('stdout',p.stdout),('stderr',p.stderr)]:
        namefile=name+'.'+kind+'.log';(OUT/namefile).write_bytes(data);row[kind]=namefile;row[kind+'_sha256']=hashlib.sha256(data).hexdigest()
    (OUT/(name+'.command.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n');rows.append(row)
    (OUT/'final-command-index.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    print(name+' rc='+str(p.returncode),flush=True)
    if p.returncode: print(p.stderr.decode(errors='replace')[-2000:],flush=True)
    return p
before=freeze();(OUT/'final-freeze-source-sha256.json').write_text(json.dumps({'files':before},indent=2)+'\n')
record('final-model',['python3','-I','-B','-m','unittest','discover','-s','device/drivers/pmp4010/tools','-p','_test_*.py'])
record('final-drivers',['bash','tools/check-drivers.sh'])
record('final-docs',['bash','tools/check-docs.sh'])
record('final-deps',['bash','tools/check-deps.sh'])
record('final-manual-citations',['bash','tools/check-manual-citations.sh'])
record('final-gen-od',['python3','-I','-B','tools/hwdriver/gen-od.py','--model','pmp4010','--check'])
record('final-gen-canopen',['python3','-I','-B','tools/hwdriver/gen-canopen.py','--check'])
record('final-gen-access',['python3','-I','-B','tools/hwdriver/gen-pmp4010-access.py','--check'])
record('final-independent-pty',['python3','-I','-B','device/drivers/pmp4010/tools/_test_manual_pty.py','--run',str(OUT/'final-independent-pty')])
if Path('/tmp/pmp4010-manual-pty-stopped-final').is_dir():shutil.copytree('/tmp/pmp4010-manual-pty-stopped-final',OUT/'worker-stopped-pty',dirs_exist_ok=True)
after=freeze();old=json.loads((OUT/'freeze-source-sha256.json').read_text())['files'];changed=[p for p in before if before[p]!=old.get(p)]
result={'records':rows,'all_returncodes_zero':all(x['returncode']==0 for x in rows),'source_before_after_match':before==after,'changed_since_initial':changed,'hwtui_all_python_sources_unchanged':all(before[p]==old.get(p) for p in before if p.startswith('tools/hwtui/')),'hardware_acceptance':False}
(OUT/'final-index.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='records'},indent=2))
