from pathlib import Path
import hashlib,json,shlex,subprocess,datetime
ROOT=Path('/home/zakiu/km3')
BASE=ROOT/'docs/硬件手册/PMP4010/使能后触发修复-2026-10-19-证据'
OUT=BASE/'local-acceptance'
REPORT=ROOT/'docs/硬件手册/PMP4010/使能后触发修复-2026-10-19.md'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
approved=json.loads((BASE/'implementation/final-source-sha256.json').read_text())
native=json.loads((ROOT/'docs/硬件手册/PMP4010/正常驱动验收-2026-10-19-证据/native-current/source-after-sha256.json').read_text())['files']
check={'approved_six_match':all(sha(ROOT/p)==h for p,h in approved.items()),'native_77_match':len(native)==77 and all(sha(ROOT/p)==h for p,h in native.items()),'all_acceptance_source_match':all(sha(ROOT/p)==h for p,h in json.loads((OUT/'freeze-source-sha256.json').read_text())['files'].items()),'archive_sha256':sha(BASE/'package/pmp4010-manual-board-payload.tar.gz')}
assert all(check[k] for k in ['approved_six_match','native_77_match','all_acceptance_source_match'])
(OUT/'freeze-after-package.json').write_text(json.dumps(check,indent=2)+'\n')
provenance={'source':str(BASE/'implementation'),'files':[{'path':str(p.relative_to(ROOT)),'size':p.stat().st_size,'sha256':sha(p)} for p in sorted((BASE/'implementation').rglob('*')) if p.is_file()], 'retained_failed_runs':json.loads((BASE/'implementation/command-index.json').read_text())['retained_failed_runs']}
(OUT/'implementation-evidence-provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
rows=json.loads((OUT/'command-index.json').read_text())
pkg=json.loads((BASE/'package/summary.json').read_text())
lines=['# PMP4010 使能后触发修复验收（2026-10-19）','','本轮本地验收完成；真实电机驱动、板端纯加载与现场动作尚未执行。独立包已生成，未部署。','','## 修复与预期行为','','`pmp4010_manual.py` 在未使能阶段保留安全目标预置；go 在 Operation enabled 阶段发出 2F→3F→2F，并在固定 deadline 内逐阶段确认 fresh ACK0→1→0，同时核对状态、模式、冻结目标、6062、标定、限幅和故障。`pmp4010_simple.py` 更新确认/帮助文案。未改 OD 或 C++ 协议语义。','','实现最终冻结六文件：manual/simple 与 `_test_manual.py`、`_test_simple.py`、`_test_simple_pty.py`、`_test_manual_pty.py`。完整 SHA256 见 `使能后触发修复-2026-10-19-证据/implementation/final-source-sha256.json`。','','## 本地结果','','三套 Python：382（原375，本轮增加7；高于原178底线）/217/8，均 rc=0。独立生产 CLI curses MockBus PTY：34观察点，rc=0；这些观察点不计入单元测试数量。实测模拟节点只有 Operation enabled 新边沿才移动，反馈到达冻结目标。','','fresh native-current 的 Host/ASan/TSan 各17/17为本轮先前已完成证据；此次未重编/重跑 C++。其77项源码输入与本轮冻结前、验收后、打包后均匹配。ARM库沿用 SHA256 `59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404`；宿主未执行 ARM 库。','','|检查|rc|完整 argv|','|---|---:|---|']
for row in rows: lines.append('|'+row['name']+'|'+str(row['returncode'])+'|`'+shlex.join(row['argv'])+'`|')
lines+=['','所有命令 cwd 为 `/home/zakiu/km3`；完整 argv、rc、stdout/stderr 与其哈希见 `使能后触发修复-2026-10-19-证据/local-acceptance/command-index.json`。','', '## 四门与三个生成器原始输出','']
for row in rows:
    if row['name'] not in ['drivers','docs','deps','manual-citations','gen-od','gen-canopen','gen-access']:continue
    lines+=['### '+row['name'],'','```text','argv='+shlex.join(row['argv']),'cwd='+row['cwd'],'rc='+str(row['returncode']),'stdout:',(OUT/row['stdout']).read_text().rstrip(),'stderr:',(OUT/row['stderr']).read_text().rstrip(),'```','']
lines+=['## oldfail/newpass 与失败留档','','实现方 oldfail rc=1：旧实现没有 Operation enabled 边沿、模拟位置保持不变；newpass rc=0：新实现仅一次新边沿并出现真实模型位置渐进反馈。以下均为离线模拟，不构成实机验收。','']
for name in ['oldfail.log','newpass.log']:
    lines+=['### '+name,'','```text',(BASE/'implementation'/name).read_text().rstrip(),'```','']
lines+=['实现方全部失败/重跑原日志保留在 implementation/，`implementation-evidence-provenance.json` 索引每一文件及哈希，未删除失败。legacy PTY27由实现方实跑，本轮未冗余单独运行；独立 simple PTY34 原输出如下。','','```text',(OUT/'independent-pty.stdout.log').read_text().rstrip(),'```','','## 独立板端包','',f"归档：`使能后触发修复-2026-10-19-证据/package/pmp4010-manual-board-payload.tar.gz`，{pkg['archive_bytes']} bytes；SHA256 `{pkg['archive_sha256']}`。",'',f"24成员（22普通文件、2符号链接），23 manifest entries、18 imports；ABI1、native33/Python33/int32 14，禁止I/O尝试0。manifest导入前验哈希、缺库拒绝、篡改拒绝、shell语法、中文help、nonTTY拒绝、节点/范围覆盖拒绝及归档roundtrip检查均符合预期。launcher沿用 node1/node-range1 和现有配置 can-2→can1 解析行为，未改变交互。",'','完整包命令及预期拒绝rc见 `使能后触发修复-2026-10-19-证据/package/command-index.json`，包摘要见 summary.json。README及manifest标识为本轮修复，实际Python数量382/217/8。','','## 边界','','本执行者未 SSH、未打开真实 CAN socket、未部署、未启动服务或硬件动作。当前结论为本地模拟/静态/包加载验证；实机正常驱动和现场反馈仍由主线另行验证。路由 runtime model/effort、last_token_usage.input_tokens 均 unknown；fresh packet 加准备工作估计小于35K，仅估计。','']
assert not REPORT.exists(), 'refuse overwrite report'
REPORT.write_text('\n'.join(lines))
argv=['bash','tools/check-docs.sh']
p=subprocess.run(argv,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
row={'name':'docs-after-report','argv':argv,'cwd':str(ROOT),'returncode':p.returncode,'stdout':'docs-after-report.stdout.log','stderr':'docs-after-report.stderr.log'}
for kind in ['stdout','stderr']:(OUT/row[kind]).write_bytes(getattr(p,kind));row[kind+'_sha256']=hashlib.sha256(getattr(p,kind)).hexdigest()
(OUT/'docs-after-report.command.json').write_text(json.dumps(row,indent=2)+'\n')
rows.append(row);(OUT/'command-index.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
assert p.returncode==0,p.stderr
with REPORT.open('a') as f:f.write('\n报告生成后 check-docs.sh rc=0，原始输出：\n\n```text\n'+p.stdout.decode()+p.stderr.decode()+'```\n')
print(json.dumps({'report':str(REPORT),'docs_after_report_rc':p.returncode,'freeze_after_package':check},ensure_ascii=False,indent=2))
