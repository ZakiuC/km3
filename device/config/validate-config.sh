#!/usr/bin/env bash
# Reuse the production C++ parser/validator; never maintain duplicate JSON rules.
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "usage: $0 /absolute/path/km3-rt-controller /path/device.json" >&2
  exit 2
fi
exec "$1" --validate-config-only --config "$2"
