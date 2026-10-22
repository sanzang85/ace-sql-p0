from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .config import (
    USE_VLLM_EMBEDDINGS,
    VLLM_CHAT_MODEL,
    VLLM_EMBEDDING_MODEL,
    VLLM_EMBEDDING_OPENAI_BASE_URL,
    VLLM_OPENAI_BASE_URL,
    VLLM_RERANK_MODEL,
    get_project_paths,
)
from .evaluation import evaluate_and_persist
from .reporting import write_run_report
from .runtime import TRACE_RUNTIME
from .state import initial_state
from .trace import write_trace_json


def build_parser() -> argparse.ArgumentParser:
    paths = get_project_paths()
    parser = argparse.ArgumentParser(description="NL2SQL 项目统一入口")
    parser.add_argument("--query", type=str, required=True, help="用户输入的自然语言问题")
    parser.add_argument("--request-id", default=None, help="可复现的请求标识；省略时使用随机标识")
    parser.add_argument(
        "--output",
        type=str,
        default=str(paths.reports_dir / "nl2sql_output_report.md"),
        help="单次运行报告输出路径",
    )
    parser.add_argument(
        "--thread",
        type=str,
        default="test_thread_eval",
        help="LangGraph 记忆线程 ID",
    )
    parser.add_argument(
        "--gt_file",
        type=str,
        default=str(paths.default_gt_file),
        help="包含标准 SQL 的 Excel 文件",
    )
    parser.add_argument(
        "--eval_output",
        type=str,
        default=str(paths.evaluation_dir / "evaluation_results.xlsx"),
        help="评测结果输出 Excel 路径",
    )
    parser.add_argument(
        "--trace_dir",
        type=str,
        default=str(paths.traces_dir),
        help="trace 输出目录",
    )
    parser.add_argument(
        "--trace_max_chars",
        type=int,
        default=0,
        help="trace 中单个字符串最大字符数，0 表示不截断",
    )
    parser.add_argument(
        "--disable_trace",
        action="store_true",
        help="关闭 trace 输出",
    )
    parser.add_argument(
        "--append-eval-output",
        action="store_true",
        help="追加写入评测结果，默认覆盖旧文件",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(argv)

    repo_root = get_project_paths().repo_root
    for attr in ("output", "gt_file", "eval_output", "trace_dir"):
        raw_value = Path(getattr(options, attr))
        if not raw_value.is_absolute():
            setattr(options, attr, str((repo_root / raw_value).resolve()))

    output_path = Path(options.output)
    gt_file = Path(options.gt_file)
    eval_output = Path(options.eval_output)
    trace_dir = Path(options.trace_dir)

    print(f"[vLLM] chat endpoint: {VLLM_OPENAI_BASE_URL} | chat={VLLM_CHAT_MODEL} | rerank={VLLM_RERANK_MODEL}")
    print(f"[vLLM] embedding endpoint: {VLLM_EMBEDDING_OPENAI_BASE_URL} | embedding={VLLM_EMBEDDING_MODEL} | enabled={USE_VLLM_EMBEDDINGS}")

    TRACE_RUNTIME.max_string_chars = max(0, options.trace_max_chars)
    TRACE_RUNTIME.node_counter = 0
    if not options.disable_trace:
        run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        query_slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", options.query).strip("_")[:50] or "query"
        TRACE_RUNTIME.run_dir = str((trace_dir / f"{run_stamp}_{query_slug}").resolve())
        Path(TRACE_RUNTIME.run_dir, "nodes").mkdir(parents=True, exist_ok=True)
        write_trace_json(
            str(Path(TRACE_RUNTIME.run_dir) / "run_meta.json"),
            {
                "script": str((repo_root / "ace-sql0626_trace.py").resolve()),
                "entrypoint": str(Path(sys.argv[0]).resolve()),
                "query": options.query,
                "thread": options.thread,
                "output": str(output_path.resolve()),
                "gt_file": str(gt_file.resolve()),
                "eval_output": str(eval_output.resolve()),
                "trace_max_chars": TRACE_RUNTIME.max_string_chars,
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "note": "Each node directory contains input.json, input_summary.json, output.json, output_summary.json, and meta.json.",
            },
        )
    else:
        TRACE_RUNTIME.run_dir = None

    initial_input = initial_state(options.query)
    if options.request_id:
        initial_input["lifecycle_request_id"] = options.request_id
    config = {"configurable": {"thread_id": options.thread}}
    print(f"\n开始运行 NL2SQL 工作流...\n【问题】: {options.query}\n【单次输出文件】: {output_path}")
    if TRACE_RUNTIME.run_dir:
        print(f"【Trace目录】: {TRACE_RUNTIME.run_dir}")

    from .workflow import build_workflow

    app = build_workflow()
    from .knowledge_boundary import GenerationBoundary
    boundary = GenerationBoundary(repo_root / 'data/knowledge', gt_file)
    try:
        with boundary:
            final_values = write_run_report(app, initial_input, config, output_path)
    finally:
        if TRACE_RUNTIME.run_dir:
            write_trace_json(str(Path(TRACE_RUNTIME.run_dir) / 'generation_access_audit.json'), boundary.report())
    evaluation = evaluate_and_persist(
        query=options.query,
        predicted_sql=final_values.get("generated_sql", ""),
        predicted_data=final_values.get("raw_execution_data", []),
        gt_file=gt_file,
        eval_output=eval_output,
        append=options.append_eval_output,
        predicted_error=final_values.get("error_message"),
    )
    from .lifecycle_runtime import record_evaluation_event

    record_evaluation_event(final_values, evaluation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
