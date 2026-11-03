from pathlib import Path
import hashlib,json
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010';EV=BASE/'状态样本修复-2026-10-19-证据';BOARD=EV/'board-stage';REPORT=BASE/'状态样本修复-2026-10-19.md'
final=json.loads((BOARD/'final-summary.json').read_text());post=json.loads((BOARD/'postcheck-summary.json').read_text());pkg=json.loads((EV/'package/summary.json').read_text())
assert final['archive_sha256']==pkg['archive_sha256'] and final['manifest_sha256']==pkg['manifest_sha256']
assert all(x==0 for x in final['step_rc'].values()) and final['imports_actual']==18
assert len(post['compared_keys'])==11 and all(post['equal'].values())
assert final['CAN_opens']==final['CAN_sends']==final['SDO_operations']==0 and not final['real_TUI_started'] and not final['launcher_executed']
text=REPORT.read_text().replace('当前包只完成本地验收，尚未由本任务部署或运行真实TUI。板端只读现场证据由board-readonly独立持有；真实当前电机状态与物理原因不由本地mock推断。','本地验收与板端隔离投放/纯加载已完成；真实TUI与电机使能/点动尚待用户本人执行。板端只读现场证据由board-readonly独立持有；真实当前电机状态与物理原因不由本地mock推断。')
section=f'''## 板端隔离投放与纯加载

独立获授权执行方于 `{final['utc']}` 完成正式43b0包单次上传、独立目录解包和纯加载；stage/pureload/sh-n及前后被动检查均rc0。板端实际18模块导入、AArch64 ABI1、33 native/33 Python/14 Int32向量、missing-native拒绝及help通过，禁止IO尝试0；归档24成员、manifest23项与本地冻结一致。实际加载的manual/simple/ARM哈希均与本报告包清单相同。本报告作者只在本地核对原始证据，未执行SSH或访问CAN。

前后11项比较全部相同，包括boot/config/current/services/进程/接收表/CANlink收发统计；只见原RT PID600及fd0/1/2。真实TUI、launcher、扫描、CAN打开/发送、SDO、进程控制均未执行，驱动实时状态仍未知；不构成真实运动修复验收。旧包及旧日志保留。冻结包README/summary中的板端pending是打包当时状态，不修改原包。

用户本人运行：

```sh
{final['user_command']}
```

原始命令/输出/rc：`状态样本修复-2026-10-19-证据/board-stage/{{before-passive,stage,pureload,launcher-syntax,after-passive}}.command.json`及同名前缀`.stdout/.stderr/.rc`；完整汇总及文件清单见该目录 `final-summary.json`、`postcheck-summary.json`、`evidence-manifest.json`。

'''
assert '## 本地原始命令' in text
text=text.replace('## 本地原始命令',section+'## 本地原始命令',1);REPORT.write_text(text)
proof={'board_final_summary_sha256':hashlib.sha256((BOARD/'final-summary.json').read_bytes()).hexdigest(),'board_postcheck_summary_sha256':hashlib.sha256((BOARD/'postcheck-summary.json').read_bytes()).hexdigest(),'board_archive_matches_local':True,'board_manifest_matches_local':True,'compared_keys':11,'local_evidence_read_only':True,'author_SSH_or_CAN':False}
(EV/'local-acceptance/board-report-update-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
print('board report updated; real TUI/motor acceptance remains pending')
