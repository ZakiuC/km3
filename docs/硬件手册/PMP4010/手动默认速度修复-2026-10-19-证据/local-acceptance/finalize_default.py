from pathlib import Path
import hashlib,json,shlex
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010/手动默认速度修复-2026-10-19-证据';OUT=BASE/'local-acceptance'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
frozen=json.loads((BASE/'implementation/final-source-sha256.json').read_text());native=json.loads((ROOT/'docs/硬件手册/PMP4010/正常驱动验收-2026-10-19-证据/native-current/source-after-sha256.json').read_text())['files'];before=json.loads((OUT/'freeze-source-sha256.json').read_text())['files']
check={'approved_file_count':len(frozen),'approved_seven_match':len(frozen)==7 and all(sha(ROOT/p)==h for p,h in frozen.items()),'native_77_match':len(native)==77 and all(sha(ROOT/p)==h for p,h in native.items()),'all_acceptance_files_match':all(sha(ROOT/p)==h for p,h in before.items())};assert check['approved_seven_match'] and check['native_77_match'] and check['all_acceptance_files_match']
(OUT/'freeze-after-package.json').write_text(json.dumps(check,indent=2)+'\n')
(OUT/'implementation-evidence-provenance.json').write_text(json.dumps([{'path':str(p.relative_to(ROOT)),'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted((BASE/'implementation').rglob('*')) if p.is_file()],ensure_ascii=False,indent=2)+'\n')
pkg=json.loads((BASE/'package/summary.json').read_text());rows=json.loads((OUT/'command-index.json').read_text())
p=ROOT/'docs/硬件手册/PMP4010/正常驱动验收-2026-10-19.md';s=p.read_text();n=pkg['python_test_counts']['model']
s=s.replace('当前最新完整本地验收是386/217/8、四门/三个生成器、public MockBus PTY34','当前最新完整本地验收是'+str(n)+'/217/8、四门/三个生成器、public MockBus PTY34').replace('新默认速度版本将独立冻结、复验和打包，不自动部署。','新默认速度版本已独立冻结、复验并打包；部署须由主线另行授权。')
s+='''\n## 手动默认速度最终版本：本地验收\n\n手动入口默认1000 U/s，显式 `--manual-velocity 100` 仍保留；pmp4010.py中文help与manual默认/fallback/V文案同步。0.1°步长、所有上限和守卫未变；此前测试若依赖100则显式设置100。默认1000实际6081写入，当前标定折算0.457763671875 rpm；显式100为0.0457763671875 rpm。\n\n'''+f'三套Python **{n}/217/8**均rc0，public独立PTY34观察点rc0；四门/三个生成器逐项rc0，未重跑native。最终七文件、native77项及本地验收全部冻结哈希在打包后仍一致。原完整argv/cwd/rc/stdout/stderr见[命令索引](手动默认速度修复-2026-10-19-证据/local-acceptance/command-index.json)，所有实现方局部检查与失败文件均保留并哈希索引。\n\n'
s+='|项目|rc|argv|\n|---|---:|---|\n'
for r in rows:s+='|'+r['name']+'|'+str(r['returncode'])+'|`'+shlex.join(r['argv'])+'`|\n'
s+='\n各命令cwd均为 `/home/zakiu/km3`。七门原始输出：\n'
for r in rows:
 if r['name'] not in ['drivers','docs','deps','manual-citations','gen-od','gen-canopen','gen-access']:continue
 s+='\n### 默认速度版本 '+r['name']+'\n\n```text\nargv='+shlex.join(r['argv'])+'\ncwd='+r['cwd']+'\nrc='+str(r['returncode'])+'\nstdout:\n'+(OUT/r['stdout']).read_text().rstrip()+'\nstderr:\n'+(OUT/r['stderr']).read_text().rstrip()+'\n```\n'
s+='\n默认版本独立PTY原输出（34为观察点，不是新增34 unit）：\n\n```text\n'+(OUT/'independent-pty.stdout.log').read_text().rstrip()+'\n```\n'
s+=f"\n独立新包：[归档](手动默认速度修复-2026-10-19-证据/package/pmp4010-manual-board-payload.tar.gz)，{pkg['archive_bytes']} bytes，SHA256 `{pkg['archive_sha256']}`；manifest SHA256 `{pkg['manifest_sha256']}`。24成员=22普通文件+2symlink、23 manifest entries、18imports，ABI1/native33/Python33/int32 14，ARM仍59ab…6404。缺库/导入前篡改拒绝、sh-n/help/nonTTY/覆盖参数拒绝/roundtrip符合预期，禁止I/O0。此处为本地结果，板端最终投放/纯加载等待回填。\n"
p.write_text(s);print(json.dumps({'python':pkg['python_test_counts'],'freeze':check,'report':str(p)},ensure_ascii=False))
