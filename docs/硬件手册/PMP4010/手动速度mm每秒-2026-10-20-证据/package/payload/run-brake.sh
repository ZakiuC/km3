#!/bin/sh
set -eu
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
case "$#" in
    0) ;;
    1) case "$1" in
        --help|-h) exec python3 -I -B "$package_dir/drivers/pmp4010/pmp4010_brake.py" --help;;
        *) echo "抱闸测试启动器只接受 --help；请在界面操作" >&2; exit 2;;
       esac;;
    *) echo "抱闸测试启动器只接受 --help；请在界面操作" >&2; exit 2;;
esac
[ -t 0 ] && [ -t 1 ] || { echo "抱闸测试需要交互终端；未连接 CAN" >&2; exit 2; }
mkdir -p "$package_dir/logs"
run_dir=$(mktemp -d "$package_dir/logs/brake-XXXXXXXX")
printf '日志目录：%s\n' "$run_dir"
exec python3 -I -B "$package_dir/drivers/pmp4010/pmp4010_brake.py" --node 1 --motion-log "$run_dir/motion.jsonl"
