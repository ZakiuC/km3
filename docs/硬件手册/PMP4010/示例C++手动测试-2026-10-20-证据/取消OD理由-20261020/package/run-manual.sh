#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/pmp4010_manual_test" --interface can1 --node 1 --mm-per-rev 4 "$@"
