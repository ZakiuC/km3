#!/usr/bin/env python3
import datetime,hashlib,json,pathlib,shlex
from zoneinfo import ZoneInfo
E=pathlib.Path(__file__).resolve().parent;ROOT=pathlib.Path('/home/zakiu/km3')
f=json.loads((E/'implementation/source-freeze-input.json').read_text());info=json.loads((E/'package/summary.json').read_text());deploy=json.loads((E/'deployment/result.json').read_text())
for name,digest in f['source_hashes'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
records=[];snapshot=None
for folder in ['acceptance','package','deployment']:
 for path in sorted((E/folder).glob('*.json')):
  d=json.loads(path.read_text())
  if 'source_sha256_before' not in d:continue
  assert d['rc']==0 and d['source_sha256_before']==d['source_sha256_after'],str(path)
  if snapshot is None:snapshot=d['source_sha256_before']
  assert snapshot==d['source_sha256_before'],str(path)
  records.append((path,d))
for name,digest in snapshot.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
old=ROOT/'docs/硬件手册/PMP4010/抱闸手动测试-2026-10-19-证据/package/payload';new=E/'package/payload'
delta=[]
for p in new.rglob('*'):
 if p.is_file() and not p.is_symlink() and p.read_bytes()!=(old/p.relative_to(new)).read_bytes():delta.append(str(p.relative_to(new)))
assert set(delta)=={'drivers/pmp4010/pmp4010_simple.py','drivers/pmp4010/pmp4010.py','README-手动入口.md','payload-manifest.json'}
(E/'acceptance/source-and-package-validation.json').write_text(json.dumps(dict(time=datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),source_files_per_snapshot=len(snapshot),frozen_source_current=True,all_command_source_snapshots_identical=True,records=[str(p.relative_to(E)) for p,d in records],package_delta=delta,all_other_payload_bytes_identical=True),ensure_ascii=False,indent=2)+'\n')
s='''# 自实现手动测试线性速度 mm/s：验收与部署

Asia/Shanghai 2026-10-20。已把当前Python manual-linear的s输入、主屏实际/下一速度、审阅和帮助统一为mm/s；角度模式仍rpm。机械换算采用精确分数 mm/s×60÷每电机轴转mm，随后调用既有唯一C++ rpm→U/s函数，底层速度仍U/s。4mm/电机轴转仍可配置；修改c不自动改变设备U/s，旧请求清除、旧审阅失效。

默认物理速度保持1000U/s，未提高；默认/兼容参数、c清除旧请求后的mm/s显示按实读标定与配置换算，没有硬编码某一个mm/s默认值。--manual-velocity仍明确为U/s兼容参数。无需手填OD理由，连接/会话Enter确认和动作两段确认保持，其余限制与正式OD状态不变。

## 改动与本轮实测

生产源码仅pmp4010_simple.py与pmp4010.py（后者CLI中文帮助）；测试为_test_simple.py/_test_simple_pty.py。实现worker保留[4份diff、源码before与原始测试记录](implementation/README.md)，冻结证据为[source-freeze-input.json](implementation/source-freeze-input.json)。本打包/部署worker未编辑业务源码。

| 检查 | 原基线 | 本轮实测 |
| --- | --- | --- |
| 全PMP Python | 457 | 461，rc0，45.917s |
| simple定向 | 51 | 55，rc0 |
| manual定向 | 63 | 63，rc0 |
| PTY套件 | 3（before源码） | 3，最终完整套件重新运行，rc0 |
| 独立linear 80x24 PTY | 无 | 44检查，rc0，最终screens留存 |
| hwtui/tools Python | 217 / 8 | 217 / 8，rc0 |
| 四闸门+三生成器 | 历史各rc0 | 本轮七条分别执行，各rc0 |
| payload与roundtrip宿主load_smoke | 历史通过 | 各20imports、33native/33Python/14int32、I/O0，rc0 |
| 板端load_smoke | 历史通过 | 同上，原包ARM库ABI1，rc0 |
| 板端sh-n/直接help | 历史通过 | 各rc0 |

PMP Python、定向和PTY由实现worker执行，本worker执行hwtui/tools、七闸门、打包和加载部署。simple初次55测试有3 errors、rc1：新增1mm/s用例超出fixture607F10000，随后只提高针对性mock限值，生产上限检查未改；保留[simple-initial.log](implementation/simple-initial.log)。最终显示收尾后simple与linear PTY、完整461均重新验证。早期simple-pty单跑在末次显示改动前，最终完整461覆盖了全部3个PTY测试，不冒充早期记录为最终结果。

依赖闸门rc0仍报告Python3旧映射缺项。原正式C++与示例C++未改、未重建；其历史套件/ASan/TSan不算本轮运行。本轮没有任何真实CAN、固件、负载、抱闸、复位、使能或运动验证。

## 包来源与部署

以原可信e137c603包为基线，旧清单8cf6fa0...逐项校验后复制。新包只改变两份生产Python、包内README与payloadmanifest；其余hwtui、OD、ARM库、两启动器和旧功能均字节保留。本轮原生库没有重建。归档28成员（26普通文件、2符号链接）校验路径、类型、链接目标与tar roundtrip字节，两个禁止I/O宿主smoke通过。每条验收/打包/部署命令前后源码snapshot一致且等于最终源码；核对原始数据：[source-and-package-validation.json](acceptance/source-and-package-validation.json)。

'''
s+=f'归档：`{info["archive"]}`。\n\n归档SHA256：`{info["archive_sha256"]}`。\n\n新payloadmanifest SHA256：`{info["manifest_sha256"]}`。\n\n'
s+='''ARM库SHA256：`59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404`。launcher SHA256：`56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1`，均原值。新打包脚本[build_package.py](package/build_package.py)采取排他创建保护旧证据。

'''
s+=f'实际板端：root@192.168.0.188，RK3588-Tronlong/aarch64/Python3.10.12，目录 `{deploy["target"]}`。严格沿用SSH已登记主机密钥和BatchMode；上传archive SHA、manifest SHA、27项清单及ARM库hash均在板端核对。audit load_smoke禁止socket/子进程等I/O，实际attempts=0；launcher只sh-n，帮助直接运行pmp4010.py --help（launcher --help会创建日志，因此未运行）。\n\n'
s+='''两个受管稳定链接pmp4010-manual-test与pmp4010-cpp-test均经确认从原Python e137c603目录原子切到新版本，解析后launcher hash相同。推荐命令保持：

```sh
ssh -t root@192.168.0.188 '/opt/km3/hwtools/pmp4010-manual-test/run-manual.sh'
```

进入后s输入mm/s，例如输入1表示1mm/s，仍须Enter审阅、再Enter执行；速度/标定/限位保护可能拒绝超限值。用户需退出旧界面后重新进入；本轮没有停止已有进程或服务。临时上传archive与专属目录已清除，临时链接无残留；正式新旧版本和证据均保留。未改网络/CAN配置/current，未触碰harness/架构或commit/push，也未回滚他人dirty edits。

## 实现worker测试原始记录

'''
for item in f['local_acceptance']['evidence']:
 rel=str(pathlib.Path(item['raw_output']).relative_to(E.relative_to(ROOT)))
 s+='```sh\n'+item['command']+'\n```\n\n'+f'rc={item["rc"]}，[原始输出]({rel})。\n\n'
for path,d in records:
 rel=str(path.relative_to(E));stem=path.stem;folder=path.parent.name
 s+=f'## {rel} — rc={d["rc"]}\n\n[argv/时间/rc/hash]({rel}) · [stdout]({folder}/{stem}.stdout) · [stderr]({folder}/{stem}.stderr)\n\n'
 if len(shlex.join(d['argv']))<1600:s+='```sh\n'+shlex.join(d['argv'])+'\n```\n\n'
 else:s+=f'完整远端Python原文：[{stem}.remote.py]({folder}/{stem}.remote.py)。\n\n'
 # Retain the complete raw outputs in linked files; include concise terminal outputs inline.
 stdout=(path.parent/(stem+'.stdout')).read_text();stderr=(path.parent/(stem+'.stderr')).read_text()
 if len(stdout)<3200:s+='stdout:\n\n```text\n'+(stdout or '(empty)\n')+'```\n\n'
 else:s+='stdout完整原文见以上文件（未截断保存）。\n\n'
 s+='stderr:\n\n```text\n'+(stderr or '(empty)\n')+'```\n\n'
s+='''## 最后文档闸门

本报告新增后只补跑bash tools/check-docs.sh，原始[argv/rc](acceptance/final-docs-corrected.json)、[stdout](acceptance/final-docs-corrected.stdout)、[stderr](acceptance/final-docs-corrected.stderr)另存。不重复其他套件。
'''
(E/'验收与部署报告.md').write_text(s)
