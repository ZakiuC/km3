#!/usr/bin/env python3
"""Write the report only from frozen source, package records and deployment results."""
import hashlib,json,pathlib,shlex
E=pathlib.Path(__file__).resolve().parent;ROOT=pathlib.Path('/home/zakiu/km3')
f=json.loads((E/'package/source-freeze-input.json').read_text());info=json.loads((E/'package/summary.json').read_text());deployment=json.loads((E/'deployment/result.json').read_text())
for name,digest in f['source_hashes'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
records=[]
for folder in ['acceptance','package','deployment']:
 for p in sorted((E/folder).glob('*.json')):
  d=json.loads(p.read_text())
  if 'source_sha256_before' not in d:continue
  assert d['source_sha256_before']==d['source_sha256_after'],str(p)
  # Retain every actual failure and retry rather than treating failures as absent.
  records.append((p,d))
old=ROOT/'docs/硬件手册/PMP4010/手动速度mm每秒-2026-10-20-证据/package/payload';new=E/'package/payload'
delta=[];added=[]
for p in new.rglob('*'):
 if not p.is_file() or p.is_symlink():continue
 rel=p.relative_to(new);base=old/rel
 if not base.exists():added.append(str(rel))
 elif p.read_bytes()!=base.read_bytes():delta.append(str(rel))
assert set(delta)=={'drivers/pmp4010/pmp4010_simple.py','hwtui/tui.py','load_smoke.py','README-手动入口.md','payload-manifest.json'},delta
assert added==['drivers/pmp4010/pmp4010_simple_view.py'],added
validation={'frozen_source_current':True,'production_files_replaced':info['production_files_replaced'],'production_files_added':info['production_files_added'],'package_delta':delta,'package_added':added,'other_payload_bytes_identical':True,'records':[str(p.relative_to(E)) for p,d in records]}
(E/'acceptance/source-and-package-validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2)+'\n')
s='''# 自实现手动界面重设计：验收与部署

这轮重做Python手动工具主屏、输入、审阅、中文帮助和诊断的布局与颜色。新增纯呈现模块pmp4010_simple_view.py，修改pmp4010_simple.py接入呈现，共享hwtui/tui.py只增加可选渲染入口；默认渲染的原逐字节断言保留。操作语义、速度单位、正式OD与C++换算库沿用上一版。

本轮范围是离线MockBus/PTY验收与已授权板端文件更新。板端只做清单、禁止socket/子进程I/O的加载、sh -n与直接Python --help；没有打开真实CAN、使能、复位、运动、停止既有进程/服务或更改网络/CAN配置。离线预览不代表现场硬件验收。

## 源码与验证记录

冻结文件与每项测试证据见[源冻结](package/source-freeze-input.json)。实现worker的完整PMP/hwtui结果按前后hash复核；本worker逐字面运行tools测试、四闸门与三生成器。每次命令的argv、开始结束、rc、源码前后hash与完整输出都在下文链接，失败与重试保留。未改的C++/示例C++没有重复重编译或冒充为本轮运行。

| 检查 | 旧基线 | 本轮实测 |
| --- | --- | --- |
| PMP Python完整 | 461 | 472，50.775s，rc0 |
| hwtui完整 | 217 | 218，0.349s，rc0 |
| tools | 8 | 8，rc0 |
| 原共享逐字节测试 | 61 | 原61函数AST保持，新增1默认hook字节测试 |
| 真实PTY，80×24/小屏NO_COLOR | 无本轮结果 | 各35检查、rc0；都含120×32/64×20/80×24 resize |
| 四闸门/三生成器 | 各rc0 | 本轮七条literal命令各rc0 |
| payload/roundtrip宿主加载 | 旧20 imports | 各21 imports、native33/Python33/int32 14、I/O0、rc0 |
| 板端ARM加载 | 旧20 imports | 21 imports、native33/Python33/int32 14、I/O0、ABI1、rc0 |
| 板端sh -n与direct --help | 旧rc0 | 各rc0 |

实现的早期失败、修正与重跑均在[实现说明](implementation/implementation-summary.md)与原始logs保留；最终采纳唯一freeze.json的passed/unchanged记录。依赖闸门rc0仍有Python3未在映射表提示。新增报告完成后只补跑docs闸门。

## 包与板端状态

以已部署可信76f8db43包为基线，先验证原archive/manifest及全部条目，再替换冻结的simple.py、tui.py并增加simple_view.py；包内README、load_smoke和manifest同步更新。load_smoke显式覆盖新模块。包与tar roundtrip分别运行宿主本地库的禁止I/O加载，板端加载随包ARM库。校验[源码和包差异](acceptance/source-and-package-validation.json)。

'''
s+=f'归档SHA256：`{info["archive_sha256"]}`。\n\nmanifest SHA256：`{info["manifest_sha256"]}`。\n\n归档成员：{info["archive_members"]}；清单项：{info["manifest_entries"]}；普通文件：{info["regular_files"]}；符号链接：{info["symlinks"]}。\n\n'
s+=f'ARM库SHA256：`{info["arm_library_sha256"]}`；launcher SHA256：`{info["launcher_sha256"]}`；两者与基包逐字节一致，OD也逐字节一致。\n\n'
s+=f'板端目录：`{deployment["target"]}`；目标root@192.168.0.188，已核对machine-id与aarch64。归档与清单SHA、所有payload条目、ARM库均在板端复核。\n\n'
s+='''稳定入口pmp4010-manual-test与兼容入口pmp4010-cpp-test，均从原mmps-76f8db43 Python目录替换到本轮Python目录。每个链接以os.replace原子替换；两个链接的更新不是单个事务。所有旧版本保留，专属上传临时目录/归档清理，现有进程不触碰。用户退出旧界面后重新进入：

```sh
ssh -t root@192.168.0.188 '/opt/km3/hwtools/pmp4010-manual-test/run-manual.sh'
```

launcher --help会先建日志，因此验收不运行它；使用pmp4010.py --help。未触碰harness/、架构文档、正式driver/OD语义或commit/push，未回滚其他worker修改。本轮实际CAN、固件、标定、限位现场效果、负载、抱闸、使能/复位/运动均未现场验证。

## MockBus实际Grid预览

'''
for p in sorted((E/'acceptance').glob('previews-frozen-mono/*.png')):s+=f'- [{p.stem}]({p.relative_to(E)})（离线MockBus实际Grid）\n'
s+='\n## 实现worker原始证据\n\n'
for item in f['results']:
 log=pathlib.Path(item['log']).relative_to(E.relative_to(ROOT))
 s+='```sh\n'+shlex.join(item['command'])+'\n```\n\n'+f'rc={item["rc"]}，[完整原始输出]({log})。\n\n'
for p,d in records:
 rel=p.relative_to(E);stem=p.stem;folder=p.parent.name
 s+=f'## {rel} — rc={d["rc"]}\n\n[argv/时间/rc/源码hash]({rel}) · [stdout]({folder}/{stem}.stdout) · [stderr]({folder}/{stem}.stderr)\n\n'
 command=shlex.join(d['argv'])
 if len(command)<1800:s+='```sh\n'+command+'\n```\n\n'
 elif (p.parent/(stem+'.remote.py')).exists():s+=f'完整远端脚本：[{stem}.remote.py]({folder}/{stem}.remote.py)。\n\n'
 for suffix in ['stdout','stderr']:
  text=(p.parent/(stem+'.'+suffix)).read_text()
  s+=suffix+':\n\n'
  if len(text)<=4000:s+='```text\n'+(text or '(empty)\n')+'```\n\n'
  else:s+='完整原始输出见以上文件，未截断保存。\n\n'
s+='''## 最后docs闸门

新增本报告后单跑bash tools/check-docs.sh；原始argv、rc、stdout与stderr保存在acceptance/final-docs对应文件。完整运行记录不覆盖先前失败。
'''
(E/'验收与部署报告.md').write_text(s)
print('Report written; final docs gate must run next.')
