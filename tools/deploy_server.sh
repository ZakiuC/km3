#!/usr/bin/env bash
# 服务器端部署：原生 systemd，不用容器。
# 依据 docs/架构设计/开发规范.md §5.2。
set -euo pipefail

HOST=""
VERSION=""
BUILD_DIR="build-server"
TARGET_ROOT="/opt/km3-server"
SERVICE="km3-server"
HEALTH_URL="http://127.0.0.1:8080/readyz"
DRY_RUN=0

usage() {
  cat <<EOF
usage: $0 --host <ip|hostname> --version <semver> [--build-dir DIR] [--health-url URL] [--dry-run]
EOF
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host)       HOST="$2"; shift 2 ;;
    --version)    VERSION="$2"; shift 2 ;;
    --build-dir)  BUILD_DIR="$2"; shift 2 ;;
    --health-url) HEALTH_URL="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done
[ -n "$HOST" ] && [ -n "$VERSION" ] || usage

SSH_OPTS=(-o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)
REMOTE="root@${HOST}"
run() {
  if [ "$DRY_RUN" -eq 1 ]; then echo "[dry-run] ssh ${REMOTE} :: $*"; else
    ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"; fi
}

[ -f "${BUILD_DIR}/km3-server" ] || { echo "缺少 ${BUILD_DIR}/km3-server" >&2; exit 1; }

PREV="$(ssh "${SSH_OPTS[@]}" "$REMOTE" "readlink -f ${TARGET_ROOT}/current 2>/dev/null | xargs -r basename" || true)"
echo "==> 目标 ${REMOTE}，版本 ${VERSION}，当前 ${PREV:-<无>}"

run "mkdir -p ${TARGET_ROOT}/releases/${VERSION}"
if [ "$DRY_RUN" -eq 0 ]; then
  scp "${SSH_OPTS[@]}" "${BUILD_DIR}/km3-server" "${REMOTE}:${TARGET_ROOT}/releases/${VERSION}/"
fi

# 迁移先于切换：编号 SQL，psql 顺序执行
run "for f in /opt/km3-server/migrations/*.sql; do psql -v ON_ERROR_STOP=1 -f \"\$f\" >/dev/null; done"

run "ln -sfn ${TARGET_ROOT}/releases/${VERSION} ${TARGET_ROOT}/current.tmp && \
     mv -T ${TARGET_ROOT}/current.tmp ${TARGET_ROOT}/current && \
     systemctl restart ${SERVICE}"

if [ "$DRY_RUN" -eq 0 ]; then
  sleep 2
  if ssh "${SSH_OPTS[@]}" "$REMOTE" "curl -fsS ${HEALTH_URL} >/dev/null"; then
    echo "==> 部署成功：${VERSION}"
  else
    echo "!! /readyz 失败，回滚" >&2
    [ -n "$PREV" ] && ssh "${SSH_OPTS[@]}" "$REMOTE" \
      "ln -sfn ${TARGET_ROOT}/releases/${PREV} ${TARGET_ROOT}/current && systemctl restart ${SERVICE}"
    exit 1
  fi
fi
