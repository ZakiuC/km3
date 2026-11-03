from pathlib import Path
import hashlib,json,subprocess,shutil,os,datetime,re
ROOT=Path('/home/zakiu/km3')
OUT=Path('/tmp/km3-pp-disabled-start-package-20261019')
PAY=OUT/'payload'
def record(name,args,required=True):
    run=subprocess.run(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    stem=OUT/'records'/name
    attempt=1
    while stem.with_suffix('.command.json').exists():
        attempt+=1; stem=OUT/'records'/(name+'-attempt'+str(attempt))
    stem.with_suffix('.command.json').write_text(json.dumps({'argv':args,'cwd':str(ROOT)},ensure_ascii=False,indent=2)+'\n')
    stem.with_suffix('.stdout.log').write_bytes(run.stdout)
    stem.with_suffix('.stderr.log').write_bytes(run.stderr)
    stem.with_suffix('.exitcode').write_text(str(run.returncode)+'\n')
    if required and run.returncode: raise RuntimeError((name,run.returncode))
    return run.stdout.decode()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
head=record('source-head',['git','rev-parse','HEAD']).strip()
# Explicit pathspec exclusion: do not expose or inspect excluded directory content.
dirty=record('source-dirty',['git','status','--porcelain=v1','--untracked-files=all','--','device/drivers/pmp4010','tools/hwtui','tools/tests'])
files={}
for name in ['canopen','canopen_consts','canopen_wire','canbus','discover','mockbus','tui']:
    files['hwtui/'+name+'.py']='tools/hwtui/'+name+'.py'
for name in ['pmp4010','pmp4010_bench','pmp4010_params','pmp4010_position','pmp4010_tui','pmp4010_velocity']:
    files['drivers/pmp4010/'+name+'.py']='device/drivers/pmp4010/tools/'+name+'.py'
files.update({'drivers/pmp4010/od.json':'device/drivers/pmp4010/driver/od.json',
              'drivers/pmp4010/velocity_vectors.json':'device/drivers/pmp4010/tests/velocity_vectors.json',
              'drivers/pmp4010/libpmp4010_velocity.so.1.0.0':'build-arm64/libpmp4010_velocity.so.1.0.0'})
manifest=[]
for dest,source in files.items():
    dst=PAY/dest;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/source,dst)
    manifest.append({'path':dest,'source':source,'size':dst.stat().st_size,'sha256':sha(dst)})
for name,target in [('libpmp4010_velocity.so.1','libpmp4010_velocity.so.1.0.0'),('libpmp4010_velocity.so','libpmp4010_velocity.so.1')]:
    dst=PAY/'drivers/pmp4010'/name
    if not dst.is_symlink(): dst.symlink_to(target)
    manifest.append({'path':str(dst.relative_to(PAY)),'symlink':target})
source_paths=['device/drivers/pmp4010/driver/pmp4010_velocity.cpp','device/drivers/pmp4010/driver/pmp4010_velocity.hpp','device/drivers/pmp4010/driver/pmp4010_velocity_c.cpp','device/drivers/pmp4010/driver/pmp4010_velocity_c.h','device/drivers/pmp4010/native/CMakeLists.txt','device/cmake/aarch64-linux-gnu.cmake','tools/deploy_hwtools.sh']
source_manifest=[{'path':s,'sha256':sha(ROOT/s),'size':(ROOT/s).stat().st_size} for s in source_paths]
(OUT/'source-manifest.json').write_text(json.dumps({'head':head,'dirty_status_scope':'explicit device/drivers/pmp4010, tools/hwtui, tools/tests paths only','dirty_status':dirty,'package_sources':manifest,'native_source_and_build_inputs':source_manifest,'snapshot_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'routing':{'requested_role':'light_worker','requested_model':'gpt-6.1-sol','requested_effort':'medium','actual_model':'unknown','actual_effort':'unknown','last_token_usage_input_tokens':'unknown','input_estimate':'unknown; fresh concise task packet supplied, runtime usage unavailable'}},ensure_ascii=False,indent=2)+'\n')
lib=str(PAY/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0')
record('arm-file',['file','-L',lib])
record('arm-elf-header',['readelf','-h',lib])
dynamic=record('arm-elf-dynamic',['readelf','-d',lib])
versions=record('arm-elf-versions',['readelf','--version-info',lib])
symbols=record('arm-elf-symbols',['readelf','-Ws',lib])
record('arm-sha256',['sha256sum',lib])
assert 'AArch64' in (OUT/'records/arm-elf-header.stdout.log').read_text()
assert not re.search(r'asan|tsan|ubsan|lsan|msan|sanitizer',dynamic+symbols,re.I)
needed=re.findall(r'\(NEEDED\).*?\[(.*?)\]',dynamic)
assert set(needed)=={'libstdc++.so.6','libgcc_s.so.1','libc.so.6','ld-linux-aarch64.so.1'},needed
for sym in ['pmp4010_velocity_abi_version','pmp4010_velocity_convert','pmp4010_velocity_require_int32']:
    assert re.search(r'GLOBAL\s+DEFAULT\s+(?!UND)\S+\s+'+sym+r'\b',symbols),sym
for ver in ['GLIBC_2.17','GLIBCXX_3.4.21','CXXABI_1.3.9','GCC_3.0']: assert ver in versions
(PAY/'payload-manifest.json').write_text(json.dumps({'stage':'probe_no_travel','freeze_manifest_sha256':'a9750a6952ee851f502bd0bd59b7eedd2542d35c1f423503f1a75acc17c6e5ff','source_head':head,'files':manifest},indent=2)+'\n')
(OUT/'elf-assessment.json').write_text(json.dumps({'arm_library_sha256':sha(Path(lib)),'architecture':'AArch64','needed':needed,'version_needs':sorted(set(re.findall(r'Name: (\S+)',versions))),'sanitizer_runtime_reference_count':0,'exports_checked':3,'runtime_resolution_on_board':'pending; static ELF inspection cannot establish board library availability'},indent=2)+'\n')
print(json.dumps({'head':head,'payload_regular_files':sum('sha256' in x for x in manifest),'symlinks':2,'elf_checks':'passed','arm_sha256':sha(Path(lib))},indent=2))
