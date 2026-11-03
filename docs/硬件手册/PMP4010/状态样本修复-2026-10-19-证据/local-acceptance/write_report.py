from pathlib import Path
import json,re,shlex
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010';EV=BASE/'状态样本修复-2026-10-19-证据';OUT=EV/'local-acceptance';PKG=EV/'package';REPORT=BASE/'状态样本修复-2026-10-19.md'
def read(p):return json.loads(p.read_text())
def count(name):return int(re.search(r'Ran (\d+) tests?',(OUT/(name+'.stderr.log')).read_text()).group(1))
index=read(OUT/'index-final.json');pkg=read(PKG/'summary.json');pty=read(OUT/'independent-pty/summary.json');approved=read(OUT/'root-approved-source-freeze.json')
assert index['all_returncodes_zero'] and index['source_before_after_match']
text=f'''# PMP4010 状态样本修复（2026-10-19）

现场b579包的三份日志显示目标/profile已写入，后续manual状态采样拒绝；旧日志缺少同一时刻的原始6041h状态字与控制字，不能确证现场物理根因。代码审查确认一处独立缺陷：控制字确认后立即只接受目标状态，会将仍处于合法前态的切换延迟误判为异常。本轮以有界、受控的previous→target等待修复该缺陷，并补充raw状态/phase/CW/cleanup诊断；保留原保护。该修复不证明现场所有故障已解决，真实手动使能/点动仍待用户本人执行。

模型Python基线364→本次实测{count('model')}项，hwtui{count('hwtui')}项、tools{count('tools-tests')}项。三套Python、四闸门、三生成器、新单屏生产入口PTY均rc0，最终源冻结前后相同。本轮修改文件以root批准清单为准：

'''+''.join('- `'+row['path']+'`\n' for row in approved['files'])+f'''
另新增本报告及同名证据目录；旧简化交互报告与证据保留。

旧→新复现使用同一测试方法 `_test_manual.ManualTests.test_controlled_106_107_accept_delayed_previous_first_and_last_samples`。原实现写0106h后，MockBus 经 SDO 事务返回的状态样本依次为0260h/0260h/0231h，写0107h后为0231h/0231h/0233h：旧源码首次合法前态即抛“关键状态样本异常”，原始 `pmp4010-delayed-old-failure.log` 为rc1；修复后同用例 `pmp4010-delayed-new-pass.log` 为rc0，固定607Ah目标-1434 U完成提交。两份原日志由实现方运行，本验收方原样保留；旧日志是在修复前运行，不表示当前源码仍会得到rc1。

旧源为前包b579的 `pmp4010_manual.py`，SHA256 `cd17750a16fdc185fd5a45ea5182c7405fa6397b5932d0ae34c9e7337b1af1c8`；可从保留的前包payload取该文件，在隔离目录加载到该单一测试方法，再切换到本轮manual比较，勿覆盖工作区源码。源引用、原日志哈希和执行方边界见 `local-acceptance/worker-evidence-provenance.json`。当前完整375项及独立生产PTY另由本验收方实跑，PTY包含包含模拟状态延迟的完整 f→Enter 流程；实现方manual43/simple18专项是子集，不另加到总量。

另独立实跑 `local-acceptance/locked-repro.py`，非预期状态0237h仍被拒绝；保留首故障phase“等待受控迁移”、CW0106h、raw6041=0237h，控制字序列为0106h→0002h→0000h。80×24锁定屏明确“当前实读状态未知”及“最后有效采样”，并保留历史诊断，未用最后有效位置冒称当前实读。原始命令/rc0/stdout/stderr见 `local-acceptance/locked-repro.command.json`，屏幕与JSONL见 `local-acceptance/independent-locked-recorded/`。

独立包：`{pkg['archive']}`；SHA256 `{pkg['archive_sha256']}`；{pkg['archive_bytes']} bytes。Manifest：`{pkg['manifest']}`；SHA256 `{pkg['manifest_sha256']}`。实际归档{pkg['archive_members']}成员（{pkg['regular_files']}普通文件+{pkg['symlinks']}符号链接），导入{pkg['imports']}模块，不含测试。中文launcher固定node1/range1，拒绝RT/OD/动作参数及非法设置；保留用户本人确认。

生产 `--manual` 入口的真实curses/标准MockBus PTY记录{pty['passed']}个观察点，rc{pty['rc']}，不将观察点数当作独立unit tests；真实socket创建被禁止。完整终端字节、状态、请求与80×24快照见 `local-acceptance/independent-pty/`。

包加载、native33/Python33向量、14个Int32向量、missing-native拒绝、tamper导入前拒绝、roundtrip、help、nonTTY拒绝、sh-n及参数限制原始输出均保留。ARM库SHA256 `{pkg['arm_library_sha256']}` 与旧产物一致，本轮沿用旧库，未重新交叉编译、未在主机执行ARM。

C++本轮未修改、未重跑。历史Host/ASan/TSan实际各17/17，TSan使用setarch -R；原命令及原输出引用见 `local-acceptance/historical-native-reference.json`。仅核对native数学库输入和已有日志哈希，不声称小清单覆盖全部C++测试输入。

当前包只完成本地验收，尚未由本任务部署或运行真实TUI。板端只读现场证据由board-readonly独立持有；真实当前电机状态与物理原因不由本地mock推断。

路由请求light_worker/GPT-6.1 Sol/medium；actual model/effort/last input usage不可见，记unknown。基于父任务摘要与有限输出保守估计输入低于180K（父任务估计155K加有限验收输出），仅估计。报告和索引不参与自身哈希。报告后文档闸门记录见 `local-acceptance/docs-after-report.command.json`。

'''
for title,folder,rows in [('本地原始命令',OUT,index['records']),('独立包原始命令',PKG,read(PKG/'command-index.json'))]:
 text+='## '+title+'\n\n'
 for row in rows:
  text+='```sh\n'+shlex.join(row['argv'])+'\n```\n\n'+f"rc={row['returncode']}；stdout: `{folder.relative_to(BASE)}/{row['stdout']}`；stderr: `{folder.relative_to(BASE)}/{row['stderr']}`。\n\n"
REPORT.write_text(text)
print(REPORT)
