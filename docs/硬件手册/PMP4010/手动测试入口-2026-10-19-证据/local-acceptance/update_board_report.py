from pathlib import Path
import hashlib,json
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010/手动测试入口-2026-10-19-证据';BOARD=BASE/'board-stage';LOCAL=BASE/'local-acceptance';REPORT=ROOT/'docs/硬件手册/PMP4010/手动测试入口-2026-10-19.md'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest=json.loads((BOARD/'evidence-manifest.json').read_text())
for item in manifest['files']:
 p=BOARD/item['path'];assert p.stat().st_size==item['size'] and sha(p)==item['sha256'],item['path']
summary=json.loads((BOARD/'final-summary.json').read_text());post=json.loads((BOARD/'postcheck-summary.json').read_text());s=REPORT.read_text();marker='## 命令、退出码与原始输出';raw=s[s.index(marker):];rawsha=hashlib.sha256(raw.encode()).hexdigest()
s=s.replace('# PMP4010 手动测试入口与本地验收（2026-10-19）','# PMP4010 手动测试入口交付：本地验收与板端纯加载完成（2026-10-19）',1)
s=s.replace('截至本报告本地验收完成时，仅完成本地主机mock、真实curses PTY、C++测试和打包验证。未SSH、未打开CAN、未使能/运动；板端纯加载及人工操作仍待完成。上一阶段硬件失败证据未覆盖，本报告不将其计作手动硬件通过。','手动入口实现、本地mock/真实curses PTY/C++验收、隔离投放及板端AArch64纯加载均已完成。板端阶段由另一个获授权agent执行SSH投放和加载；未运行真实手动TUI、扫描、CAN读写、使能或运动，当前电机状态也未读取。真实操作待用户本人完成。上一阶段硬件失败证据保留，本报告不将其计作手动运动验收通过。',1)
s=s.replace('手动入口实现与本地验收已完成；板端stage与pureload由另一个授权agent执行，结果待回传。真实使能、解除Halt与运动交给用户本人操作。','手动入口实现、本地验收与授权板端stage/pureload已完成；真实使能、解除Halt与运动交给用户本人操作。',1)
s=s.replace('解包至独立目录，不切current；先由板端验收方完成纯加载，再由用户本人运行：','已投放至独立目录并完成板端纯加载，未切current。用户本人可直接运行：',1)
s=s.replace('python3 -I -B /ABSOLUTE/PACKAGE/load_smoke.py --check-missing-library\nssh -t root@192.168.0.188 \'/ABSOLUTE/PACKAGE/run-manual.sh\'',summary['manual_launch_command'],1)
s=s.replace('最近路由输入估计低于70K（低于240K守卫），来自现有相关上下文与本次任务包估计；actual usage仍unknown，不冒称实测。','最近本报告worker路由输入估计低于80K（低于240K守卫），来自现有相关上下文与本次任务包估计；actual usage仍unknown，不冒称实测。板端worker自身路由估计独立见其final-summary，不将两者当作实测usage。',1)
lines=['## 板端隔离投放与纯加载结果','',f"完成时间UTC：`{summary['utc']}`。证据总览：`手动测试入口-2026-10-19-证据/board-stage/final-summary.json`；完整文件校验表：`board-stage/evidence-manifest.json`。本报告worker仅本地读取这些证据，未独立SSH或访问CAN。",'',f"实际payload：`{summary['payload']}`。隔离目录保留供用户使用；正式current未切换。归档fd973…、manifest c2e3…、manual d43c…与本地冻结内容匹配；远端manifest22项逐项匹配，归档23成员，launcher为755。未运行启动脚本，故logs目录不存在。",'', 'Stage、pureload、launcher语法检查均rc0。板端17个模块导入、33 native + 33 Python + 14 Int32向量通过，实际加载包内AArch64库、ABI1。禁止IO/write/run尝试0；真实ManualApp.run=0、CAN socket opens=0、CAN sends=0、scan=0、controlword=0。这里只证明软件加载、数学及help/语法兼容，不证明真实手动TUI或电机行为。','',f"被动前后对比实际列出{len(post['compared_keys'])}项，changed_keys=`[]`，boot/config/current/services/全部CAN receive lists等一致，CANstats也不变。对比字段为：`"+'`, `'.join(post['compared_keys'])+'`。只见RT进程PID600、fd0/1/2，无manual/TUI/runner残留。未读取当前电机状态、位置、制动器或物理停止情况。','','实际纯加载步骤由下方带审计钩子的远端Python包装运行`load_smoke.py --check-missing-library`；实际launcher检查为：','','```sh','sh -n '+summary['payload']+'/run-manual.sh','```','','包内README的“板端待执行”是冻结打包时状态；本节记载后续实际板端加载结果，冻结包未因报告状态更新而修改。','','### 板端原命令、退出码与关键原始输出','']
for name in ['stage','pureload','launcher-syntax']:
 command=json.loads((BOARD/(name+'.command.json')).read_text());out=(BOARD/(name+'.stdout')).read_text();err=(BOARD/(name+'.stderr')).read_text()
 assert command['rc']==int((BOARD/(name+'.rc')).read_text())==0
 lines+=['#### '+name,'',f"完整argv/时间/rc：`board-stage/{name}.command.json`；远端脚本：`board-stage/{name}.remote.py`；原始输出：`board-stage/{name}.stdout`、`board-stage/{name}.stderr`；rc文件：`board-stage/{name}.rc`。",'', '原始argv（JSON保留各参数边界）：','','```json',json.dumps(command['argv'],ensure_ascii=False,indent=2),'```','',f"rc=`{command['rc']}`；stderr="+('empty' if not err else err),'','关键原始stdout：','','```text']
 if name=='pureload':
  selected=[line for line in out.splitlines() if line.startswith('payload_manifest=') or line.startswith('import=pmp4010_manual ') or 'library_realpath' in line or line.startswith('missing_library=') or line.startswith('{"result":') or line.startswith('{"manual_import":')]
  lines+=selected
 else:lines.append(out.rstrip())
 lines+=['```','']
lines+=['被动前后检查摘要原文；原始快照与命令分别见`board-stage/before-passive.*`和`board-stage/after-passive.*`：','','```json',json.dumps(post,ensure_ascii=False,indent=2),'```','']
s=s.replace(marker,'\n'.join(lines)+'\n'+marker,1);assert s[s.index(marker):]==raw
REPORT.write_text(s)
proof={'board_evidence_manifest_entries_verified':len(manifest['files']),'board_evidence_manifest_sha256':sha(BOARD/'evidence-manifest.json'),'raw_section_sha256_before':rawsha,'raw_section_sha256_after':hashlib.sha256(s[s.index(marker):].encode()).hexdigest(),'raw_section_unchanged':True,'archive_sha256':sha(BASE/'package/pmp4010-manual-board-payload.tar.gz'),'postcheck_compared_keys_actual':len(post['compared_keys']),'board_paths_read_only':True,'runtime_model':'unknown','runtime_effort':'unknown','last_token_usage_input_tokens':'unknown','input_estimate':'below 80K from relevant context and current task packet; estimate only','requested_role':'light_worker','requested_model':'gpt-6.1-sol','requested_effort':'medium'}
(LOCAL/'board-report-update-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n');print(json.dumps(proof,ensure_ascii=False,indent=2))
