#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/scripts/common.sh"
exec "$PYTHON_BIN" "$ACE_ROOT/scripts/check_environment.py" "$@"
