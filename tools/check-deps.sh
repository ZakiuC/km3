#!/usr/bin/env bash
# 校验 tools/install-deps.sh 是否覆盖两个 CMake 工程的全部 find_package。
#
# 起因：早先用手写正则审计，只在"缩进续行"上匹配包名，漏掉了
# `apt-get install -y libpq-dev` 这种单行写法，误报 libpq-dev 缺失。
# 本脚本改为先抽出全部 install 行（含续行），再逐个 token 比对。
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
import io, re, sys

def packages_in(script):
    """收集脚本里所有会被 apt 安装的目标包（含反斜杠续行）。

    识别两种形态：
      1. apt-get install [-y] pkg1 pkg2
      2. install_group "人类可读标签" pkg1 pkg2      <- install-deps.sh 现在用它分组安装

    第 2 种是后加的：把全部包塞进一条 apt-get 时，任一不可解析的依赖会让
    整次安装失败且什么都不装，因此改为分组安装并逐组报告。
    但**只更新安装脚本、忘了同步这个解析器**，结果是它报"提供 0 个包"、
    把所有依赖都判成缺失 —— 检查器本身成了假警报来源。
    标签一定要先剔除：它含中文、全角括号与斜杠，不是包名，
    若按 token 一起取进来会污染集合（例如把 clang-format 从标签里认成包名）。
    """
    text = io.open(script, encoding="utf-8").read()
    # 先把续行拼成单行
    text = re.sub(r"\\\n\s*", " ", text)
    pkgs = set()
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            continue
        body = ""
        if re.match(r"^apt-get\s+install\b", s):
            # 去掉 apt-get install -y 前缀
            body = re.sub(r"^apt-get\s+install\s+(-[a-zA-Z]+\s+)*", "", s)
        elif re.match(r"^install_group\b", s):
            # 去掉 install_group 与它后面的第一个引号参数（标签）
            body = re.sub(r'^install_group\s+"[^"]*"\s*', "", s)
            if body == s:
                # 标签未加引号时退化为去掉首个 token，避免整行被吞
                body = re.sub(r"^install_group\s+\S+\s*", "", s)
        else:
            continue
        body = body.split("#")[0]
        for tok in body.split():
            if re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", tok):
                pkgs.add(tok)
    return pkgs

# CMake 依赖 -> 提供它的 Debian 包（多选一即满足）
PROVIDERS = {
    "Threads":       ["build-essential"],
    "Protobuf":      ["libprotobuf-dev"],
    "spdlog":        ["libspdlog-dev"],
    "fmt":           ["libfmt-dev"],
    "nlohmann_json": ["nlohmann-json3-dev"],
    "ZeroMQ":        ["libzmq3-dev"],
    "SQLite3":       ["libsqlite3-dev"],
    "OpenSSL":       ["libssl-dev"],
    "Boost":         ["libboost-all-dev", "libboost-dev"],
    "PostgreSQL":    ["libpq-dev"],
    "libpqxx":       ["libpqxx-dev"],
}

cmake = ""
for f in ("device/CMakeLists.txt", "server/CMakeLists.txt"):
    cmake += io.open(f, encoding="utf-8").read()
needed = sorted(set(re.findall(r"find_package\((\w+)", cmake)))

have = packages_in("tools/install-deps.sh")

print(f"  install-deps.sh 提供 {len(have)} 个包")
print(f"  CMake 共需要 {len(needed)} 个依赖\n")

missing, unknown = [], []
for dep in needed:
    provs = PROVIDERS.get(dep)
    if not provs:
        unknown.append(dep)
        print(f"    {dep:16s} ??  未在映射表中")
        continue
    hit = [p for p in provs if p in have]
    if hit:
        print(f"    {dep:16s} OK  ({hit[0]})")
    else:
        print(f"    {dep:16s} 缺失 -> 应装 {provs}")
        missing.append((dep, provs))

print()
if missing:
    print("结果：install-deps.sh 不完整，缺失：")
    for dep, provs in missing:
        print(f"  {dep} -> {provs}")
    sys.exit(1)
if unknown:
    print(f"结果：全部已知依赖已覆盖；{len(unknown)} 个需人工确认：{', '.join(unknown)}")
    sys.exit(0)
print("结果：install-deps.sh 覆盖全部 CMake 依赖")
PY
