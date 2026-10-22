#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/scripts/common.sh"
if [[ $# -eq 0 || "$1" == --help || "$1" == -h ]]; then
  echo '用法：bash run_query.sh "自然语言问题" [其他 query CLI 参数]'
  echo '可选环境变量：PYTHON_BIN、RUN_DIR。模型配置在 portable_template_llm_module/data/config/model_profiles.json。'
  exit 0
fi
query="$1"
shift
run_dir="$(ace_run_dir query)"
mkdir -p "$(dirname -- "$run_dir")"
mkdir "$run_dir"  # A single-query run must not overwrite an existing run.
export NL2SQL_MECHANISM_ABLATION=none
echo "输出目录：$run_dir"
"$PYTHON_BIN" "$ACE_ROOT/ace_sql_patent2.py" query \
  --profile qwen38_p0 --query "$query" \
  --output "$run_dir/report.md" --trace_dir "$run_dir/trace" \
  --gt_file "$ACE_ROOT/portable_template_llm_module/data/ground_truth/a_0623.xlsx" \
  --eval_output "$run_dir/evaluation.xlsx" "$@" 2>&1 | tee "$run_dir/console.log"
