from pathlib import Path
import subprocess,json,hashlib,datetime,time,shlex,sys
ROOT=Path('/home/zakiu/km3')
OUT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def record(name,args):
    assert not (OUT/(name+'.command.json')).exists(), name
    start=datetime.datetime.now(datetime.timezone.utc).isoformat(); t=time.monotonic()
    (OUT/(name+'.command.json')).write_text(json.dumps({'argv':args,'shell_command':shlex.join(args),'cwd':str(ROOT),'started_utc':start},ensure_ascii=False,indent=2)+'\n')
    with (OUT/(name+'.stdout.log')).open('wb') as out,(OUT/(name+'.stderr.log')).open('wb') as err:
        p=subprocess.run(args,cwd=ROOT,stdout=out,stderr=err)
    row={'name':name,'argv':args,'shell_command':shlex.join(args),'cwd':str(ROOT),'started_utc':start,'duration_seconds':time.monotonic()-t,'returncode':p.returncode,'stdout':name+'.stdout.log','stderr':name+'.stderr.log','stdout_sha256':sha(OUT/(name+'.stdout.log')),'stderr_sha256':sha(OUT/(name+'.stderr.log'))}
    (OUT/(name+'.exitcode')).write_text(str(p.returncode)+'\n')
    (OUT/(name+'.result.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
    print(name+' rc='+str(p.returncode),flush=True)
    return row
def main():
    frozen=json.loads((OUT/'freeze-source-sha256.json').read_text())['files']
    assert all(sha(ROOT/x)==v for x,v in frozen.items())
    rows=[]
    def run(name,args):rows.append(record(name,args))
    run('model',['python3','-I','-m','unittest','discover','-s','device/drivers/pmp4010/tools','-p','_test_*.py'])
    run('hwtui',['python3','-I','-m','unittest','discover','-s','tools/hwtui','-p','test_*.py'])
    run('tools-tests',['python3','-I','-m','unittest','discover','-s','tools/tests','-p','test_*.py'])
    run('host-build',['cmake','--build','build-host','-j4'])
    run('host-ctest',['ctest','--test-dir','build-host','--output-on-failure'])
    run('asan-build',['cmake','--build','build-asan','-j4'])
    run('asan-ctest',['ctest','--test-dir','build-asan','--output-on-failure'])
    run('tsan-build',['cmake','--build','build-tsan','-j4'])
    run('tsan-ctest',['setarch','-R','ctest','--test-dir','build-tsan','--output-on-failure'])
    run('arm-build',['cmake','--build','build-arm64','-j4'])
    run('drivers',['bash','tools/check-drivers.sh'])
    run('docs',['bash','tools/check-docs.sh'])
    run('deps',['bash','tools/check-deps.sh'])
    run('manual-citations',['bash','tools/check-manual-citations.sh'])
    run('gen-od',['python3','-I','tools/hwdriver/gen-od.py','--model','pmp4010','--check'])
    run('gen-canopen',['python3','-I','tools/hwdriver/gen-canopen.py','--check'])
    run('gen-access',['python3','-I','tools/hwdriver/gen-pmp4010-access.py','--check'])
    after={x:sha(ROOT/x) for x in frozen}
    (OUT/'source-after.json').write_text(json.dumps(after,indent=2)+'\n')
    result={'stage':'stage3_post_power_recovered','freeze_manifest_sha256':sha(OUT/'freeze-source-sha256.json'),'freeze_files':len(frozen),'source_before_after_match':frozen==after,'records':rows,'all_returncodes_zero':all(x['returncode']==0 for x in rows),'hardware_acceptance':False,'prior_temporary_stage3_evidence':'lost; these commands are fresh executions'}
    (OUT/'index-final.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    assert result['source_before_after_match'] and result['all_returncodes_zero']
if __name__=='__main__':main()
