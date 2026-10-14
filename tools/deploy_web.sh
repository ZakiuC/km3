#!/usr/bin/env bash
# 管理端部署：静态产物，nginx 托管。**服务器不引入 Node 运行时。**
# 依据 docs/架构设计/开发规范.md §5.3 与 管理端Web程序架构.md §八。
#
# 与 deploy_server.sh 的两点差异（静态站点的性质决定）：
#   1. 无服务重启：不切进程，只切目录。回滚即切回上一个目录，无需停机。
#   2. 健康检查看 index.html 可达性，没有 /readyz 这类探针。
#
# nginx 侧需满足（管理端Web程序架构.md §八）：
#   静态根  /opt/km3-web/current
#   SPA 回退 try_files $uri $uri/ /index.html
#   缓存    带 hash 的资源长缓存；index.html **不缓存**
#   同源    /api/ 反代到本机 km3-server（Cookie 会话与实时通道都依赖同源）
set -euo pipefail

HOST=""
VERSION=""
BUILD_DIR="admin-web/dist"
TARGET_ROOT="/opt/km3-web"
SITE_URL=""
DRY_RUN=0

usage() {
  printf 'usage: %s --host <ip|hostname> --version <semver> [--build-dir DIR] [--url URL] [--dry-run]\n' "$0"
  printf '\n  --url  对外访问地址，用于部署后校验，默认 http://<host>/\n'
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host)      HOST="$2"; shift 2 ;;
    --version)   VERSION="$2"; shift 2 ;;
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
    --url)       SITE_URL="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage ;;
    *) printf 'unknown arg: %s\n' "$1" >&2; usage ;;
  esac
done
[ -n "$HOST" ] && [ -n "$VERSION" ] || usage
[ -n "$SITE_URL" ] || SITE_URL="http://${HOST}/"

# 防呆：路径拼装的每一段都必须非空，否则下面的 rm -rf / ln -sfn 会指向错误位置。
[ -n "$TARGET_ROOT" ] || { printf 'TARGET_ROOT 为空，拒绝继续\n' >&2; exit 1; }
case "$TARGET_ROOT" in /*) ;; *) printf 'TARGET_ROOT 必须是绝对路径\n' >&2; exit 1 ;; esac

SSH_OPTS=(-o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)
REMOTE="root@${HOST}"
run() {
  if [ "$DRY_RUN" -eq 1 ]; then printf '[dry-run] ssh %s :: %s\n' "$REMOTE" "$*"; else
    ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"; fi
}

# 缺 index.html 说明构建没跑或失败，不是可容忍的边界情况。
[ -f "${BUILD_DIR}/index.html" ] || {
  printf '缺少 %s/index.html —— 先执行 admin-web 的构建\n' "$BUILD_DIR" >&2; exit 1; }

# 记录当前版本以便回滚。
# 不能用 `readlink -f ... | basename`：GNU readlink -f 对**不存在的路径**也会
# 打印规范化结果，首次部署时 PREV 会取到字面量 "current"（并不存在的版本目录），
# 回滚于是把 current 指向悬空目标，比不回滚更糟（deploy_device.sh 上实测过）。
PREV="$(ssh "${SSH_OPTS[@]}" "$REMOTE" \
  "if [ -L ${TARGET_ROOT}/current ]; then \
     t=\$(readlink ${TARGET_ROOT}/current); b=\$(basename \"\$t\"); \
     [ -d ${TARGET_ROOT}/releases/\$b ] && echo \"\$b\"; \
   fi" || true)"
printf '==> 目标 %s，版本 %s，当前 %s\n' "$REMOTE" "$VERSION" "${PREV:-<无>}"

run "mkdir -p ${TARGET_ROOT}/releases/${VERSION}"

# 静态站点是**目录**而非单个二进制。用 rsync --delete 保证远端与本地一致，
# 否则历次部署残留的带 hash 资源会一直堆积。无 rsync 时退化为 tar 管道。
if [ "$DRY_RUN" -eq 0 ]; then
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete -e "ssh ${SSH_OPTS[*]}" \
      "${BUILD_DIR}/" "${REMOTE}:${TARGET_ROOT}/releases/${VERSION}/"
  else
    printf '  (本机无 rsync，改用 tar 管道)\n' >&2
    tar -C "${BUILD_DIR}" -cf - . | ssh "${SSH_OPTS[@]}" "$REMOTE" \
      "mkdir -p ${TARGET_ROOT}/releases/${VERSION} && \
       rm -rf ${TARGET_ROOT}/releases/${VERSION:?}/* && \
       tar -C ${TARGET_ROOT}/releases/${VERSION} -xf -"
  fi
fi

# 与 deploy_server.sh 相同的原子切换：先建临时链接再 mv -T。
# 不直接用 ln -sfn：链接已存在时它会先删后建，中间态可能被并发请求读到。
run "ln -sfn ${TARGET_ROOT}/releases/${VERSION} ${TARGET_ROOT}/current.tmp && \
     mv -T ${TARGET_ROOT}/current.tmp ${TARGET_ROOT}/current"

if [ "$DRY_RUN" -eq 0 ]; then
  if ssh "${SSH_OPTS[@]}" "$REMOTE" "curl -fsS '${SITE_URL}' >/dev/null"; then
    printf '==> 部署成功：%s\n' "$VERSION"
  else
    printf '!! %s 不可达，回滚\n' "$SITE_URL" >&2
    if [ -n "$PREV" ]; then
      ssh "${SSH_OPTS[@]}" "$REMOTE" \
        "ln -sfn ${TARGET_ROOT}/releases/${PREV} ${TARGET_ROOT}/current.tmp && \
         mv -T ${TARGET_ROOT}/current.tmp ${TARGET_ROOT}/current"
    fi
    exit 1
  fi
fi
