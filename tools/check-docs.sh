#!/usr/bin/env bash
# 校验架构文档与仓库的一致性。CI 的 docs job 会调用本脚本。
#
# 检查三项：
#   1. 文档里的 `xxx.md` 引用是否可解析（依次按文档同级 -> docs/ -> 仓库根查找）
#   2. 设备端文档 §4.1 的目录树中，每个具名文件是否真实存在
#   3. 文档里形如 `tools/foo.sh` 的**仓库内路径引用**是否存在
#   4. 禁止 "CAN" 加连字符再加数字的写法（会被读成主板 CAN 端口号）
#   5. tools/README.md 必须列出 tools/ 下每个脚本
#   6. systemd 单元语法必须通过 systemd-analyze verify（见下，需区分两类报错）
#
# 第 3 项的由来：只有第 1 项时，文档里写了一个不存在的 tools/deploy_web.sh，
# 检查器看不见——它只匹配反引号里的 .md。这类"引用了没写的脚本"
# 与悬空的 md 链接是同一类缺陷，因此一并检查。
#
# 方向说明：第 2 项是**单向**的（文档提到 -> 磁盘必须有）。
# 不检查反向（磁盘有 -> 文档必须提到），因为那会迫使文档追着代码走。
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
import io, os, re, shutil, subprocess, sys

def expand(tok):
    """展开 {a,b} 形式。用于目录树里的简写。"""
    i = tok.find("{")
    if i < 0:
        return [tok]
    depth, j = 0, i
    while j < len(tok):
        if tok[j] == "{":
            depth += 1
        elif tok[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    if j >= len(tok):
        return [tok]
    head, body, tail = tok[:i], tok[i + 1:j], tok[j + 1:]
    out = []
    for alt in body.split(","):
        for rest in expand(tail):
            out.extend(expand(head + alt.strip() + rest))
    return out

fail = 0

# ---------- 1. markdown 引用 ----------
refs_checked = 0
for fn in sorted(os.listdir("docs/架构设计")):
    if not fn.endswith(".md"):
        continue
    path = os.path.join("docs/架构设计", fn)
    text = io.open(path, encoding="utf-8").read()
    for ref in sorted(set(re.findall(r"`([^`]*\.md)`", text))):
        if ref.startswith("http"):
            continue
        refs_checked += 1
        if os.path.isfile(os.path.join("docs/架构设计", ref)) \
           or os.path.isfile(os.path.join("docs", ref)) \
           or os.path.isfile(ref):
            continue
        print(f"  悬空引用: {path} -> {ref}")
        fail = 1
print(f"  markdown 引用：检查 {refs_checked} 处")

# ---------- 3. 仓库内路径引用 ----------
# 只覆盖**顶层目录**开头的路径，且不含通配与占位符。
# 刻意不做全量路径校验：文档里的 `/opt/km3/...`、`/api/v1`、`app/build.gradle.kts`
# 之类不是仓库相对路径，硬查会产生大量假报，反而让人不再信任检查器。
TOPDIRS = ("common", "device", "server", "android", "admin-web",
           "tools", "docs", ".github")
EXTS = (".sh", ".py", ".sql", ".cpp", ".hpp", ".kt", ".kts", ".ts", ".vue",
        ".proto", ".cmake", ".yml", ".json", ".txt")

all_repo_files = set()
for line in os.popen(
    "find . -type f -not -path './.git/*' -not -path './build-*' "
    "-not -path './harness/*' -not -path '*/node_modules/*' "
    "-not -path '*/dist/*'"
).read().split("\n"):
    if line:
        all_repo_files.add(line[2:] if line.startswith("./") else line)

scan_files = [os.path.join("docs/架构设计", f)
              for f in sorted(os.listdir("docs/架构设计")) if f.endswith(".md")]
scan_files += [f for f in ("README.md", "CLAUDE.md") if os.path.isfile(f)]

path_refs = 0
for path in scan_files:
    text = io.open(path, encoding="utf-8").read()
    for ref in sorted(set(re.findall(r"`([^`]*)`", text))):
        if "/" not in ref or ref.startswith(("http", "/", "..")):
            continue
        if any(ch in ref for ch in "*{}<>$| "):
            continue
        if not ref.endswith(EXTS):
            continue
        if ref.split("/")[0] not in TOPDIRS:
            continue
        path_refs += 1
        if ref in all_repo_files:
            continue
        if os.path.join(os.path.dirname(path), ref) in all_repo_files:
            continue
        print(f"  悬空引用（文件）: {path} -> {ref}")
        fail = 1
print(f"  仓库内路径引用：检查 {path_refs} 处")

# ---------- 4. CAN 端口写法歧义 ----------
#
# 背景：硬件设备清单.xlsx 定义 "CAN-x 指主板 CAN 的第 x 个口"，因此
# CAN-1 -> can0、CAN-2 -> can1，逻辑名与内核接口名**差一位**。
# 而协议名 "CAN 2.0B" 若写成带连字符的形式，形如 CAN-数字.数字，
# 会被读成端口号 —— 差一位的结果是驱动对错总线，现场只表现为"电机不动"。
# 同理 USB 转 CAN 模块的接口名恰好是 can2，它不是主板端口 CAN-2。
#
# 本检查只针对**写法**（形如 CAN 加连字符加数字加句点加数字），
# 不试图判断语义；语义一致性靠文档里的映射表与代码里的逻辑名约定。
CAN_AMBIGUOUS = re.compile(r"CAN-[0-9]+\.[0-9]")
can_files = [os.path.join("docs/架构设计", f)
             for f in sorted(os.listdir("docs/架构设计")) if f.endswith(".md")]
can_files += [f for f in ("README.md", "CLAUDE.md") if os.path.isfile(f)]
can_files += [os.path.join("device", f) for f in ("README.md",) if os.path.isfile(os.path.join("device", f))]

can_hits = 0
for path in can_files:
    for i, line in enumerate(io.open(path, encoding="utf-8").read().split("\n"), 1):
        if CAN_AMBIGUOUS.search(line):
            print(f"  歧义写法（会被读成 CAN 端口号）: {path}:{i}: {line.strip()[:70]}")
            can_hits += 1
if can_hits:
    print("  正确写法：协议名用 'CAN 2.0B'（空格，无连字符）")
    fail = 1
print(f"  CAN 写法歧义：检查 {len(can_files)} 个文件，命中 {can_hits} 处")

# ---------- 5. tools/README.md 必须列出 tools/ 下的每个脚本 ----------
# 由来：README 是手维护的表，实测漏列了三个脚本（check-deps / check-docs /
# provision_device），而它是"有哪些工具"的唯一索引。手维护的表迟早漏，
# 所以用机检兜住；方向是**单向**的（磁盘有 -> 表里必须有），
# 反向不检查（表里可以提到将来计划中的脚本）。
tools_disk = sorted(f for f in os.listdir("tools")
                    if f.endswith((".sh", ".py")))
readme_path = "tools/README.md"
if os.path.isfile(readme_path):
    rtext = io.open(readme_path, encoding="utf-8").read()
    listed = set(re.findall(r"`([a-z0-9_-]+\.(?:sh|py))`", rtext))
    unlisted = [f for f in tools_disk if f not in listed]
    for f in unlisted:
        print(f"  tools/README.md 未列出: tools/{f}")
    if unlisted:
        fail = 1
    print(f"  tools 索引：磁盘 {len(tools_disk)} 个脚本，表内 {len(listed)} 个，未列 {len(unlisted)} 个")
else:
    print("  !! tools/README.md 不存在")

# ---------- 6. systemd 单元语法 ----------
#
# 由来：km3-can.service 早期把 `Environment=CAN_IFACES=can0 can1` 写成不加引号的形式，
# systemd 只接受第一个值并打印 "Invalid environment assignment, ignoring: can1" ——
# 第二个 CAN 接口静默消失，而单元文件看起来完全正常。
# 同类缺陷（键放错节、缺 ExecStart、变量优先级颠倒）都只能由 systemd-analyze 发现，
# 因此把它纳入机检。
#
# **必须区分的两类输出**：
#   (a) 真正的问题："Unknown key name", "Invalid environment assignment",
#       "Service has no ExecStart=", 语法错误
#   (b) 预期噪音："Command /opt/km3/current/... is not executable" ——
#       开发机上没有部署，ExecStart 自然找不到文件。这不是缺陷。
# 只把 (a) 计为失败；若把 (b) 也算进去，检查器会在每台开发机上恒红而被忽略。
#
# 本机校验对板子是否有效：实测本机 systemd 为 249.11-0ubuntu3.22，
# 测试板为 249.11-0ubuntu3.21 —— 同为 systemd 249，仅补丁号差一位，
# 因此本机 verify 的结论可外推到板子。**若将来两者主版本号拉开，
# 这个前提就失效了**（例如 249 不识别的键在 255 上合法，或反之），
# 届时需要改为在板子上校验，或固定比对版本。
UNIT_DIR = "device/deploy/systemd"
if os.path.isdir(UNIT_DIR) and shutil.which("systemd-analyze"):
    unit_files = sorted(f for f in os.listdir(UNIT_DIR) if f.endswith(".service"))
    unit_bad = []
    for fn in unit_files:
        path = os.path.join(UNIT_DIR, fn)
        r = subprocess.run(["systemd-analyze", "verify", path],
                           capture_output=True, text=True)
        for line in (r.stdout + r.stderr).split("\n"):
            if not line.strip():
                continue
            low = line.lower()
            if "is not executable" in low or "no such file or directory" in low:
                continue  # 预期噪音：本机未部署
            if fn not in line and UNIT_DIR not in line:
                continue  # 与本次校验的单元无关（依赖链上的系统单元）
            unit_bad.append(line.strip())
    for line in unit_bad:
        print(f"  systemd 单元问题: {line}")
    if unit_bad:
        fail = 1
    print(f"  systemd 单元：检查 {len(unit_files)} 个，问题 {len(unit_bad)} 处")
elif not shutil.which("systemd-analyze"):
    print("  systemd 单元：跳过（无 systemd-analyze）")
else:
    print(f"  systemd 单元：跳过（无 {UNIT_DIR}）")

# ---------- 2. 目录树（单向） ----------
tree_doc = "docs/架构设计/设备端程序架构.md"
text = io.open(tree_doc, encoding="utf-8").read()
try:
    start = text.index("```\nkm3/")
    end = text.index("```", start + 5)
except ValueError:
    print("  !! 未在设备端文档中找到 km3/ 目录树")
    sys.exit(1)
tree = text[start:end]

files = os.popen(
    "find . -type f -not -path './.git/*' -not -path './build-host/*' "
    "-not -path './build-server/*' -not -path './build-arm64/*' "
    "-not -path './build-tsan/*' -not -path './harness/*'"
).read()
bases = {os.path.basename(l) for l in files.split("\n") if l}
dirs = os.popen(
    "find . -type d -not -path './.git*' -not -path './build-host*' "
    "-not -path './build-server*' -not -path './build-arm64*' "
    "-not -path './build-tsan*' -not -path './harness*'"
).read()
dirset = {l.rstrip("/") for l in dirs.split("\n") if l and l != "."}

named, missing = 0, []
for raw in tree.split("\n"):
    if raw.strip().startswith("```"):
        continue
    line = raw.split("#")[0].translate(str.maketrans("", "", "│├└─"))
    for seg in re.split(r"\s+", line):
        seg = seg.strip().strip(",")
        if not seg or seg == "km3/" or seg.startswith("```"):
            continue
        for c in expand(seg):
            c = c.strip().strip(",")
            if not c or c == "..." or c.startswith("```"):
                continue
            named += 1
            if c.endswith("/"):
                d = c.rstrip("/")
                if not any(x == d or x.endswith("/" + d) for x in dirset):
                    missing.append(c)
            elif "." in os.path.basename(c):
                if os.path.basename(c) not in bases:
                    missing.append(c)
            else:
                if not any(x == c or x.endswith("/" + c) for x in dirset):
                    missing.append(c)

print(f"  目录树：检查 {named} 项（单向：文档提到 -> 磁盘存在）")
if missing:
    for m in sorted(set(missing)):
        print(f"  树中声称但不存在: {m}")
    fail = 1

if fail:
    print("结果：不一致")
    sys.exit(1)
print("结果：文档与仓库一致")
PY
