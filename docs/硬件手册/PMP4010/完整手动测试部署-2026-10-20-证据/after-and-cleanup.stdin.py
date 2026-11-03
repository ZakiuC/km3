import pathlib,hashlib,json,os
before={'python': '3.10.12 (main, Mar  3 2026, 11:56:32) [GCC 11.4.0]', 'machine': 'aarch64', 'hostname': 'RK3588-Tronlong', 'model': 'TL3588-EVM', 'configs': {'/etc/km3/device.json': '96a15d8d28f760ab9c5b8f43f6b5ddccc667c77f89413fcd9c3201aa9ddbb205', '/etc/km3/can.env': '9253d719d847eed56ea7ef768985c0215faf0f78dfc98838152c734a0feff407'}, 'current_link': '/opt/km3/hwtools/releases/0.1.5+zh-5e31e29', 'current_resolved': '/opt/km3/hwtools/releases/0.1.5+zh-5e31e29', 'final_exists': False}
stage=pathlib.Path('/opt/km3/hwtools/.manual-test-e137c603-f7ocslad')
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
