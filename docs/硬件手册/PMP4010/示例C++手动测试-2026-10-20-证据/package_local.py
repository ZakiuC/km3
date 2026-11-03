#!/usr/bin/env python3
"""Create a local ARM64 deliverable, never copy it to another machine."""
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

evidence = Path(__file__).resolve().parent
root = evidence.parents[3]
build = root / "build-pmp4010-cpp-arm64"
record = json.loads((evidence / "final-arm64-static-build.json").read_text())
if record["returncode"] != 0:
    raise SystemExit("ARM64 build did not succeed")
hashes = record["source_sha256_after"]
for name, digest in record["source_sha256_before"].items():
    if hashes.get(name) != digest:
        raise SystemExit("Source changed during ARM64 build: " + name)
for name, digest in hashes.items():
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
        raise SystemExit("Source changed after ARM64 build: " + name)
package = evidence / "package"
package.mkdir(exist_ok=True)
shutil.copy2(build / "pmp4010_manual_test", package / "pmp4010_manual_test")
shutil.copy2(root / "tools/pmp4010_cpp_test/README.md", package / "README.md")
shutil.copy2(evidence / "final-arm64-static-elf.stdout.log", package / "ELF_DEPENDENCIES.txt")
(package / "SOURCE_SHA256.json").write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + "\n")
(package / "使用说明.md").write_text("""# ARM64 本地交付包

本包只在开发机生成，未部署。程序为 Linux AArch64、非 sanitizer 构建。
libstdc++ 和 libgcc 已静态链接；ELF 无 libstdc++.so.6/libgcc_s.so.1 动态依赖。
目标机必须提供最高 GLIBC_2.34 及对应较早符号，
以及 libm.so.6、libc.so.6 和 ld-linux-aarch64.so.1。
完整依赖和符号版本见 ELF_DEPENDENCIES.txt；未访问目标机确认运行库，
因此不能仅凭交叉编译认定可直接运行。

人工审核后，在目标目录校验并查看帮助：

```sh
sha256sum -c SHA256SUMS
./pmp4010_manual_test --version
./pmp4010_manual_test --help
```

不接 CAN 的离线交互入口：

```sh
./pmp4010_manual_test --mock --od-reason '人工模拟检查'
```

真机必须显式选择已有 --interface，并人工确认 RT 与其他 CAN 控制程序已停止，
再提供 --exclusive-can-ack。默认 --bitrate 1000000、--rpm 1，
--sdo-timeout-ms 取值 1..500。工具只检查链路，不修改接口或位速率。
这是 PP 位置测试，rpm 是轨迹速度。--mm-per-rev 必须由现场人员确认。
操作需要非空 OD 理由与两次 Enter；7项非 verified 详见 README。
启动仅读，Space/x 停止，d 失能，q 退出。失能不证明抱闸与机械停稳。

README 中仓库构建命令供开发机使用，本包直接运行同目录二进制。
本地 mock/PTY/交叉编译均不代替硬件、固件、负载、抱闸或现场安全验收。

最终 ARM64 交付构建命令（仓库根目录）：

```sh
cmake -S tools/pmp4010_cpp_test -B build-pmp4010-cpp-arm64 -DCMAKE_BUILD_TYPE=Release -DPMP4010_SANITIZER=none -DCMAKE_TOOLCHAIN_FILE=/home/zakiu/km3/device/cmake/aarch64-linux-gnu.cmake '-DCMAKE_EXE_LINKER_FLAGS=-static-libstdc++ -static-libgcc'
cmake --build build-pmp4010-cpp-arm64 -j4
aarch64-linux-gnu-readelf -h -d -V build-pmp4010-cpp-arm64/pmp4010_manual_test
```
""")
lines = []
for path in sorted(package.iterdir()):
    if path.is_file() and path.name != "SHA256SUMS":
        lines.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name)
(package / "SHA256SUMS").write_text("\n".join(lines) + "\n")
archive = build / "pmp4010-cpp-manual-arm64.tar.gz"
with tarfile.open(archive, "w:gz") as output:
    output.add(package, arcname="pmp4010-cpp-manual-arm64")
print(json.dumps({"package": str(package), "archive": str(archive),
                  "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}, indent=2))
