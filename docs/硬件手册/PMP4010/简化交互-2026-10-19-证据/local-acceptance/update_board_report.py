from pathlib import Path
import hashlib,json
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010';EV=BASE/'简化交互-2026-10-19-证据';BOARD=EV/'board-stage';REPORT=BASE/'简化交互-2026-10-19.md'
final=json.loads((BOARD/'final-summary.json').read_text());post=json.loads((BOARD/'postcheck-summary.json').read_text());pkg=json.loads((EV/'package/summary.json').read_text())
assert final['archive_sha256']==pkg['archive_sha256'] and final['manifest_sha256']==pkg['manifest_sha256']
assert final['result']=='PASS_SIMPLE_PACKAGE_DEPLOY_AND_PURELOAD_ONLY' and final['package_import_count']==18
assert all(rc==0 for rc in final['command_rcs'].values()) and post['changed_keys']==[]
assert len(post['compared_keys'])==11 and final['CAN_stats_unchanged'] and not final['manual_launcher_executed']
assert final['CAN_socket_opens']==final['CAN_sends']==final['real_simple_or_manualApp_run_calls']==0
text=REPORT.read_text().replace('本地模拟、真实curses PTY及独立包校验已完成，真实电机使能/点动仍待用户本人执行','本地模拟、真实curses PTY、独立包校验及板端隔离投放/纯加载已完成，真实电机使能/点动仍待用户本人执行')
text=text.replace('当前本地包尚不代表板端加载或硬件验收；板端证据由独立获授权的执行方补充。','板端纯加载现已完成，真实TUI与硬件使能/点动尚待用户本人执行。包内README/summary的板端pending是冻结打包时状态，未修改冻结包。')
section=f'''## 板端隔离投放与纯加载

独立获授权执行方于 `{final['utc']}` 完成正式包单次上传、隔离解包、纯加载与shell语法检查；stage/pureload/sh-n及前后被动检查均rc0。本报告作者仅本地核对原始证据，未执行SSH或访问CAN。远端23项manifest逐项一致，24个归档成员；launcher为755，logs目录不存在。实际AArch64 ABI1、18模块、33 native/33 Python/14 Int32向量及missing-native拒绝均通过，禁止IO/写入/run尝试0。

身份/boot/config/current/services/进程/rcvlists/can_link等实际11项前后比较 `changed_keys=[]`，CAN收发统计不变；只见原RT进程600及fd0/1/2。未启动launcher、SimpleApp/ManualApp、扫描、SDO、控制字或运动，也未读取当前电机状态/位置/制动器。旧正式包保留，撤回eaeb候选未上传。

用户本人运行：

```sh
{final['manual_launch_command']}
```

原始命令/退出码与输出：`简化交互-2026-10-19-证据/board-stage/{{stage,pureload,launcher-syntax,before-passive,after-passive,stage-driver}}.command.json`及同名前缀`.stdout/.stderr/.rc`；结果与完整文件指纹见该目录 `final-summary.json`、`postcheck-summary.json`、`evidence-manifest.json`。

'''
assert '## 本地命令记录' in text
text=text.replace('## 本地命令记录',section+'## 本地命令记录',1)
REPORT.write_text(text)
proof={'board_final_summary_sha256':hashlib.sha256((BOARD/'final-summary.json').read_bytes()).hexdigest(),'board_postcheck_summary_sha256':hashlib.sha256((BOARD/'postcheck-summary.json').read_bytes()).hexdigest(),'board_archive_matches_local':True,'board_manifest_matches_local':True,'compared_keys':11,'local_read_only_evidence_review':True,'author_SSH_or_CAN':False}
(EV/'local-acceptance/board-report-update-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
print('board report updated from raw corroborated final summary; archive/manifest matched')
