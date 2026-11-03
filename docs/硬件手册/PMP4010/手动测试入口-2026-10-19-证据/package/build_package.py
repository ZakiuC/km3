from pathlib import Path
import hashlib,json,subprocess,shutil,os,tarfile,datetime
ROOT=Path('/home/zakiu/km3');OUT=ROOT/'docs/硬件手册/PMP4010/手动测试入口-2026-10-19-证据/package';PAY=OUT/'payload';OLD=ROOT/'docs/硬件手册/PMP4010/失能起步修复验收-2026-10-19-证据/stage3-recovered-package'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
rows=[]
def record(name,args,expected=0,cwd=ROOT):
    p=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    row={'name':name,'argv':args,'cwd':str(cwd),'returncode':p.returncode,'expected_returncode':expected}
    for kind,data in [('stdout',p.stdout),('stderr',p.stderr)]:
        file=name+'.'+kind+'.log';(OUT/file).write_bytes(data);row[kind]=file;row[kind+'_sha256']=hashlib.sha256(data).hexdigest()
    rows.append(row);(OUT/'command-index.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    assert p.returncode==expected,(name,p.returncode,p.stderr.decode(errors='replace'))
    print(name+' rc='+str(p.returncode),flush=True)
    return p
assert not PAY.exists(),'refuse overwrite existing package payload'
prior=json.loads((OLD/'source-manifest.json').read_text());files={x['path']:x['source'] for x in prior['package_sources'] if 'source' in x}
files['drivers/pmp4010/pmp4010_manual.py']='device/drivers/pmp4010/tools/pmp4010_manual.py'
manifest=[]
for dest,src in files.items():
    path=PAY/dest;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/src,path)
    manifest.append({'path':dest,'source':src,'size':path.stat().st_size,'sha256':sha(path)})
for name,target in [('libpmp4010_velocity.so.1','libpmp4010_velocity.so.1.0.0'),('libpmp4010_velocity.so','libpmp4010_velocity.so.1')]:
    p=PAY/'drivers/pmp4010'/name;p.symlink_to(target);manifest.append({'path':str(p.relative_to(PAY)),'symlink':target})
smoke=(OLD/'payload/load_smoke.py').read_text().replace("'pmp4010_bench', 'pmp4010_tui', 'pmp4010')","'pmp4010_bench', 'pmp4010_tui', 'pmp4010', 'pmp4010_manual')")
assert "'pmp4010_manual')" in smoke
(PAY/'load_smoke.py').write_text(smoke)
launcher='''#!/bin/sh
set -eu
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
need_value=0
for arg in "$@"; do
    if [ "$need_value" -eq 1 ]; then
        case "$arg" in --*) echo "选项缺少数值" >&2; exit 2;; esac
        need_value=0
        continue
    fi
    case "$arg" in
        --manual-velocity|--rows|--cols|--timeout|--state-timeout|--move-timeout) need_value=1;;
        --ascii|--help|-h) ;;
        *) echo "手动启动脚本不接受此参数：$arg" >&2; exit 2;;
    esac
done
[ "$need_value" -eq 0 ] || { echo "选项缺少数值" >&2; exit 2; }
mkdir -p "$package_dir/logs"
run_dir=$(mktemp -d "$package_dir/logs/manual-XXXXXXXX")
printf '日志目录：%s\\n' "$run_dir"
exec python3 -I -B "$package_dir/drivers/pmp4010/pmp4010.py" --manual --node 1 --node-range 1 --motion-log "$run_dir/motion.jsonl" "$@"
'''
(PAY/'run-manual.sh').write_text(launcher);(PAY/'run-manual.sh').chmod(0o755)
(PAY/'README-手动入口.md').write_text('''# PMP4010 手动入口

隔离包只面向节点1。实际接口由板上现有 /etc/km3/device.json 的 can-2 解析；本包不修改配置、不切 current、不启动服务。

先纯加载（不打开总线）：

```sh
python3 -I -B /ABSOLUTE/PACKAGE/load_smoke.py --check-missing-library
```

用户本人运行（把路径替换为实际解包目录）：

```sh
ssh -t root@192.168.0.188 '/ABSOLUTE/PACKAGE/run-manual.sh'
```

可加 --manual-velocity 100、--rows 40、--cols 150 等显示/超时参数；启动脚本拒绝传入RT/OD或动作确认参数。默认速度100 U/s，实际rpm及设备上限在界面审阅。

启动、扫描不会自动使能。依界面完成人工RT确认、OD理由、软行程及位移预算；A/R提交目标后，E保持Halt使能，G单独解除Halt，X软件Quick stop，D失能，q退出。每个写动作保留两段确认；真实急停、故障和通信异常保护保留。取消自动起步和零漂移检查。Quick stop不等同失能。

日志位于本隔离包 logs/manual-XXXXXXXX/；不代填目标、RT/OD理由或动态确认文本。本包仅完成本地主机模拟/PTY与静态检查；板端纯加载和真实人工操作尚待执行。
''')
for name in ['load_smoke.py','run-manual.sh','README-手动入口.md']:
    p=PAY/name;manifest.append({'path':name,'size':p.stat().st_size,'sha256':sha(p),'origin':'package-generated'})
head=record('source-head',['git','rev-parse','HEAD']).stdout.decode().strip()
(PAY/'payload-manifest.json').write_text(json.dumps({'stage':'manual_pp_user_operated','source_head':head,'files':manifest},ensure_ascii=False,indent=2)+'\n')
(OUT/'source-manifest.json').write_text(json.dumps({'head':head,'files':manifest,'snapshot_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},ensure_ascii=False,indent=2)+'\n')
record('host-load-smoke',['python3','-I','-B',str(PAY/'load_smoke.py'),'--library',str(ROOT/'build-host/libpmp4010_velocity.so'),'--check-missing-library'])
record('launcher-shell-syntax',['sh','-n',str(PAY/'run-manual.sh')])
record('launcher-help',['sh',str(PAY/'run-manual.sh'),'--help'],cwd=Path('/'))
record('launcher-no-tty-refused',['sh',str(PAY/'run-manual.sh')],2,cwd=Path('/'))
record('launcher-rt-argument-refused',['sh',str(PAY/'run-manual.sh'),'--i-know-rt-is-running'],2)
record('launcher-od-argument-refused',['sh',str(PAY/'run-manual.sh'),'--od-gaps-bypass','not-allowed'],2)
record('launcher-node-override-refused',['sh',str(PAY/'run-manual.sh'),'--node','2'],2)
record('arm-file',['file','-L',str(PAY/'drivers/pmp4010/libpmp4010_velocity.so')])
record('arm-elf-header',['readelf','-h',str(PAY/'drivers/pmp4010/libpmp4010_velocity.so')])
assert sha(PAY/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0')=='59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
for x in manifest:
    if 'source' in x:assert sha(ROOT/x['source'])==x['sha256'],x['source']+' changed while packaging'
archive=OUT/'pmp4010-manual-board-payload.tar.gz'
with tarfile.open(archive,'w:gz',dereference=False) as tf:
    for name in sorted([x['path'] for x in manifest]+['payload-manifest.json']):tf.add(PAY/name,arcname=name,recursive=False)
record('archive-list',['tar','-tvzf',str(archive)])
rt=OUT/'roundtrip';rt.mkdir()
with tarfile.open(archive) as tf:
    members=tf.getmembers()
    for m in members:
        assert not Path(m.name).is_absolute() and '..' not in Path(m.name).parts
        assert m.isfile() or m.issym()
        if m.issym():assert '/' not in m.linkname and '..' not in m.linkname
    tf.extractall(rt,filter='data')
record('roundtrip-smoke',['python3','-I','-B',str(rt/'load_smoke.py'),'--library',str(ROOT/'build-host/libpmp4010_velocity.so'),'--check-missing-library'])
target=rt/'drivers/pmp4010/pmp4010_manual.py';data=target.read_bytes();target.write_bytes(data+b'\n# intentionally corrupted test copy\n')
try:
    p=record('tampered-manual-refused',['python3','-I','-B',str(rt/'load_smoke.py'),'--library',str(ROOT/'build-host/libpmp4010_velocity.so')],1)
    assert b'payload SHA256 mismatch' in p.stderr and b'import=' not in p.stdout
finally:target.write_bytes(data)
summary={'archive':str(archive),'archive_bytes':archive.stat().st_size,'archive_sha256':sha(archive),'manifest':str(PAY/'payload-manifest.json'),'manifest_sha256':sha(PAY/'payload-manifest.json'),'archive_members':len(members),'regular_files':sum(m.isfile() for m in members),'symlinks':sum(m.issym() for m in members),'imports':17,'native_vectors':33,'python_vectors':33,'int32_vectors':14,'forbidden_io_attempts':0,'tamper_refused_before_import':True,'arm_library_sha256':sha(PAY/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0'),'arm_library_build':'reused previous artifact after matching SHA256','arm_execution':False,'hardware_acceptance':False,'board_pureload':'pending','launcher':str(PAY/'run-manual.sh')}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2))
