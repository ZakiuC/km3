from pathlib import Path
import datetime,hashlib,json,re,subprocess,time
ROOT=Path('/home/zakiu/km3');OUT=ROOT/'docs/硬件手册/PMP4010/正常驱动验收-2026-10-19-证据/native-current'
assert not (OUT/'command-index.json').exists(),'refuse overwriting evidence'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
roots=['device/src','device/include','device/tests','device/drivers/pmp4010/driver','device/drivers/pmp4010/tests','device/drivers/pmp4010/native','common','device/proto','device/cmake']
listing=subprocess.run(['rg','--files',*roots],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
paths=set(listing.stdout.decode().splitlines())|{'device/CMakeLists.txt','device/drivers/pmp4010/CMakeLists.txt'}
paths={p for p in paths if (ROOT/p).is_file()}
def freeze():return {p:sha(ROOT/p) for p in sorted(paths)}
before=freeze();(OUT/'source-before-sha256.json').write_text(json.dumps({'scope':'all files in explicitly enumerated current C++ source/header/test/common/proto/cmake roots; no harness or architecture traversal','roots':roots,'files':before},ensure_ascii=False,indent=2)+'\n')
pyfreeze=ROOT/'docs/硬件手册/PMP4010/状态样本修复-2026-10-19-证据/local-acceptance/freeze-source-sha256.json';expected=json.loads(pyfreeze.read_text())['files']
pyproof=[{'path':p,'expected_sha256':h,'current_sha256':sha(ROOT/p),'match':sha(ROOT/p)==h} for p,h in expected.items()]
(OUT/'existing-python-freeze-check.json').write_text(json.dumps({'reference':str(pyfreeze),'all_match':all(x['match'] for x in pyproof),'scope':'hash equality only; does not claim rerunning375/217/8 or seven gates','files':pyproof},ensure_ascii=False,indent=2)+'\n')
rows=[]
def record(name,argv):
 start=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic();p=subprocess.run(argv,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 row={'name':name,'argv':argv,'cwd':str(ROOT),'started_utc':start,'duration_seconds':time.monotonic()-t,'returncode':p.returncode}
 for kind,data in [('stdout',p.stdout),('stderr',p.stderr)]:
  namefile=name+'.'+kind+'.log';(OUT/namefile).write_bytes(data);row[kind]=namefile;row[kind+'_sha256']=hashlib.sha256(data).hexdigest()
 rows.append(row);(OUT/(name+'.command.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n');(OUT/'command-index.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
 print(name+' rc='+str(p.returncode),flush=True)
 return p
jobs=[('host-build',['cmake','--build','build-host','--clean-first','-j4']),('host-ctest',['ctest','--test-dir','build-host','--output-on-failure']),('asan-build',['cmake','--build','build-asan','--clean-first','-j4']),('asan-ctest',['ctest','--test-dir','build-asan','--output-on-failure']),('tsan-build',['cmake','--build','build-tsan','--clean-first','-j4']),('tsan-ctest',['setarch','-R','ctest','--test-dir','build-tsan','--output-on-failure'])]
failed=None
for name,argv in jobs:
 p=record(name,argv)
 if p.returncode:
  failed=name;break
after=freeze();(OUT/'source-after-sha256.json').write_text(json.dumps({'files':after},ensure_ascii=False,indent=2)+'\n')
ctest={}
for row in rows:
 if row['name'].endswith('-ctest'):
  txt=(OUT/row['stdout']).read_text();match=re.search(r'(\d+)% tests passed, (\d+) tests failed out of (\d+)',txt);ctest[row['name']]={'summary':match.group(0) if match else None,'returncode':row['returncode']}
configs={}
for directory in ['build-host','build-asan','build-tsan']:
 cache=ROOT/directory/'CMakeCache.txt';fields={}
 for line in cache.read_text().splitlines():
  if line.startswith(('CMAKE_HOME_DIRECTORY:','CMAKE_BUILD_TYPE:','KM3_SANITIZER:','KM3_BUILD_TESTS:','KM3_ENABLE_PROTO:')):fields[line.split('=',1)[0]]=line.split('=',1)[1]
 configs[directory]={'cache_sha256':sha(cache),'fields':fields}
result={'all_requested_commands_completed':len(rows)==len(jobs),'all_returncodes_zero':all(r['returncode']==0 for r in rows),'failed_command':failed,'source_before_after_match':before==after,'source_files':len(before),'ctest':ctest,'configs':configs,'python_current_freeze_all_match':all(x['match'] for x in pyproof),'python_or_gates_rerun':False,'hardware_or_SSH_or_CAN':False,'runtime_model':'unknown','runtime_effort':'unknown','last_token_usage_input_tokens':'unknown','conservative_input_estimate':'below200K; parent estimate180K plus bounded recent tool output; not measured'}
(OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
raise SystemExit(1 if failed else 0)
