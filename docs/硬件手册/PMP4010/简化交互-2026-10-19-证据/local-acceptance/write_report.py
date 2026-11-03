from pathlib import Path
import hashlib,json,shlex
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010';EV=BASE/'简化交互-2026-10-19-证据';OUT=EV/'local-acceptance';PKG=EV/'package';REPORT=BASE/'简化交互-2026-10-19.md'
def read(p):return json.loads(p.read_text())
idx=read(OUT/'index-final.json');pkg=read(PKG/'summary.json');pty=read(OUT/'independent-pty/summary.json')
model=(OUT/'model.stderr.log').read_text();import re
count=re.search(r'Ran (\d+) tests?',model).group(1)
files=read(OUT/'freeze-source-sha256.json')['files'];old=read(BASE/'手动测试入口-2026-10-19-证据/local-acceptance/final-freeze-source-sha256.json')['files']
changed=[row['path'] for row in read(OUT/'root-approved-source-freeze.json')['files']]
text=f'''# PMP4010 简化手动交互（2026-10-19）

已将原来的 A/R→E→G、F2/动态 token 多步操作改为单屏：`s`设置速度、`a`设置步长，`f/b`后按 Enter 确认点动，`e`后按 Enter 确认保持当前位置使能，`Space/x`在任何页面停止，`d`失能、`q`退出；RT/OD每会话确认一次。模型Python由348项增至本次实测{count}项，hwtui217项、tools8项。本地模拟、真实curses PTY及独立包校验已完成，真实电机使能/点动仍待用户本人执行；前次约+13U偏移的物理原因仍未知，本轮不构成根因修复或硬件运动通过。

本轮业务源码/测试共7个文件；另新增本报告及同名证据目录：

'''+''.join(f'- `{p}`\n' for p in changed)+f'''
操作时默认80×24画面显示速度/步长及单位、运行状态和阻止原因。1度预算、607C、故障、通信异常和隔离保护继续复用原事务逻辑。`e`保持当前位置并不构成机械制动器证明；软件停止不等同失能。

独立包：`{pkg['archive']}`，SHA256 `{pkg['archive_sha256']}`，{pkg['archive_bytes']} bytes。Manifest：`{pkg['manifest']}`，SHA256 `{pkg['manifest_sha256']}`。归档共{pkg['archive_members']}成员（普通文件{pkg['regular_files']}、符号链接{pkg['symlinks']}），不含测试。

用户本人在获授权的板端目录运行 `./run-manual.sh`；脚本固定节点1与范围1，只接受设置/显示/超时选项，拒绝RT/OD/动作参数。不自动代填确认。日志新建于 `logs/manual-XXXXXXXX/`。当前本地包尚不代表板端加载或硬件验收；板端证据由独立获授权的执行方补充。

本轮实测：三套Python {count}/217/8、四闸门和三生成器均rc0；真实生产 `--manual` 入口以标准MockBus运行curses PTY，禁止真实socket。PTY summary原文如下；其中checks/观察点不代表同等数量的独立unit tests，全量Python包含PTY测试。

```json
{json.dumps(pty,ensure_ascii=False,indent=2)}
```

包内实际导入{pkg['imports']}模块；native33/Python33向量与14个Int32向量，禁止IO尝试0；missing-native明确拒绝，roundtrip重新加载通过，故意篡改simple模块副本在导入前拒绝；help、非TTY拒绝、参数限制和sh -n均保留原始输出。ARM库 SHA256 `{pkg['arm_library_sha256']}` 与旧产物一致，沿用旧库，本轮未重新交叉编译、未在主机执行ARM。

C++本轮未改、未重跑。上一轮实际Host/ASan/TSan各17/17（TSan使用setarch -R）的完整原命令/输出在 `手动测试入口-2026-10-19-证据/local-acceptance/native-command-index.json`；本轮只核对其日志哈希和native数学库构建输入，不声称该小清单覆盖所有C++测试输入。引用记录见 `local-acceptance/historical-native-reference.json`。

最终源冻结before/after一致：`{idx['source_before_after_match']}`；完整冻结见 `local-acceptance/freeze-source-sha256.json`。本报告和索引不参与自身哈希。路由请求light_worker/GPT-6.1 Sol/medium；实际model/effort/input usage不可见，记unknown；当前有界上下文估计低于140K，仅估计，非实测。

本地原始证据在 `简化交互-2026-10-19-证据/local-acceptance/`，包原始证据在 `package/`。此前两轮候选分别保存在 `initial-source-raced/` 与 `pre-refresh-cancel-fix/`：各命令rc0但源冻结前后不一致，且独立PTY误跑旧manual脚本，故均不作为最终新界面验收；旧候选包保存在 `package/candidate-pre-refresh-cancel-fix/`，不可用于交付。最终以下正式命令使用 `_test_simple_pty.py`。

原eaeb23d5844e包因launcher英文错误提示被撤回，原包/原payload/原checks完整保留在 `package/withdrawn-eaeb23d5844e/`。正式包只改该中文提示，业务源冻结不变。

四份80×24快照的行列、可见单位与确认/取消提示另经 `local-acceptance/screen-80x24-check.json`核对；真实终端字节保留在PTY的 `terminal.ansi`。报告写入后再次运行 `bash tools/check-docs.sh`，完整argv/rc/stdout/stderr见 `local-acceptance/docs-after-report.command.json`。

下面列完整独立argv与rc；stdout/stderr按命令同名前缀保存，不拼接命令。

'''
for label,folder,rows in [('本地',OUT,idx['records']),('独立包',PKG,read(PKG/'command-index.json'))]:
 text+=f'## {label}命令记录\n\n'
 for row in rows:
  text+='```sh\n'+shlex.join(row['argv'])+'\n```\n\n'+f"rc={row['returncode']}；stdout: `{folder.relative_to(BASE)}/{row['stdout']}`；stderr: `{folder.relative_to(BASE)}/{row['stderr']}`。\n\n"
REPORT.write_text(text)
print(str(REPORT))
