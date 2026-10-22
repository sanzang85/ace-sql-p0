#!/usr/bin/env bash
# Shared setup; source this file from launchers.
set -euo pipefail
ACE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ACE_ROOT/.env" ]]; then
  set -a
  source "$ACE_ROOT/.env"
  set +a
fi
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "$ACE_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ACE_ROOT/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi
export PYTHONPATH="$ACE_ROOT/portable_template_llm_module"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
cd "$ACE_ROOT"

ace_run_dir() {
  local kind="$1"
  "$PYTHON_BIN" - "$ACE_ROOT" "${RUN_DIR:-}" "$kind" <<'PY'
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import sys
root, supplied, kind = sys.argv[1:]
path = Path(supplied) if supplied else Path('outputs') / (kind + '_' + datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f'))
print((Path(root) / path).resolve())
PY
}
