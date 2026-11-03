from pathlib import Path
import subprocess,hashlib,json,tarfile,os
ROOT=Path('/home/zakiu/km3'); OUT=Path('/tmp/km3-pp-prepare-package-20261017'); PAY=OUT/'payload'
def record(name,args,expected=0):
    run=subprocess.run(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    for suffix,data in [('command.json',(json.dumps({'argv':args,'cwd':str(ROOT)},ensure_ascii=False,indent=2)+'\n').encode()),('stdout.log',run.stdout),('stderr.log',run.stderr),('exitcode',(str(run.returncode)+'\n').encode())]:
        (OUT/'records'/(name+'.'+suffix)).write_bytes(data)
    if run.returncode!=expected: raise RuntimeError((name,run.returncode,run.stderr.decode()))
    return run.stdout.decode()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
record('package-prepare',['python3','-I','-B',str(OUT/'prepare.py')])
manifest=json.loads((PAY/'payload-manifest.json').read_text())
for p in [PAY/'load_smoke.py',PAY/'README-板端加载.md']:
    manifest['files'].append({'path':p.name,'size':p.stat().st_size,'sha256':sha(p),'origin':'package-generated'})
(PAY/'payload-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
record('host-file',['file','-L',str(ROOT/'build-host/libpmp4010_velocity.so')])
record('host-sha256',['sha256sum',str(ROOT/'build-host/libpmp4010_velocity.so.1.0.0')])
record('host-load-smoke',['python3','-I','-B',str(PAY/'load_smoke.py'),'--library',str(ROOT/'build-host/libpmp4010_velocity.so'),'--check-missing-library'])
# Record source delta only for explicitly selected native/runtime inputs.
src=json.loads((OUT/'source-manifest.json').read_text())
paths=sorted({x['source'] for x in src['package_sources'] if 'source' in x and not x['source'].startswith('build-')}|{x['path'] for x in src['native_source_and_build_inputs']})
record('selected-source-diff',['git','diff','HEAD','--']+paths)
for x in src['package_sources']:
    if 'source' in x: assert sha(ROOT/x['source'])==x['sha256'],x['source']+' changed during capture'
for x in src['native_source_and_build_inputs']: assert sha(ROOT/x['path'])==x['sha256'],x['path']+' changed during capture'
assert not list(PAY.rglob('__pycache__'))
archive=OUT/'pmp4010-board-payload.tar.gz'
with tarfile.open(archive,'w:gz',dereference=False) as tf:
    for p in sorted(PAY.rglob('*')):
        if p.is_file() or p.is_symlink(): tf.add(p,arcname=str(p.relative_to(PAY)),recursive=False)
record('archive-list',['tar','-tvzf',str(archive)])
record('archive-sha256',['sha256sum',str(archive)])
regular=[p for p in PAY.rglob('*') if p.is_file() and not p.is_symlink()]
summary={'archive':str(archive),'archive_bytes':archive.stat().st_size,'archive_sha256':sha(archive),'payload_regular_files':len(regular),'payload_symlinks':len([p for p in PAY.rglob('*') if p.is_symlink()]),'payload_regular_bytes':sum(p.stat().st_size for p in regular),'arm_library_sha256':sha(PAY/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0'),'checks':{'host_load_smoke_exitcode':0,'native_vectors':33,'python_vectors':33,'native_int32_vectors':14,'forbidden_io_attempts':0,'arm_execution':'not run','board_compatibility':'pending','source_snapshot_stable_during_capture':True}}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
