import capture,json
before=json.loads(next(line.split('=',1)[1] for line in (capture.ROOT/'preflight-corrected.stdout').read_text().splitlines() if line.startswith('SNAPSHOT_JSON=')))
stage=json.loads((capture.ROOT/'remote-stage.json').read_text())['stage']
script='''import pathlib,hashlib,json,os
before=BEFORE
stage=pathlib.Path(STAGE)
final=pathlib.Path('/opt/km3/hwtools/manual-test-20261020-e137c603')
after={'configs':{x:hashlib.sha256(pathlib.Path(x).read_bytes()).hexdigest() for x in before['configs']},'current_link':os.readlink('/opt/km3/hwtools/current'),'current_resolved':str(pathlib.Path('/opt/km3/hwtools/current').resolve())}
for k,v in after.items():assert v==before[k], 'changed '+k
manifest=final/'payload-manifest.json'
assert hashlib.sha256(manifest.read_bytes()).hexdigest()=='8cf6fa0d6efdfb3831ff7e5397fd67314aa3ea433969e07d51478296634caa51'
items=json.loads(manifest.read_text())['files']
for item in items:
 p=final/item['path']
 if 'symlink' in item:assert p.is_symlink() and os.readlink(p)==item['symlink']
 else:assert not p.is_symlink() and p.stat().st_size==item['size'] and hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
actual={str(p.relative_to(final)) for p in final.rglob('*') if not p.is_dir()}
expected={i['path'] for i in items}|{'payload-manifest.json'}
assert actual==expected, 'unexpected payload files '+str(actual^expected)
assert stage.parent==final.parent and stage.name.startswith('.manual-test-e137c603-')
assert {p.name for p in stage.iterdir()}=={'payload.tar.gz'}
assert hashlib.sha256((stage/'payload.tar.gz').read_bytes()).hexdigest()=='e137c603d57c584134f14f006bcae482d3b999ce1d2dda8dd25b19005a80b237'
(stage/'payload.tar.gz').unlink();stage.rmdir()
print(json.dumps({'result':'PASS','before_after_identical':after,'payload_entries':len(actual),'file_sha_and_links':'PASS','launch':str(final/'run-manual.sh'),'launcher_sha256':hashlib.sha256((final/'run-manual.sh').read_bytes()).hexdigest(),'temporary_stage_removed':str(stage),'no_CAN_operations':True,'physical_acceptance':False},ensure_ascii=False))
'''.replace('BEFORE',repr(before)).replace('STAGE',repr(stage))
capture.run('after-and-cleanup',capture.SSH+['python3 -I -B -'],script.encode())
