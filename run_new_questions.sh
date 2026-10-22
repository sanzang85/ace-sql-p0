#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/scripts/common.sh"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  cat <<'HELP'
用法：./run_new_questions.sh [--check-only] [--exclude-unscorable] [--ids 1,2,3]
默认问题：portable_template_llm_module/data/inputs/q_0623.csv
默认答案：portable_template_llm_module/data/ground_truth/a_0623.xlsx
环境变量：INPUT_FILE、GT_FILE、RUN_DIR、WORKERS（默认4）、QUESTION_TIMEOUT（默认2700）、PYTHON_BIN。
默认遇到缺失或冲突答案立即停止；--exclude-unscorable 显式排除并记录这些题。
--ids 使用准备后CSV的行号；原题号对应见 prepared_inputs/question_id_mapping.csv。
--check-only 只检查文件及映射、保存检查报告，不调用模型或数据库。
HELP
  exit 0
fi
prepare_args=()
benchmark_args=()
check_only=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only) check_only=1; prepare_args+=(--check-only); shift ;;
    --exclude-unscorable) prepare_args+=(--exclude-unscorable); shift ;;
    --ids)
      if [[ $# -lt 2 ]]; then echo '--ids 需要题号列表' >&2; exit 2; fi
      benchmark_args+=(--ids "$2"); shift 2 ;;
    --ids=*) benchmark_args+=("$1"); shift ;;
    *) echo "不支持的参数：$1；查看 --help。" >&2; exit 2 ;;
  esac
done
input_file="${INPUT_FILE:-portable_template_llm_module/data/inputs/q_0623.csv}"
gt_file="${GT_FILE:-portable_template_llm_module/data/ground_truth/a_0623.xlsx}"
for path in "$input_file" "$gt_file"; do
  if [[ ! -r "$path" ]]; then echo "文件不存在或不可读：$path" >&2; exit 2; fi
done
run_dir="$(ace_run_dir new_questions)"
mkdir -p "$run_dir"
echo "输出目录：$run_dir"
"$PYTHON_BIN" "$ACE_ROOT/scripts/prepare_new_questions.py" \
  --input "$input_file" --gt "$gt_file" --run-dir "$run_dir" "${prepare_args[@]}" \
  2>&1 | tee -a "$run_dir/preparation.log"
if [[ "$check_only" == 1 ]]; then exit 0; fi
"$PYTHON_BIN" "$ACE_ROOT/scripts/run_patent2_benchmark.py" \
  --run-dir "$run_dir" --profile qwen38_p0 \
  --input "$run_dir/prepared_inputs/questions.csv" \
  --gt "$run_dir/prepared_inputs/ground_truth.xlsx" \
  --workers "${WORKERS:-4}" --timeout "${QUESTION_TIMEOUT:-2700}" "${benchmark_args[@]}" \
  2>&1 | tee -a "$run_dir/launcher.log"
"$PYTHON_BIN" "$ACE_ROOT/scripts/summarize_patent2_benchmark.py" "$run_dir" \
  2>&1 | tee "$run_dir/summary.log"
"$PYTHON_BIN" "$ACE_ROOT/scripts/audit_patent2_evidence.py" "$run_dir" \
  2>&1 | tee "$run_dir/audit.log"
"$PYTHON_BIN" "$ACE_ROOT/scripts/audit_p0.py" "$run_dir" \
  2>&1 | tee "$run_dir/isolation_audit.log"
echo "报告：$run_dir/analysis/实验报告.md"
echo "CSV：$run_dir/analysis/问答结果汇总.csv"
echo "Excel：$run_dir/analysis/问答结果汇总.xlsx"
echo "原始题号映射及排除明细：$run_dir/prepared_inputs/"
