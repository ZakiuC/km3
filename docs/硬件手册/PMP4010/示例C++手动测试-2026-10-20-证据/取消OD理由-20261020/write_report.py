#!/usr/bin/env python3
import datetime, hashlib, json, pathlib, shlex
from zoneinfo import ZoneInfo
E=pathlib.Path(__file__).resolve().parent
ROOT=pathlib.Path('/home/zakiu/km3')
manifest=json.loads((E/'package/SOURCE_SHA256.json').read_text())
for name,digest in manifest.items(): assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
checks=[]
for folder in ['host','acceptance','deployment']:
    for path in sorted((E/folder).glob('*.json')):
        d=json.loads(path.read_text())
        if 'source_sha256_before' not in d: continue
        assert d['returncode']==0,path.name
        assert d['source_sha256_before']==manifest and d['source_sha256_after']==manifest,path.name
        checks.append(str(path.relative_to(E)))
frozen=json.loads((E/'host/frozen-sha256.json').read_text())
for name,digest in frozen.items(): assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
(E/'cross-source-validation.json').write_text(json.dumps(dict(time=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),source_entries=len(manifest),records_matching_frozen_source=checks,frozen_source_and_host_binary_match=True),ensure_ascii=False,indent=2)+'\n')
result=json.loads((E/'deployment/result.json').read_text())
report='''# 独立 C++ 手动测试取消 OD 理由：验收与部署

2026-10-20，Asia/Shanghai。本轮按用户要求删除独立手动工具的 OD 未核对项理由字段、输入页与 CLI --od-reason；默认参数即可审阅、执行、停止和定向 Warning2312 复位，不需文字理由。旧参数现在作为未知参数被拒绝。正式 OD 状态和其他设备、通信保护均不变。

真机未提供 --exclusive-can-ack 时仍停在会话确认页，按 Enter 确认 RT 与其他 CAN 控制程序已停止后才启动工作线程；该页不要求文字。参数输入后的 Enter 审阅、再 Enter 执行保留。默认1rpm、4mm/电机轴转、can1/node1；按既有例外无主机1°硬顶。

## 本轮结果与历史基线

| 范围 | 上轮结果 | 本轮结果 |
| --- | --- | --- |
| backend，host/ASan/TSan各自 | 53 cases / 1004 assertions | 53 cases / 1007 assertions / 0 failures |
| preflight，三配置各自 | 100 assertions | 100 assertions，全部fake |
| CTest，三配置各自 | 2/2 | 2/2，rc0 |
| host完整PTY，含strace | 110 assertions | 120 assertions，rc0 |
| ASan/TSan完整PTY --no-strace，各自 | 103 assertions | 111 assertions，rc0 |
| ARM64 static libstdc++/libgcc构建 | rc0 | rc0，独立新构建目录 |
| 四闸门/三生成器 | 各rc0 | 七条分别执行，各rc0 |
| package SHA | 5/5 | 6/6（新增包内启动脚本） |
| 板端version/help/wrapper-help | 各rc0 | 各rc0 |

host 构建/CTest/PTY由实现worker执行并留存host/，本部署worker执行ASan/TSan、ARM构建与余下检查；每条真实命令和原始stdout/stderr如下。TSan CTest及PTY均使用setarch -R。本轮无失败/重试；no-strace只跳过当前9项syscall审计断言，不跳交互和协议mock检查。host完整strace证明真实入口在独占确认前不创建socket、mock流程不创建真实CAN；sanitizer PTY本身不声称做过syscall审计。

上轮正式项目Python457/217/8及原项目host/ASan/TSan各17/17为历史基线，本轮没有重跑这些大套件。父线程提供的独立manifest复核：对照旧92项恰好7个预期工具文件变化，其余85项一致；父线程另已审阅确认只删除理由要求，真正的会话Enter确认前不启动worker、两段动作确认保留。本段明确引用父线程核验，不冒充本worker重跑其diff审阅。

本worker逐项验证本轮所有host/acceptance/deployment命令前后92项hash均等于最终包manifest，并核对实现worker7文件和host binary冻结清单。原始核对：[cross-source-validation.json](cross-source-validation.json)。没有修改正式driver、od.json、原示例、架构文档或harness，没有commit/push。依赖闸门rc0仍保留已有Python3映射警告，未改旧依赖表。

## 新包与板端入口

'''
report+=f'二进制 SHA256：`{result["binary_sha256"]}`。\n\n压缩包 SHA256：`{result["archive_sha256"]}`。\n\n'
report+='''压缩包：`build-pmp4010-cpp-no-od-arm64/pmp4010-cpp-no-od-manual-arm64.tar.gz`，使用本轮[package_arm64.py](package_arm64.py)生成，旧package、tar、验收与部署报告均保留。新包包含README、使用说明、ELF依赖、92项源码manifest、binary和run-manual.sh。AArch64，NEEDED仅libm.so.6/libc.so.6/ld-linux-aarch64.so.1，最高GLIBC_2.34；libstdc++/libgcc静态链接。

目标root@192.168.0.188，严格沿用已登记SSH主机密钥与BatchMode，主机RK3588-Tronlong，machine-id b4b158c71fc04ebeb83d3a79a528a1de，aarch64，glibc2.35。新版本目录独占创建，不覆盖既存版本；稳定短路径此前不存在，本轮原子设置为本工具新版本符号链接。

'''
report+=f'实际版本目录：`{result["target"]}`。\n\n稳定入口：`{result["stable_entry"]}`。\n\n'
report+='''现场启动（本次没有执行真机会话）：

```sh
ssh -t root@192.168.0.188 '/opt/km3/hwtools/pmp4010-cpp-test/run-manual.sh'
```

进入后按Enter确认独占CAN，无需输入OD理由；默认1rpm。输入动作参数后Enter审阅，再Enter执行；Space/x停止、d失能、q退出。新脚本预填 --interface can1 --node 1 --mm-per-rev 4，透传额外参数，不预填独占确认。

只完成本地mock/PTY、交叉构建和板端version/help加载；未打开真实CAN、扫描SDO、复位、使能、运动，未修改CAN配置或停止/重启服务、进程。旧目录和正在运行的旧程序保留。本轮临时archive与专属上传目录已删除，没有残留临时链接；新正式版本与稳定链接保留供用户测试。

## 逐命令原始证据

原始argv为对应JSON权威；每条保存实际rc、开始结束时间、92项源码前后hash和不截断stdout/stderr。部署remote.py原文同目录保存。下面直接收录原始stdout/stderr，不以摘要冒充命令输出。

'''
for name in checks:
    path=E/name;d=json.loads(path.read_text());stem=path.stem;folder=path.parent
    report+=f'### {name} — rc={d["returncode"]}\n\n'
    report+=f'[命令/rc/hash]({name}) · [stdout]({folder.name}/{stem}.stdout.log) · [stderr]({folder.name}/{stem}.stderr.log)\n\n'
    report+='```sh\n'+shlex.join(d['argv'])+'\n```\n\n'
    report+='stdout:\n\n```text\n'+((folder/(stem+'.stdout.log')).read_text() or '(empty)\n')+'```\n\nstderr:\n\n```text\n'+((folder/(stem+'.stderr.log')).read_text() or '(empty)\n')+'```\n\n'
report+='''## 最后文档闸门

本报告生成后仅补跑 bash tools/check-docs.sh，原始[命令/rc](acceptance/final-docs.json)、[stdout](acceptance/final-docs.stdout.log)、[stderr](acceptance/final-docs.stderr.log)单独保留；不重复其他套件。
'''
(E/'验收与部署报告.md').write_text(report)
