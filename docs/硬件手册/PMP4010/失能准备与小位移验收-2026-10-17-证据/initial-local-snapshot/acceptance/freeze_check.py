from pathlib import Path
import hashlib,json,shutil
R=Path('/home/zakiu/km3');A=Path('/tmp/km3-pp-prepare-acceptance-20261017');P=Path('/tmp/km3-pp-prepare-package-20261017');I=Path('/tmp/km3-pp-prepare-impl-20261017');O=Path('/tmp/km3-pmp4010-board-20261017-csvguard-final-package')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
expected=json.loads((I/'final-sourcehash.json').read_text())
for s,h in expected.items():assert sha(R/s)==h,s
assert expected['device/drivers/pmp4010/tools/pmp4010_tui.py']=='6ef77e2edbbfea5f579fa5db0996472a9932c9a1ddc3bd0118abd1a5ab7654b8'
manifest=json.loads((P/'source-manifest.json').read_text())
for item in manifest['package_sources']:
 if 'source' in item:assert sha(R/item['source'])==item['sha256'],item['source']
paths=set(json.loads(Path('/tmp/km3-csv-guard-final-20261017/source-sha256-final.json').read_text()))|set(expected)|{x['path'] for x in manifest['native_source_and_build_inputs']}|{x['source'] for x in manifest['package_sources'] if 'source' in x}
full={s:sha(R/s) for s in sorted(paths)}
for target in [P/'full-source-sha256.json',A/'source-sha256-final.json']:target.write_text(json.dumps(full,indent=2)+'\n')
shutil.copytree(I,A/'imported-implementation-evidence')
assert int((I/'model-delivery-retry.rc').read_text())==0
assert 'Ran 241 tests' in (I/'model-delivery-retry.stderr').read_text()
assert int((I/'bench-pty-delivery.rc').read_text())==0
assert sha(O/'pmp4010-board-payload.tar.gz')=='ab853ce5565e55cc76c9782617f826a60c547d05339dc5c252f16e85eb1c3c39'
assert sha(P/'pmp4010-board-payload.tar.gz')=='e5e6be59dbed7611f3b4e5f6d5c53e38e2f7e1527afa1831e7b511e4a0fcacb6'
for stem in ['host-load-smoke','archive-host-smoke']:
 s=(P/('records/'+stem+'.stdout.log')).read_text();assert sum(x.startswith('import=') for x in s.splitlines())==16
 result=json.loads(s.splitlines()[-1]);assert result=={'result':'PASS','native_vectors':33,'python_vectors':33,'native_int32_vectors':14,'forbidden_io_attempts':0,'hardware_acceptance':False}
print(json.dumps({'source_hashes_verified':len(full),'implementation_final_hashes_verified':len(expected),'payload_runtime_hashes_stable':True,'old_archive_untouched':True,'new_archive_sha256':sha(P/'pmp4010-board-payload.tar.gz'),'model_tests':241,'pty_rc':0,'host_and_extract_smoke':'16 imports, native33/python33/Int3214, forbidden IO=0 each'},indent=2))
