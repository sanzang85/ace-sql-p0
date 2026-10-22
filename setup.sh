#!/usr/bin/env bash
set -euo pipefail
P0_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$P0_ROOT/.venv/bin/python" ]]; then
  "${PYTHON_BIN:-python3}" -m venv "$P0_ROOT/.venv"
fi
"$P0_ROOT/.venv/bin/python" -m pip install -r "$P0_ROOT/requirements.txt"
if [[ "${INSTALL_CHARTS:-0}" == 1 ]]; then
  "$P0_ROOT/.venv/bin/python" -m pip install -r "$P0_ROOT/requirements-report.txt"
fi
echo "安装完成：$P0_ROOT/.venv/bin/python"
