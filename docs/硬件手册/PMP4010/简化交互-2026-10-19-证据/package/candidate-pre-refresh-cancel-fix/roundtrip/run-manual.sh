#!/bin/sh
set -eu
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
need_value=0
for arg in "$@"; do
    if [ "$need_value" -eq 1 ]; then
        case "$arg" in --*) echo "选项缺少数值" >&2; exit 2;; esac
        need_value=0
        continue
    fi
    case "$arg" in
        --manual-velocity|--rows|--cols|--timeout|--state-timeout|--move-timeout) need_value=1;;
        --ascii|--help|-h) ;;
        *) echo "手动启动脚本不接受此参数：$arg" >&2; exit 2;;
    esac
done
[ "$need_value" -eq 0 ] || { echo "选项缺少数值" >&2; exit 2; }
mkdir -p "$package_dir/logs"
run_dir=$(mktemp -d "$package_dir/logs/manual-XXXXXXXX")
printf '日志目录：%s\n' "$run_dir"
exec python3 -I -B "$package_dir/drivers/pmp4010/pmp4010.py" --manual --node 1 --node-range 1 --motion-log "$run_dir/motion.jsonl" "$@"
