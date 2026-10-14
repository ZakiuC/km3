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
    """收集脚本里所有 apt-get install 的目标包（含反斜杠续行）。"""
    text = io.open(script, encoding="utf-8").read()
    # 先把续行拼成单行
    text = re.sub(r"\\\n\s*", " ", text)
    pkgs = set()
    for line in text.split("\n"):
        s = line.strip()
        if not re.match(r"^apt-get\s+install\b", s):
            continue
        if s.startswith("#"):
            continue
        # 去掉 apt-get install -y 前缀与行尾注释
        body = re.sub(r"^apt-get\s+install\s+(-[a-zA-Z]+\s+)*", "", s)
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
