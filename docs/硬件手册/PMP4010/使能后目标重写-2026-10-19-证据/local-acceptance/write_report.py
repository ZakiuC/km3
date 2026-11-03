from pathlib import Path
import hashlib,json,shlex
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010/使能后目标重写-2026-10-19-证据';OUT=BASE/'local-acceptance'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
approved=json.loads((BASE/'implementation/final-source-sha256.json').read_text())
native=json.loads((ROOT/'docs/硬件手册/PMP4010/正常驱动验收-2026-10-19-证据/native-current/source-after-sha256.json').read_text())['files']
allfiles=json.loads((OUT/'freeze-source-sha256.json').read_text())['files']
check={'approved_six_match':all(sha(ROOT/p)==h for p,h in approved.items()),'native_77_match':len(native)==77 and all(sha(ROOT/p)==h for p,h in native.items()),'acceptance_files_match':all(sha(ROOT/p)==h for p,h in allfiles.items())}
assert all(check.values())
(OUT/'freeze-after-package.json').write_text(json.dumps(check,indent=2)+'\n')
provenance=[{'path':str(p.relative_to(ROOT)),'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted((BASE/'implementation').rglob('*')) if p.is_file()]
(OUT/'implementation-evidence-provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
pkg=json.loads((BASE/'package/summary.json').read_text());rows=json.loads((OUT/'command-index.json').read_text())
lines=['# PMP4010 使能后目标重写受控对照（2026-10-19）','','本轮本地验收与独立包完成，实机对照尚待执行。目标重写是受控协议对照，固件dirty flag或使能后数据失效仍是假设，根因未确证。旧c20f包 live-03/04 已成功触发但未正向到达冻结目标，旧报告结论不变。','','## 行为与冻结','','go 在初始 Operation enabled、Halt12F、ACK0、零速完整proof后，通过同一client对同一冻结607A重写一次并读回，再核对完整proof，随后执行既有2F→3F→2F及fresh ACK0→1→0。目标不重算、其他参数不变，写attempt前登记cleanup责任并保持固定deadline。','', '五文件变更：manual/simple及三项相关tests；legacy `_test_manual_pty.py` 未改，纳入六文件冻结身份。manual SHA256 `'+approved['device/drivers/pmp4010/tools/pmp4010_manual.py']+'`，simple SHA256 `'+approved['device/drivers/pmp4010/tools/pmp4010_simple.py']+'`。完整冻结见 implementation/final-source-sha256.json。','','## 本地实测','','三套Python '+str(pkg['python_test_counts']['model'])+'/217/8，均rc=0（上一轮382，最初用户178底线）。public生产CLI curses MockBus PTY34观察点单独执行rc0，观察点不计入unit。四门、三个生成器和help均rc0。','','native-current此前fresh Host/ASan/TSan各17/17，77项输入在本轮验收前后及打包后保持匹配；本轮未重编或重跑native。ARM库沿用59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404，未在host执行ARM。','','|项目|rc|argv|','|---|---:|---|']
for r in rows:lines.append('|'+r['name']+'|'+str(r['returncode'])+'|`'+shlex.join(r['argv'])+'`|')
lines+=['','上述cwd均为 `/home/zakiu/km3`。所有完整stdout/stderr与哈希见 local-acceptance/command-index.json。','','## 四门与三个生成器原输出','']
for r in rows:
    if r['name'] not in ['drivers','docs','deps','manual-citations','gen-od','gen-canopen','gen-access']:continue
    lines +=['### '+r['name'],'','```text','argv='+shlex.join(r['argv']),'cwd='+r['cwd'],'rc='+str(r['returncode']),'stdout:',(OUT/r['stdout']).read_text().rstrip(),'stderr:',(OUT/r['stderr']).read_text().rstrip(),'```','']
lines+=['## 假设模型的旧失败与新通过','','require_enabled_target模型显式假设enable后目标需刷新，不是PMP4010固件事实。实现方old-repro隔离导入保存c20f，预期rc1：有边沿和ACK但无轨迹；newpass一次enabled-target下载后出现模型渐进反馈。全部失败与重跑留在implementation/，逐文件哈希见local-acceptance/implementation-evidence-provenance.json。','']
for name in ['old-repro-expected-failure.log','newpass.log']:
    lines+=['### '+name,'','```text',(BASE/'implementation'/name).read_text().rstrip(),'```','']
lines+=['独立public PTY原输出：','','```text',(OUT/'independent-pty.stdout.log').read_text().rstrip(),'```','','## 独立包','',f"归档 `使能后目标重写-2026-10-19-证据/package/pmp4010-manual-board-payload.tar.gz`，{pkg['archive_bytes']} bytes，SHA256 `{pkg['archive_sha256']}`。manifest SHA256 `{pkg['manifest_sha256']}`。",'','24成员=22普通文件+2符号链接，23 manifest entries、18imports、ABI1、native33/Python33/int32 14。缺库/导入前篡改拒绝、sh-n/help/nonTTY/覆盖参数拒绝和roundtrip符合预期，禁止I/O尝试0。launcher交互沿用，包元信息与README为本轮名称及实际数量。详见package/command-index.json和summary.json。','','本执行者未SSH/CAN/部署/硬件动作。板端纯加载、真实运动和根因判断仍待主线独立验收；报告暂不结案，不因模拟通过追认现场成功。runtime/input usage unknown；父路由保守估计小于130K，非实测。','']
p=ROOT/'docs/硬件手册/PMP4010/使能后目标重写-2026-10-19.md';assert not p.exists();p.write_text('\n'.join(lines));print(p)
