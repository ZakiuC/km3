from pathlib import Path
import hashlib,json,ast,tokenize,io
ROOT=Path('/home/zakiu/km3')
OLD=Path('/tmp/km3-pmp4010-board-20261017-csvguard-package')
NEW=Path('/tmp/km3-pmp4010-board-20261017-csvguard-final-package')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
a=json.loads((OLD/'source-manifest.json').read_text());b=json.loads((NEW/'source-manifest.json').read_text())
old_inputs={x['path']:x for x in a['native_source_and_build_inputs']}
checks=[]
for x in b['native_source_and_build_inputs']:
    if 'pmp4010_velocity' in x['path'] or '/native/' in x['path'] or '/cmake/' in x['path']:
        current=sha(ROOT/x['path'])
        assert current==x['sha256']==old_inputs[x['path']]['sha256'],x['path']
        checks.append({'source':x['path'],'sha256':current,'unchanged_from_previous':True})
old_entries={x['path']:x for x in a['package_sources'] if 'sha256' in x}
changed=[]
for x in b['package_sources']:
    if 'source' not in x:continue
    assert sha(ROOT/x['source'])==x['sha256']==sha(NEW/'payload'/x['path']),x['source']
    if x['sha256']!=old_entries[x['path']]['sha256']:
        changed.append({'path':x['path'],'previous_sha256':old_entries[x['path']]['sha256'],'sha256':x['sha256']})
lib=ROOT/'build-arm64/libpmp4010_velocity.so.1.0.0'
hashes=[sha(lib),sha(OLD/'payload/drivers/pmp4010/libpmp4010_velocity.so.1.0.0'),sha(NEW/'payload/drivers/pmp4010/libpmp4010_velocity.so.1.0.0'),sha(lib)]
assert len(set(hashes))==1,hashes
od=json.loads((NEW/'payload/drivers/pmp4010/od.json').read_text())
entry=next(x for x in od['entries'] if x['key']=='velocity_offset')
assert entry['status']=='verified' and int(entry['index'],0)==0x60b1,entry
assert '读取 CSV 速度偏置 60B1h' in (NEW/'payload/drivers/pmp4010/pmp4010.py').read_text()
old_py=(OLD/'payload/drivers/pmp4010/pmp4010.py').read_bytes()
new_py=(NEW/'payload/drivers/pmp4010/pmp4010.py').read_bytes()
assert sha(NEW/'payload/drivers/pmp4010/pmp4010.py')=='2fca0b103544c29f78a79b9e349ec841db0e39abf8f39d9d910e57e47b1e0a1a'
assert ast.dump(ast.parse(old_py),include_attributes=False)==ast.dump(ast.parse(new_py),include_attributes=False)
def tokens(data):return [(t.type,t.string) for t in tokenize.tokenize(io.BytesIO(data).readline) if t.type not in (tokenize.COMMENT,tokenize.NL,tokenize.ENCODING)]
assert tokens(old_py)==tokens(new_py)
assert [x['path'] for x in changed]==['drivers/pmp4010/pmp4010.py'],changed
print(json.dumps({'comment_only_AST_equal':True,'comment_only_noncomment_tokens_equal':True,'result':'PASS','math_and_build_sources':checks,'changed_runtime_files':changed,'arm_hash_before_old_new_after':hashes,'velocity_offset':{'key':entry['key'],'index':entry['index'],'status':entry['status']},'current_source_package_hashes_match':True,'new_arm_execution':'not run'},ensure_ascii=False,indent=2))
