#!/usr/bin/env bash
# 设备端部署：版本目录 + 符号链接原子切换 + 失败回滚。
# 依据 docs/架构设计/开发规范.md §5.1 与 OTA升级方案.md §3。
#
# 重要：只有 km3-core / km3-video 可以这样增量切换。
#       km3-rt-controller 变更必须停机，见 OTA升级方案.md §二。
set -euo pipefail

HOST=""
VERSION=""
BUILD_DIR="build-arm64"
SERVICES_CORE=(km3-core km3-video)
TARGET_ROOT="/opt/km3"
DRY_RUN=0

usage() {
  cat <<EOF
usage: $0 --host <ip|hostname> --version <semver> [--build-dir DIR] [--root DIR] [--dry-run]

部署 km3 设备端。上传到 \$ROOT/releases/<version>/，切换 current 符号链接并重启服务。
若健康检查失败，自动切回上一版本。
EOF
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host)      HOST="$2"; shift 2 ;;
    --version)   VERSION="$2"; shift 2 ;;
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
    --root)      TARGET_ROOT="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

[ -n "$HOST" ] && [ -n "$VERSION" ] || usage

SSH_OPTS=(-o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)
REMOTE="root@${HOST}"

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] ssh ${REMOTE} :: $*"
  else
    ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"
  fi
}

echo "==> 目标 ${REMOTE}，版本 ${VERSION}，根目录 ${TARGET_ROOT}"

for bin in km3-rt-controller km3-core km3-video; do
  [ -f "${BUILD_DIR}/${bin}" ] || { echo "缺少构建产物 ${BUILD_DIR}/${bin}，请先交叉编译" >&2; exit 1; }
done

# 记录当前版本以便回滚。
#
# 不能用 `readlink -f ... | basename`：GNU readlink -f 对**不存在的路径**
# 也会打印规范化结果，因此首次部署时 PREV 会取到字面量 "current" ——
# 一个并不存在的版本目录。回滚于是把 current 指向 releases/current，
# 得到一个悬空链接，比不回滚更糟（实测）。
# 正确做法：先确认它真的是符号链接，再取目标，并校验目标目录存在。
PREV="$(ssh "${SSH_OPTS[@]}" "$REMOTE" \
  "if [ -L ${TARGET_ROOT}/current ]; then \
     t=\$(readlink ${TARGET_ROOT}/current); b=\$(basename \"\$t\"); \
     [ -d ${TARGET_ROOT}/releases/\$b ] && echo \"\$b\"; \
   fi" || true)"
echo "==> 当前版本：${PREV:-<无>}"

# 只建 releases/<version>。**绝不 mkdir current.tmp**：
# 若 current.tmp 先被建成目录，随后的 `ln -sfn X current.tmp` 会按 ln 的既有语义
# 在该目录**内部**建一个指向 X 的链接，`mv -T` 再把整个目录搬成 current ——
# 结果 current 是目录而非符号链接，ExecStart=/opt/km3/current/km3-core
# 永远找不到文件（status=203/EXEC）。早期版本的"原子切换"从未真正成立。
run "mkdir -p ${TARGET_ROOT}/releases/${VERSION}"

echo "==> 上传二进制"
if [ "$DRY_RUN" -eq 0 ]; then
  scp "${SSH_OPTS[@]}" \
      "${BUILD_DIR}/km3-rt-controller" \
      "${BUILD_DIR}/km3-core" \
      "${BUILD_DIR}/km3-video" \
      "${REMOTE}:${TARGET_ROOT}/releases/${VERSION}/"
fi

echo "==> 切换 current 并重启"
# 自愈：早期缺陷可能已把 current 留成**目录**（内含零散链接）。
# mv -T 无法用符号链接覆盖目录，会直接失败，所以先清掉。
# 只在其中没有普通文件时才删，避免误删真实内容。
run "if [ -d ${TARGET_ROOT}/current ] && [ ! -L ${TARGET_ROOT}/current ]; then \
       n=\$(find ${TARGET_ROOT}/current -type f 2>/dev/null | wc -l); \
       if [ \"\$n\" -eq 0 ]; then \
         echo '    current 是目录且无普通文件（历史缺陷残留），删除'; \
         rm -rf ${TARGET_ROOT}/current; \
       else \
         echo \"    current 是目录且含 \$n 个普通文件，拒绝删除\" >&2; exit 1; \
       fi; \
     fi"
# 原子切换：先建**符号链接**到临时名，再 mv -T 覆盖 current。
# mv -T 是原子的重命名，因此不存在"current 短暂缺失"的窗口。
run "rm -f ${TARGET_ROOT}/current.tmp && \
     ln -s ${TARGET_ROOT}/releases/${VERSION} ${TARGET_ROOT}/current.tmp && \
     mv -T ${TARGET_ROOT}/current.tmp ${TARGET_ROOT}/current && \
     systemctl daemon-reload && \
     systemctl restart ${SERVICES_CORE[*]}"

echo "==> 健康检查"
if [ "$DRY_RUN" -eq 0 ]; then
  sleep 3
  if ssh "${SSH_OPTS[@]}" "$REMOTE" "systemctl is-active --quiet ${SERVICES_CORE[*]}"; then
    echo "==> 部署成功：${VERSION}"
  else
    echo "!! 健康检查失败，回滚" >&2
    if [ -n "$PREV" ]; then
      ssh "${SSH_OPTS[@]}" "$REMOTE" \
        "rm -f ${TARGET_ROOT}/current.tmp && \
         ln -s ${TARGET_ROOT}/releases/${PREV} ${TARGET_ROOT}/current.tmp && \
         mv -T ${TARGET_ROOT}/current.tmp ${TARGET_ROOT}/current && \
         systemctl restart ${SERVICES_CORE[*]}"
      echo "==> 已回滚到 ${PREV}" >&2
    fi
    exit 1
  fi
fi
