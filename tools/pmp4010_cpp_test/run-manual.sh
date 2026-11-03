#!/usr/bin/env bash
set -euo pipefail
tool_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(CDPATH= cd -- "$tool_dir/../.." && pwd)
build_dir=${PMP4010_CPP_BUILD_DIR:-"$repo_dir/build-pmp4010-cpp-host"}
binary="$build_dir/pmp4010_manual_test"
if [[ ! -x "$binary" ]]; then
  printf '未找到已构建的程序：%s\n请按 README.md 完成本地构建。\n' "$binary" >&2
  exit 2
fi
exec "$binary" "$@"
