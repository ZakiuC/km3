#!/bin/sh
set -eu
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
need_value=0
mm_per_rev=4
for arg in "$@"; do
    if [ "$need_value" != 0 ]; then
        case "$arg" in --*) echo "选项缺少数值" >&2; exit 2;; esac
        if [ "$need_value" = mm_per_rev ]; then mm_per_rev=$arg; fi
        need_value=0
        continue
    fi
    case "$arg" in
        --manual-mm-per-rev) need_value=mm_per_rev;;
        --manual-velocity|--rows|--cols|--timeout|--state-timeout|--move-timeout) need_value=1;;
        --ascii|--help|-h) ;;
        *) echo "手动启动脚本不接受此参数：$arg" >&2; exit 2;;
    esac
done
[ "$need_value" = 0 ] || { echo "选项缺少数值" >&2; exit 2; }
python3 -I -B -c '
import math, sys
args = iter(sys.argv[1:])
for option in args:
    if option in ("--ascii", "--help", "-h"):
        continue
    value = next(args)
    try:
        if option in ("--rows", "--cols", "--manual-velocity"):
            if not value.isascii() or not value.isdecimal() or int(value) <= 0:
                raise ValueError()
            if option == "--manual-velocity" and int(value) > 0xffffffff:
                raise ValueError()
        elif option == "--manual-mm-per-rev":
            from fractions import Fraction
            if Fraction(value) <= 0:
                raise ValueError()
        elif not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError()
    except (ValueError, OverflowError, ZeroDivisionError):
        print("参数值无效：" + option + "=" + value, file=sys.stderr)
        raise SystemExit(2)
' "$@"
# Remove validated scale overrides; pass the resolved value exactly once.
remaining=$#
while [ "$remaining" -gt 0 ]; do
    arg=$1; shift; remaining=$((remaining - 1))
    if [ "$arg" = --manual-mm-per-rev ]; then
        shift; remaining=$((remaining - 1))
    else
        set -- "$@" "$arg"
    fi
done
mkdir -p "$package_dir/logs"
run_dir=$(mktemp -d "$package_dir/logs/manual-XXXXXXXX")
printf '日志目录：%s\n' "$run_dir"
exec python3 -I -B "$package_dir/drivers/pmp4010/pmp4010.py" --manual --manual-linear --manual-mm-per-rev "$mm_per_rev" --node 1 --node-range 1 --motion-log "$run_dir/motion.jsonl" "$@"
