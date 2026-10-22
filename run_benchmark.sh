#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/scripts/common.sh"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo '用法：bash run_benchmark.sh [--ids 15,39] [--ablation no_name_expansion]'
  echo '环境变量：RUN_DIR（指定已有目录可续跑）、WORKERS（默认4）、QUESTION_TIMEOUT（默认2700秒）、PYTHON_BIN。'
  echo 'run-dir、profile、input、gt、template-store、workers、timeout由脚本管理；修改并发或超时使用环境变量。'
  exit 0
fi
for arg in "$@"; do
  case "$arg" in
    --run-dir|--run-dir=*|--profile|--profile=*|--input|--input=*|--gt|--gt=*|--template-store|--template-store=*|--workers|--workers=*|--timeout|--timeout=*)
      echo "该参数由脚本管理：$arg；如需自定义输入，请直接使用 scripts/run_patent2_benchmark.py。" >&2
      exit 2 ;;
  esac
done
run_dir="$(ace_run_dir benchmark)"
mkdir -p "$run_dir"
echo "输出目录：$run_dir"
"$PYTHON_BIN" "$ACE_ROOT/scripts/run_patent2_benchmark.py" \
  --run-dir "$run_dir" --profile qwen38_p0 \
  --input "$ACE_ROOT/portable_template_llm_module/data/inputs/q_0623.csv" \
  --gt "$ACE_ROOT/portable_template_llm_module/data/ground_truth/a_0623.xlsx" \
  --workers "${WORKERS:-4}" --timeout "${QUESTION_TIMEOUT:-2700}" "$@" \
  2>&1 | tee -a "$run_dir/launcher.log"
"$PYTHON_BIN" "$ACE_ROOT/scripts/summarize_patent2_benchmark.py" "$run_dir" \
  2>&1 | tee "$run_dir/summary.log"
"$PYTHON_BIN" "$ACE_ROOT/scripts/audit_patent2_evidence.py" "$run_dir" \
  2>&1 | tee "$run_dir/audit.log"
"$PYTHON_BIN" "$ACE_ROOT/scripts/audit_p0.py" "$run_dir" 2>&1 | tee "$run_dir/isolation_audit.log"
echo "完成：$run_dir/analysis/实验报告.md"
echo "问答表格：$run_dir/analysis/问答结果汇总.xlsx"
echo "CSV表格：$run_dir/analysis/问答结果汇总.csv"
