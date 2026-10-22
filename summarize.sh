#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/scripts/common.sh"
if [[ $# -eq 0 || "$1" == --help || "$1" == -h ]]; then
  echo '用法：bash summarize.sh outputs/某次运行 [--partial] [--no-charts]'
  exit 0
fi
exec "$PYTHON_BIN" "$ACE_ROOT/scripts/summarize_patent2_benchmark.py" "$@"
