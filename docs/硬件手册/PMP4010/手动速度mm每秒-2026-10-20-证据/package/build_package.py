#!/usr/bin/env python3
"""Create the mm/s Python package from an immutable trusted payload and source freeze."""
import hashlib,importlib.util,json,os,pathlib,shutil,sys,tarfile
ROOT=pathlib.Path('/home/zakiu/km3');P=pathlib.Path(__file__).resolve().parent;E=P.parent
BASE=ROOT/'docs/硬件手册/PMP4010/抱闸手动测试-2026-10-19-证据/package'
ARCHIVE_SHA='e137c603d57c584134f14f006bcae482d3b999ce1d2dda8dd25b19005a80b237'
MANIFEST_SHA='8cf6fa0d6efdfb3831ff7e5397fd67314aa3ea433969e07d51478296634caa51'
ARM_SHA='59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
spec=importlib.util.spec_from_file_location('record',E/'acceptance/record.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);r.OUT=P

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
    assert sha(BASE/'pmp4010-brake-offline-payload.tar.gz')==ARCHIVE_SHA
    assert sha(BASE/'payload/payload-manifest.json')==MANIFEST_SHA
    old=json.loads((BASE/'payload/payload-manifest.json').read_text());inventory(BASE/'payload',old['files'])
    payload=P/'payload';roundtrip=P/'roundtrip';payload.mkdir(exist_ok=False);roundtrip.mkdir(exist_ok=False)
    replaced=[];entries=[]
    for item in old['files']:
        name=item['path'];dest=payload/name;dest.parent.mkdir(parents=True,exist_ok=True)
        entry=dict(path=name,provenance='byte-identical trusted e137c603 payload')
        if 'symlink' in item:
            dest.symlink_to(item['symlink']);entry['symlink']=item['symlink']
        else:
            src=BASE/'payload'/name
            if name.startswith('drivers/pmp4010/') and name.endswith('.py'):
                production='device/drivers/pmp4010/tools/'+pathlib.Path(name).name
                current=ROOT/production
                if sha(current)!=item['sha256']:
                    assert production in f['source_hashes'],'unfrozen production delta '+production
                    assert production in ['device/drivers/pmp4010/tools/pmp4010_simple.py','device/drivers/pmp4010/tools/pmp4010.py'],'unexpected production delta'
                    src=current;replaced.append(production);entry['provenance']='current frozen '+production
            shutil.copy2(src,dest)
            if name=='README-手动入口.md':
                dest.write_text('''# PMP4010 自实现手动测试：直线速度 mm/s

run-manual.sh默认node1、manual-linear、4mm/电机轴转；配置can-2解析到can1。连接与会话授权按Enter，无需手填OD理由。
线性模式s设置mm/s，主屏实际/下一速度、审阅、帮助均为mm/s；角度模式仍rpm。
换算为 mm/s×60÷每电机轴转毫米，再调用唯一C++ rpm→U/s函数；底层单位仍U/s。
默认仍1000U/s，不提高原速度；--manual-velocity继续明确接受U/s兼容值。
c修改mm/电机轴转不会自动改变设备U/s，清除旧请求并使旧审阅失效。
软件零点z、相对r/j、绝对p、步长a、正反f/b、保持使能e、失能d、2312受限复位t均保留。
输入参数Enter审阅，再Enter执行；Space/x停止、q安全退出。真实通信、限位、标定、607C、状态与隔离保护不变。
日志保存在包内logs/manual-XXXXXXXX/。本包不配置CAN、不自动使能/复位、不更改正式OD状态。
本轮本地Python与PTY为mock验证；板端只做禁止I/O加载及帮助，不代表实际CAN、固件、负载、抱闸或运动验收。
宿主离线加载：python3 -I -B ./load_smoke.py --library /home/zakiu/km3/build-host/libpmp4010_velocity.so --check-missing-library
该检查阻断socket/进程I/O，验证20模块、ABI和33native/33Python/14int32向量；宿主不得加载包内AArch64库。
实际板端更新状态与稳定命令见同轮验收与部署报告；保留run-brake.sh及原抱闸说明。
''');entry['provenance']='new package usage for mm/s; original archived README preserved'
            entry.update(sha256=sha(dest),size=dest.stat().st_size)
        entries.append(entry)
    assert 'device/drivers/pmp4010/tools/pmp4010_simple.py' in replaced
    head=r.run('source-head',['git','rev-parse','HEAD']).decode().strip()
    manifest=dict(stage='manual_linear_mmps_2026_10_20',source_head=head,source_freeze=f['source_hashes'],local_acceptance=f['local_acceptance'],freeze_input_sha256=sha(freeze_path),baseline_archive_sha256=ARCHIVE_SHA,baseline_manifest_sha256=MANIFEST_SHA,production_files_replaced=replaced,files=entries,hardware_acceptance=False)
    write(payload/'payload-manifest.json',manifest);inventory(payload,entries)
    assert sha(payload/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0')==ARM_SHA
    r.run('launcher-syntax',['sh','-n',str(payload/'run-manual.sh')])
    smoke=r.run('host-load-smoke',[sys.executable,'-I','-B',str(payload/'load_smoke.py'),'--library',str(ROOT/'build-host/libpmp4010_velocity.so'),'--check-missing-library'])
    assert json.loads(smoke.decode().splitlines()[-1])==dict(result='PASS',native_vectors=33,python_vectors=33,native_int32_vectors=14,forbidden_io_attempts=0,hardware_acceptance=False)
    archive=P/'pmp4010-manual-mmps-payload.tar.gz';assert not archive.exists()
    with tarfile.open(archive,'w:gz',dereference=False) as tar:
        for name in [x['path'] for x in entries]+['payload-manifest.json']:tar.add(payload/name,arcname=name,recursive=False)
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();assert len(members)==28
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
    summary=dict(archive=str(archive),archive_sha256=sha(archive),manifest_sha256=sha(payload/'payload-manifest.json'),archive_members=28,regular_files=26,symlinks=2,production_files_replaced=replaced,arm_library_sha256=ARM_SHA,launcher_sha256=sha(payload/'run-manual.sh'),source_freeze=f['source_hashes'],hardware_acceptance=False)
    write(P/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
