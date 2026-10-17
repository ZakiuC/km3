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
# 扫描面：docs/架构设计/*.md、docs/硬件手册/**/*.md（本目录下递归）、README.md、CLAUDE.md。
# 硬件手册目录是**递归**扫的：一个硬件一个目录，且协议原文在 <型号>/协议/ 下一层。
#
# 第 3 项的由来：只有第 1 项时，文档里写了一个不存在的 tools/deploy_web.sh，
# 检查器看不见——它只匹配反引号里的 .md。这类"引用了没写的脚本"
# 与悬空的 md 链接是同一类缺陷，因此一并检查。
#
# 第 5 项的覆盖范围：**只看 tools/ 直下一层**，不递归子目录。
# 子目录里的脚本（如 tools/hwdriver/new-driver.sh）不要求出现在 tools/README.md
# 的脚本索引里——它们的入口是各自子目录的说明，混进顶层索引会让
# "tools/ 下有哪些主机脚本"这件事变得不可读。若将来要覆盖子目录，
# 改的是 os.listdir 那一处，改完记得同步 tools/README.md。
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


def DocumentedMarkdown():
    """本次机检要看的全部 markdown，返回仓库相对路径列表。

    扫描面（三处，其余目录一概不看）：
      * docs/架构设计/*.md     —— 设计权威，平的
      * docs/硬件手册/**/*.md  —— **递归**，因为一个硬件一个目录，
                                   协议原文还压在 <型号>/协议/ 下一层
      * 仓库根的 README.md / CLAUDE.md

    为什么把扫描面收在函数里而不是各处 os.listdir：第 1、3、4 项各扫一遍，
    三处各写一份"扫哪些文件"必然漂移——过去第 4 项就漏了 device/README.md，
    直到有人单独补一行才对齐。收成一处后，加扫描面只改这里。
    """
    out = []
    d = "docs/架构设计"
    if os.path.isdir(d):
        out += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".md")]
    for root, dirs, files in os.walk("docs/硬件手册"):
        dirs.sort()
        out += [os.path.join(root, f) for f in sorted(files) if f.endswith(".md")]
    out += [f for f in ("README.md", "CLAUDE.md") if os.path.isfile(f)]
    return out


# 手册目录的相对前缀。第 1、3 项对它开**存在性豁免**，见下面两张表各自位置的说明。
MANUAL_DIR_PREFIX = "docs/硬件手册/"

# ---------- 1. markdown 引用 ----------
#
# 存在性豁免（只针对 docs/硬件手册/ 开头的引用）：
#   本目录的绝大多数文件是"尚未投喂"的占位——写成 docs/硬件手册/<型号>/协议/对象字典.md
#   这种引用是**正确且必要**的：它告诉人该把哪份厂家文件放哪，正是驱动占位值
#   将来被逐条替换的清单。手册没到，引用必然解析不到；
#   强制存在性会立刻把 CI 弄红，而红的原因不是缺陷，是"手册还没给"。
#   所以这里对 docs/硬件手册/ 开头的引用**跳过存在性检查**，但仍计入 refs_checked，
#   让"检查了 N 处"这个数字不因豁免而虚高。
#
#   别以为这是漏写的：下一个人若不看注释就"补上"这条检查，
#   CI 会在每次新增手册条目时变红，然后被当成噪音关掉——比不检查更坏。
#
#   豁免之外的引用照旧严格检查：手册目录里引用 docs/架构设计/*.md
#   这类**已存在**的文档，写错了仍然是缺陷。
#
# 豁免只关掉"存在性"这一项。第 3 项（仓库内路径引用）与第 4 项（CAN 写法歧义）
# 对 docs/硬件手册/ 下的文档**照常生效**。
refs_checked = 0
refs_exempt = 0
refs_placeholder = 0
for path in DocumentedMarkdown():
    text = io.open(path, encoding="utf-8").read()
    # 豁免的判据是**文档本身**在手册目录下，而不是引用串以 docs/硬件手册/ 开头。
    # 起初写成后者，结果豁免几乎不生效：手册目录里绝大多数引用是
    # `协议/对象字典.md`、`操作手册.md` 这种**相对**写法——正是那些未投喂文件的
    # 典型写法，一个都不以前缀开头。判据必须落在"谁在说"上。
    in_manual = path.startswith(MANUAL_DIR_PREFIX)
    for ref in sorted(set(re.findall(r"`([^`]*\.md)`", text))):
        if ref.startswith("http"):
            continue
        # 占位写法（含 <型号>、{a,b}、* 等）不是字面路径，硬查必假报。
        # 第 3 项早就有这条过滤，第 1 项却一直漏着，于是
        # `docs/硬件手册/<型号>/README.md` 这类**正确**的占位引用常年被判悬空，
        # 并被写进 docs/架构设计/硬件驱动开发路径.md 当作"检查器的已知假报"记着。
        # 已知假报是最坏的一种：它教人忽略这个检查器的输出。
        # 两份过滤口径从此一致，改一处必须改另一处。
        if any(ch in ref for ch in "*{}<>$| "):
            refs_checked += 1
            refs_placeholder += 1
            continue
        refs_checked += 1
        if in_manual:
            # 手册目录下的引用：是"该放哪份文件"的清单，不是对既有文件的承诺。
            refs_exempt += 1
            continue
        if os.path.isfile(os.path.join(os.path.dirname(path), ref)) \
           or os.path.isfile(os.path.join("docs/架构设计", ref)) \
           or os.path.isfile(os.path.join("docs", ref)) \
           or os.path.isfile(ref):
            continue
        print(f"  悬空引用: {path} -> {ref}")
        fail = 1
print(f"  markdown 引用：检查 {refs_checked} 处"
      f"（其中 {refs_placeholder} 处含占位符、{refs_exempt} 处来自 {MANUAL_DIR_PREFIX}"
      f" 下的文档，两者均豁免存在性）")

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
    "find . -path './harness' -prune -o -type f -not -path './.git/*' -not -path './build-*' "
    "-not -path './harness/*' -not -path '*/node_modules/*' "
    "-not -path '*/dist/*' -print"
).read().split("\n"):
    if line:
        all_repo_files.add(line[2:] if line.startswith("./") else line)

scan_files = DocumentedMarkdown()

# docs/硬件手册/ 下的**相对**引用（如 `协议/对象字典.md`）：同样豁免存在性。
# 理由与第 1 项相同——那些文件按约定是要投喂的，没到不等于写错。
# 注意这里只豁免"解析不到"的情形：解析得到（相对路径确实存在）就照常通过，
# 所以豁免不会把一条**写错**的引用洗成通过……除非它指向的正是未投喂的清单项，
# 而那本来就不是错误。
# 文档里明确写为**将来才引入**的仓库内路径。
#
# 为什么需要这张表：本项的语义是"文档提到 -> 磁盘必须有"，用来抓路径笔误。
# 但有些路径是文档**故意**写成尚未存在的——它描述的是条件触发后才引入的文件，
# 而那个条件此刻还没满足。把它判成悬空引用，等于逼文档删掉这条前瞻性约定，
# 或者逼人提前建一个没人读的空文件（那就是第二份真相，本仓库反复在防的事）。
#
# 自净机制：若表里的文件**真的出现了**，说明它已经落地，本表条目就成了
# 一处永久的豁免漏洞（"写错路径"与"还没建"从此分不清），因此当场判失败。
# 换句话说，这张表只会随时间变短，不会悄悄变长。
FUTURE_PATHS = {
    # 见 docs/架构设计/硬件驱动开发路径.md「什么时候必须去修 deploy 脚本」：
    # 任何硬件第一次交付常驻 aarch64 二进制时，同一提交内引入本文件，
    # 并把 deploy_device.sh / provision_device.sh 的四处枚举改成从它读。
    "device/deploy/binaries.txt",
}

# 自净：表里每一条都必须**尚不存在**。一旦落地就该从表里删掉，
# 否则"写错路径"与"还没建"从此分不清——豁免会永久化。
for _future in sorted(FUTURE_PATHS):
    if _future in all_repo_files:
        print(f"  !! {_future} 已落地，请从 FUTURE_PATHS 移除（见该表上方注释）")
        fail = 1

path_refs = 0
path_refs_exempt = 0
for path in scan_files:
    text = io.open(path, encoding="utf-8").read()
    in_manual = path.startswith(MANUAL_DIR_PREFIX)
    # 相对引用依次在"文档所在目录 -> 其各级祖先（到仓库根）"下查找。
    # 手册目录里写 `<型号>/协议/对象字典.md` 这种从本目录起算的相对路径，
    # 若只查文档同级目录会误报——而误报多了检查器就被忽略了。
    bases = []
    d = os.path.dirname(path)
    while True:
        bases.append(d)
        if not d or d == ".":
            break
        d = os.path.dirname(d)
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
        if any(os.path.join(b, ref) in all_repo_files for b in bases):
            continue
        if ref in FUTURE_PATHS:
            # 文档明确写为"将来才引入"的路径：此刻不在磁盘上正是**正确**状态。
            # 能走到这里即已证明它不在仓库里（上面两条 continue 处理了存在的情形），
            # 所以命中豁免表 = 放行，只计数。
            path_refs_exempt += 1
            continue
        if in_manual:
            # 手册目录内的未投喂清单项：不计失败，只计数，输出里可见。
            path_refs_exempt += 1
            continue
        print(f"  悬空引用（文件）: {path} -> {ref}")
        fail = 1
print(f"  仓库内路径引用：检查 {path_refs} 处"
      f"（其中 {path_refs_exempt} 处位于 {MANUAL_DIR_PREFIX} 内，按未投喂资料豁免）")

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
can_files = DocumentedMarkdown()
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
    "find . -path './harness' -prune -o -type f -not -path './.git/*' -not -path './build-host/*' "
    "-not -path './build-server/*' -not -path './build-arm64/*' "
    "-not -path './build-tsan/*' -not -path './harness/*' -print"
).read()
bases = {os.path.basename(l) for l in files.split("\n") if l}
dirs = os.popen(
    "find . -path './harness' -prune -o -type d -not -path './.git*' -not -path './build-host*' "
    "-not -path './build-server*' -not -path './build-arm64*' "
    "-not -path './build-tsan*' -not -path './harness*' -print"
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
