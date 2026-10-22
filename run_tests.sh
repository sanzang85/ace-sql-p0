#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/scripts/common.sh"
"$PYTHON_BIN" -m unittest discover -s "$ACE_ROOT/tests" -v
