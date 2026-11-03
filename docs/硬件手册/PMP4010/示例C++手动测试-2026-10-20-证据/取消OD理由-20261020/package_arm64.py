#!/usr/bin/env python3
"""Package only the validated no-reason standalone tool; never modify prior evidence."""
import hashlib, json, pathlib, shutil, tarfile
E=pathlib.Path(__file__).resolve().parent
ROOT=pathlib.Path('/home/zakiu/km3')
BUILD=ROOT/'build-pmp4010-cpp-no-od-arm64'
record=json.loads((E/'acceptance/arm64-build.json').read_text())
assert record['returncode']==0
manifest=record['source_sha256_after']
assert record['source_sha256_before']==manifest
for name,digest in manifest.items(): assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
for label in ['asan-ctest','tsan-ctest','asan-pty','tsan-pty','arm64-elf']:
    check=json.loads((E/('acceptance/'+label+'.json')).read_text())
    assert check['returncode']==0 and check['source_sha256_before']==manifest and check['source_sha256_after']==manifest,label
P=E/'package'
P.mkdir(exist_ok=False)
shutil.copy2(BUILD/'pmp4010_manual_test',P/'pmp4010_manual_test')
shutil.copy2(ROOT/'tools/pmp4010_cpp_test/README.md',P/'README.md')
shutil.copy2(E/'acceptance/arm64-elf.stdout.log',P/'ELF_DEPENDENCIES.txt')
(P/'SOURCE_SHA256.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
(P/'run-manual.sh').write_text('''#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/pmp4010_manual_test" --interface can1 --node 1 --mm-per-rev 4 "$@"
''')
(P/'run-manual.sh').chmod(0o755)
(P/'使用说明.md').write_text('''# 独立 C++ 手动直线测试：取消 OD 理由输入

本包不要求 OD 未核对项理由，CLI 不再接受 --od-reason。正式 OD 状态与其余保护不变。
真机仍须按会话 Enter 确认 RT 和其他 CAN 控制程序已停止，或显式 --exclusive-can-ack。
参数输入后的 Enter 审阅、再 Enter 执行保留；Space/x停止、d失能、q退出。
启动入口预填 can1、node1、4mm/电机轴转；默认1rpm、1Mbps。包不配置 CAN，不自动复位或使能。
本手动线性工具无主机1°硬顶；所有其他设备与通信保护保持。

板端可手动执行：

```sh
sha256sum -c SHA256SUMS
./pmp4010_manual_test --version
./run-manual.sh --help
./run-manual.sh
```

不接真实 CAN 的模拟入口：`./run-manual.sh --mock`。
Linux AArch64，libstdc++/libgcc静态链接；libm、libc及ld-linux仍动态链接，版本详见 ELF_DEPENDENCIES.txt。
本地模拟、sanitizer、交叉构建和板端帮助加载不等于真实 CAN、固件、负载、抱闸或运动验收。
实际部署状态与现场启动短路径以同轮部署报告为准。
''')
(P/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(P.iterdir()) if p.is_file()))
archive=BUILD/'pmp4010-cpp-no-od-manual-arm64.tar.gz'
assert not archive.exists()
with tarfile.open(archive,'w:gz') as tar: tar.add(P,arcname='pmp4010-cpp-no-od-manual-arm64')
result=dict(archive=str(archive),archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),binary_sha256=hashlib.sha256((P/'pmp4010_manual_test').read_bytes()).hexdigest(),package=str(P),source_entries=len(manifest))
(E/'package-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
