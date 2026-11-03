#!/usr/bin/env python3
"""Package frozen presentation changes atop the verified, deployed Python payload."""
import hashlib, importlib.util, json, os, pathlib, shutil, sys, tarfile
ROOT=pathlib.Path('/home/zakiu/km3'); P=pathlib.Path(__file__).resolve().parent; E=P.parent
BASE=ROOT/'docs/硬件手册/PMP4010/手动速度mm每秒-2026-10-20-证据/package'
ARCHIVE_SHA='76f8db436ae8bc7201d2a015d896f3d864f40ebfd0fabea638c77dcced140bd8'
MANIFEST_SHA='2903b456b07412c3cc4a69a2f018f786fa0bbebfc931862dfc407571ea4526b6'
ARM_SHA='59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
LAUNCHER_SHA='56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1'
ALLOWED={'device/drivers/pmp4010/tools/pmp4010_simple.py':'drivers/pmp4010/pmp4010_simple.py',
'device/drivers/pmp4010/tools/pmp4010_simple_view.py':'drivers/pmp4010/pmp4010_simple_view.py',
'tools/hwtui/tui.py':'hwtui/tui.py'}
spec=importlib.util.spec_from_file_location('record',E/'acceptance/record.py'); r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);r.OUT=P

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def inventory(base,entries):
    assert {str(p.relative_to(base)) for p in base.rglob('*') if p.is_file() or p.is_symlink()}=={x['path'] for x in entries}|{'payload-manifest.json'}
    for x in entries:
        rel=pathlib.PurePosixPath(x['path']);assert not rel.is_absolute() and '..' not in rel.parts
        p=base/x['path']
        if 'symlink' in x:assert p.is_symlink() and os.readlink(p)==x['symlink']
        else:assert not p.is_symlink() and p.stat().st_size==x['size'] and sha(p)==x['sha256'],x['path']
def freeze_check(f):
    assert f['local_acceptance']['status']=='passed' and f['local_acceptance']['evidence']
    for name,digest in f['source_hashes'].items():assert sha(ROOT/name)==digest,name

def main():
    freeze_path=pathlib.Path(sys.argv[1]).resolve(strict=True);f=json.loads(freeze_path.read_text());freeze_check(f)
    assert sha(BASE/'pmp4010-manual-mmps-payload.tar.gz')==ARCHIVE_SHA
    assert sha(BASE/'payload/payload-manifest.json')==MANIFEST_SHA
    old=json.loads((BASE/'payload/payload-manifest.json').read_text());inventory(BASE/'payload',old['files'])
    payload=P/'payload';roundtrip=P/'roundtrip';payload.mkdir(exist_ok=False);roundtrip.mkdir(exist_ok=False)
    entries=[];replaced=[];added=[]
    reverse={v:k for k,v in ALLOWED.items()}
    old_paths={x['path'] for x in old['files']}
    for item in old['files']:
        name=item['path'];dest=payload/name;dest.parent.mkdir(parents=True,exist_ok=True)
        entry=dict(path=name,provenance='byte-identical trusted 76f8db43 payload')
        if 'symlink' in item:
            dest.symlink_to(item['symlink']);entry['symlink']=item['symlink']
        else:
            src=BASE/'payload'/name
            if name.startswith('drivers/pmp4010/') and name.endswith('.py'):
                source='device/drivers/pmp4010/tools/'+pathlib.Path(name).name
            elif name.startswith('hwtui/') and name.endswith('.py'):
                source='tools/'+name
            else:source=None
            if source and sha(ROOT/source)!=item['sha256']:
                assert source in ALLOWED and source in f['source_hashes'],'unexpected or unfrozen production delta '+source
                src=ROOT/source;replaced.append(source);entry['provenance']='current frozen '+source
            shutil.copy2(src,dest)
            if name=='load_smoke.py':
                text=dest.read_text();assert "'pmp4010_simple'," in text
                text=text.replace("'pmp4010_simple',", "'pmp4010_simple', 'pmp4010_simple_view',")
                text=text.replace("    velocity = sys.modules['pmp4010_velocity']", "    print('imported_modules=' + str(len(names)), flush=True)\n    velocity = sys.modules['pmp4010_velocity']")
                dest.write_text(text);entry['provenance']='baseline no-I/O smoke plus explicit new view module coverage and import count'
            if name=='README-手动入口.md':
                dest.write_text((BASE/'payload'/name).read_text()+'''\n## 手动界面重设计（2026-10-20）\n\n主屏、输入、审阅、帮助和诊断使用中文分区与终端颜色；布局适配终端尺寸。\n这次只替换呈现模块；操作键、mm/s速度、审阅确认和所有运动保护沿用上一版。\n新增pmp4010_simple_view.py随包交付；共享tui.py保持默认渲染字节，手动界面选择增强渲染。\nload_smoke.py实际导入21个模块，阻断socket/进程I/O；33 native、33 Python、14 int32向量。\n板端只做无CAN加载、清单与帮助检查；真实通信、运动、抱闸和负载仍待现场验证。\n''');entry['provenance']='baseline mm/s instructions plus redesigned UI usage and acceptance scope'
            entry.update(sha256=sha(dest),size=dest.stat().st_size)
        entries.append(entry)
    for source,name in ALLOWED.items():
        if name in old_paths:continue
        assert source in f['source_hashes'],'new module absent from freeze'
        dest=payload/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/source,dest)
        entries.append(dict(path=name,provenance='current frozen '+source,sha256=sha(dest),size=dest.stat().st_size));added.append(source)
    assert set(replaced)=={'device/drivers/pmp4010/tools/pmp4010_simple.py','tools/hwtui/tui.py'}
    assert added==['device/drivers/pmp4010/tools/pmp4010_simple_view.py']
    head=r.run('source-head',['git','rev-parse','HEAD']).decode().strip()
    manifest=dict(stage='manual_ui_redesign_2026_10_20',source_head=head,source_freeze=f['source_hashes'],local_acceptance=f['local_acceptance'],freeze_input_sha256=sha(freeze_path),baseline_archive_sha256=ARCHIVE_SHA,baseline_manifest_sha256=MANIFEST_SHA,production_files_replaced=replaced,production_files_added=added,files=entries,hardware_acceptance=False)
    write(payload/'payload-manifest.json',manifest);inventory(payload,entries)
    assert sha(payload/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0')==ARM_SHA
    assert sha(payload/'run-manual.sh')==LAUNCHER_SHA
    assert (payload/'drivers/pmp4010/od.json').read_bytes()==(BASE/'payload/drivers/pmp4010/od.json').read_bytes()
    r.run('launcher-syntax',['sh','-n',str(payload/'run-manual.sh')])
    smoke=r.run('host-load-smoke',[sys.executable,'-I','-B',str(payload/'load_smoke.py'),'--library',str(ROOT/'build-host/libpmp4010_velocity.so'),'--check-missing-library'])
    assert b'imported_modules=21\n' in smoke
    assert json.loads(smoke.decode().splitlines()[-1])==dict(result='PASS',native_vectors=33,python_vectors=33,native_int32_vectors=14,forbidden_io_attempts=0,hardware_acceptance=False)
    archive=P/'pmp4010-manual-ui-payload.tar.gz';assert not archive.exists()
    with tarfile.open(archive,'w:gz',dereference=False) as tar:
        for name in [x['path'] for x in entries]+['payload-manifest.json']:tar.add(payload/name,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();assert len(members)==len(entries)+1
        expected={x['path']:x for x in entries}
        for m in members:
            path=pathlib.PurePosixPath(m.name);assert not path.is_absolute() and '..' not in path.parts
            dest=roundtrip/m.name;dest.parent.mkdir(parents=True,exist_ok=True)
            if m.issym():assert expected[m.name]['symlink']==m.linkname;dest.symlink_to(m.linkname)
            else:
                assert m.isfile();data=tar.extractfile(m).read();assert data==(payload/m.name).read_bytes()
                with dest.open('xb') as out:out.write(data)
                dest.chmod(m.mode&0o777)
    inventory(roundtrip,entries);assert sha(roundtrip/'payload-manifest.json')==sha(payload/'payload-manifest.json')
    r.run('roundtrip-smoke',[sys.executable,'-I','-B',str(roundtrip/'load_smoke.py'),'--library',str(ROOT/'build-host/libpmp4010_velocity.so'),'--check-missing-library'])
    freeze_check(f);shutil.copy2(freeze_path,P/'source-freeze-input.json')
    summary=dict(archive=str(archive),archive_sha256=sha(archive),manifest_sha256=sha(payload/'payload-manifest.json'),archive_members=len(entries)+1,manifest_entries=len(entries),regular_files=sum('symlink' not in x for x in entries),symlinks=sum('symlink' in x for x in entries),production_files_replaced=replaced,production_files_added=added,arm_library_sha256=ARM_SHA,launcher_sha256=sha(payload/'run-manual.sh'),source_freeze=f['source_hashes'],hardware_acceptance=False)
    write(P/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
