from pathlib import Path
import datetime,hashlib,json,subprocess,time,shutil
ROOT=Path('/home/zakiu/km3'); OUT=ROOT/'docs/硬件手册/PMP4010/使能后目标重写-2026-10-19-证据/local-acceptance'; OLD=ROOT/'docs/硬件手册/PMP4010/失能起步修复验收-2026-10-19-证据'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def freeze():
    paths=list((ROOT/'device/drivers/pmp4010/tools').glob('*.py'))+list((ROOT/'tools/hwtui').glob('*.py'))
    paths += [ROOT/'device/drivers/pmp4010/driver/od.json',ROOT/'device/drivers/pmp4010/tests/velocity_vectors.json']
    return {str(p.relative_to(ROOT)):sha(p) for p in sorted(paths)}
APPROVED=ROOT/'docs/硬件手册/PMP4010/使能后目标重写-2026-10-19-证据/implementation/final-source-sha256.json'
NATIVE=ROOT/'docs/硬件手册/PMP4010/正常驱动验收-2026-10-19-证据/native-current/source-after-sha256.json'
assert not (OUT/'command-index.json').exists(), 'refuse overwriting prior acceptance'
approved=json.loads(APPROVED.read_text())
def compare_freezes(stage):
    native=json.loads(NATIVE.read_text())['files']
    comparisons={label:[{'path':path,'expected_sha256':expected,'actual_sha256':sha(ROOT/path),'match':sha(ROOT/path)==expected} for path,expected in files.items()] for label,files in [('approved_six',approved),('native_current_77',native)]}
    report={'comparisons':comparisons,'native_source_files':len(native),'all_match':all(row['match'] for rows in comparisons.values() for row in rows)}
    (OUT/('freeze-'+stage+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    assert report['all_match'], 'frozen source mismatch '+stage
    assert len(native)==77
compare_freezes('before')
rows=[]
def record(name,argv):
    start=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic()
    p=subprocess.run(argv,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    row={'name':name,'argv':argv,'cwd':str(ROOT),'started_utc':start,'duration_seconds':time.monotonic()-t,'returncode':p.returncode}
    for kind,data in [('stdout',p.stdout),('stderr',p.stderr)]:
        namefile=name+'.'+kind+'.log';(OUT/namefile).write_bytes(data);row[kind]=namefile;row[kind+'_sha256']=hashlib.sha256(data).hexdigest()
    (OUT/(name+'.command.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n');rows.append(row)
    (OUT/'command-index.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    print(name+' rc='+str(p.returncode),flush=True)
    if p.returncode: print(p.stderr.decode(errors='replace')[-2000:],flush=True)
    assert p.returncode==0, 'acceptance failed; stop packaging: '+name
    return p
before=freeze();(OUT/'freeze-source-sha256.json').write_text(json.dumps({'files':before},indent=2)+'\n')
record('model',['python3','-I','-B','-m','unittest','discover','-s','device/drivers/pmp4010/tools','-p','_test_*.py'])
record('hwtui',['python3','-I','-B','-m','unittest','discover','-s','tools/hwtui','-p','test_*.py'])
record('tools-tests',['python3','-I','-B','-m','unittest','discover','-s','tools/tests','-p','test_*.py'])
record('drivers',['bash','tools/check-drivers.sh'])
record('docs',['bash','tools/check-docs.sh'])
record('deps',['bash','tools/check-deps.sh'])
record('manual-citations',['bash','tools/check-manual-citations.sh'])
record('gen-od',['python3','-I','-B','tools/hwdriver/gen-od.py','--model','pmp4010','--check'])
record('gen-canopen',['python3','-I','-B','tools/hwdriver/gen-canopen.py','--check'])
record('gen-access',['python3','-I','-B','tools/hwdriver/gen-pmp4010-access.py','--check'])
record('manual-help',['python3','-I','-B','device/drivers/pmp4010/tools/pmp4010.py','--manual','--help'])
record('independent-pty',['python3','-I','-B','device/drivers/pmp4010/tools/_test_simple_pty.py','--run',str(OUT/'independent-pty')])

prior=json.loads((OLD/'stage3-recovered-package/source-manifest.json').read_text());inputs=prior['native_source_and_build_inputs']
comparison=[{'path':x['path'],'previous_sha256':x['sha256'],'current_sha256':sha(ROOT/x['path']),'match':sha(ROOT/x['path'])==x['sha256']} for x in inputs]
for path in ['build-arm64/libpmp4010_velocity.so.1.0.0']:
    prev=next(x['sha256'] for x in prior['package_sources'] if x.get('source')==path);comparison.append({'path':path,'previous_sha256':prev,'current_sha256':sha(ROOT/path),'match':sha(ROOT/path)==prev})
(OUT/'native-arm-hash-reuse.json').write_text(json.dumps({'inputs':comparison,'all_match':all(x['match'] for x in comparison),'scope':'native velocity library/build inputs only; does not establish all C++ test input equality','prior_actual_index':str(OLD/'post-power-local/index-final.json'),'current_cpp_execution':False,'current_arm_build':False,'current_arm_execution':False},indent=2)+'\n')
compare_freezes('after')
after=freeze();assert before==after, 'source changed during acceptance'
result={'records':rows,'all_returncodes_zero':all(x['returncode']==0 for x in rows),'source_before_after_match':before==after,'hardware_acceptance':False,'actual_model':'unknown','actual_effort':'unknown','last_token_usage_input_tokens':'unknown','input_estimate':'initial packet below 8K; not measured; runtime metadata unavailable'}
(OUT/'index-final.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='records'},indent=2))
