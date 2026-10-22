#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
GH_BIN="${GH_BIN:-gh}"
if ! command -v "$GH_BIN" >/dev/null 2>&1; then
  if [[ -x "$HOME/.local/bin/gh" ]]; then GH_BIN="$HOME/.local/bin/gh";
  else echo '请安装 GitHub CLI 并执行 gh auth login --hostname github.com --git-protocol https --web。' >&2; exit 1; fi
fi
account="$("$GH_BIN" api user --jq .login)"
if [[ "$account" != sanzang85 ]]; then echo '当前授权账号不是 sanzang85，停止推送。' >&2; exit 1; fi
if git remote get-url origin >/dev/null 2>&1; then
  echo '本地已有 origin；请先核对远端，本脚本不覆盖已有配置。' >&2
  exit 1
fi
"$GH_BIN" auth setup-git --hostname github.com
"$GH_BIN" repo create sanzang85/ace-sql-p0 --private --source . --remote origin --push --description 'ACE-SQL P0: evidence-constrained natural language to SQL'
