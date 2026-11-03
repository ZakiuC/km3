from pathlib import Path
import datetime,hashlib,json,subprocess,time
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010/报警内容显示-2026-10-19-证据';OUT=BASE/'local-acceptance'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
verification=json.loads((BASE/'implementation/verification.json').read_text());assert verification['frozen'] is True
approved=verification['source_sha256'];assert len(approved)==9
native=json.loads((ROOT/'docs/硬件手册/PMP4010/正常驱动验收-2026-10-19-证据/native-current/source-after-sha256.json').read_text())['files'];assert len(native)==77
prior=json.loads((OUT/'command-index.json').read_text());assert prior[-1]['name']=='manual-citations' and prior[-1]['returncode']==1
rows=[r for r in prior if r['returncode']==0];assert len(rows)==6
assert not (OUT/'resume-command-index.json').exists()
def hashes():
 groups={key:[dict(path=p,expected_sha256=h,actual_sha256=sha(ROOT/p),match=sha(ROOT/p)==h) for p,h in files.items()] for key,files in [('final_nine',approved),('native_77',native)]}
 return dict(comparisons=groups,all_match=all(r['match'] for rs in groups.values() for r in rs))
before=hashes();assert before['all_match'];(OUT/'freeze-before-final-citation.json').write_text(json.dumps(before,ensure_ascii=False,indent=2)+'\n')
newrows=[]
def record(name,argv):
 start=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic();p=subprocess.run(argv,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 row=dict(name=name,argv=argv,cwd=str(ROOT),started_utc=start,duration_seconds=time.monotonic()-t,returncode=p.returncode)
 for kind in ['stdout','stderr']:
  data=getattr(p,kind);namefile=name+'.'+kind+'.log';(OUT/namefile).write_bytes(data);row[kind]=namefile;row[kind+'_sha256']=hashlib.sha256(data).hexdigest()
 (OUT/(name+'.command.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n');newrows.append(row);(OUT/'resume-command-index.json').write_text(json.dumps(newrows,ensure_ascii=False,indent=2)+'\n')
 print(name+' rc='+str(p.returncode),flush=True);assert p.returncode==0,name
record('manual-citations-final',['bash','tools/check-manual-citations.sh'])
record('gen-od',['python3','-I','-B','tools/hwdriver/gen-od.py','--model','pmp4010','--check'])
record('gen-canopen',['python3','-I','-B','tools/hwdriver/gen-canopen.py','--check'])
record('gen-access',['python3','-I','-B','tools/hwdriver/gen-pmp4010-access.py','--check'])
record('manual-help',['python3','-I','-B','device/drivers/pmp4010/tools/pmp4010.py','--manual','--manual-linear','--help'])
record('independent-pty',['python3','-I','-B','device/drivers/pmp4010/tools/_test_simple_pty.py','--run-linear',str(OUT/'independent-pty')])
after=hashes();assert before==after and after['all_match'];(OUT/'freeze-after-final-citation.json').write_text(json.dumps(after,ensure_ascii=False,indent=2)+'\n')
result=dict(records=rows+newrows,all_returncodes_zero=True,source_before_after_match=True,source_before_after_scope='final comment-fix stage only; prior whole-suite freeze differs in helper comments',whole_suite_final_source_identical=False,whole_suite_preceded_comment_fix=True,ast_equivalence_evidence='../implementation/citation-ast-equivalence.json',preserved_prior_failure=prior[-1],prior_index='command-index.json',resume_index='resume-command-index.json',hardware_acceptance=False,actual_model='unknown',actual_effort='unknown',last_token_usage_input_tokens='unknown',input_estimate='below175K parent conservative estimate, not measured')
(OUT/'index-final.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print('Final citation stage stable; previous suite and failure retained separately.')
