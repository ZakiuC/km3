from pathlib import Path
import subprocess,hashlib,json,datetime
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010/手动故障诊断-2026-10-19-证据';OUT=BASE/'local-acceptance';REPORT=ROOT/'docs/硬件手册/PMP4010/手动故障诊断-2026-10-19.md'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert not (OUT/'final-check-docs.command.json').exists(),'refuse repeat final gate'
def hashes():
 verification=json.loads((BASE/'implementation/verification.json').read_text());assert verification['frozen'] is True
 approved=verification['sha256']
 native=json.loads((ROOT/'docs/硬件手册/PMP4010/正常驱动验收-2026-10-19-证据/native-current/source-after-sha256.json').read_text())['files']
 rows={key:[{'path':p,'expected_sha256':h,'actual_sha256':sha(ROOT/p),'match':sha(ROOT/p)==h} for p,h in files.items()] for key,files in [('final_four',approved),('native_seventy_seven',native)]}
 assert len(approved)==4 and len(native)==77
 return {'comparisons':rows,'all_match':all(row['match'] for group in rows.values() for row in group)}
before=hashes();assert before['all_match']
argv=['bash','tools/check-docs.sh'];started=datetime.datetime.now(datetime.timezone.utc).isoformat();p=subprocess.run(argv,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
row={'argv':argv,'cwd':str(ROOT),'started_utc':started,'returncode':p.returncode}
for kind in ['stdout','stderr']:
 path=OUT/('final-check-docs.'+kind+'.log');path.write_bytes(getattr(p,kind));row[kind]=str(path.relative_to(ROOT));row[kind+'_sha256']=sha(path)
(OUT/'final-check-docs.command.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
after=hashes();assert after['all_match'];assert before==after
summary={'documentation_gate':row,'four_source_files_match':True,'native_77_inputs_match':True,'before_after_match':True,'hardware_actions':False,'other_tests_rerun':False,'requested_role':'light_worker','requested_model':'GPT-6.1 Sol','requested_effort':'medium','runtime_model':'unknown','runtime_effort':'unknown','last_token_usage_input_tokens':'unknown','parent_estimate_only':'below145K parent conservative estimate from bounded packets and evidence; not measured'}
(OUT/'final-source-hash-check.json').write_text(json.dumps({'before':before,'after':after},ensure_ascii=False,indent=2)+'\n')
(OUT/'final-closeout-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
assert p.returncode==0,p.stderr.decode()
print(json.dumps({'rc':p.returncode,'four_match':True,'native_77_match':True,'report':str(REPORT)},ensure_ascii=False))
