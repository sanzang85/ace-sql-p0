from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .runtime import TRACE_RUNTIME
from .trace import trace_summary, write_trace_json


def write_run_report(app, initial_input: Dict[str, Any], config: Dict[str, Any], output_path: Path) -> Dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as report_file:
        report_file.write("# NL2SQL 工作流运行报告\n\n")
        report_file.write(f"**用户查询**: {initial_input['query']}\n\n")
        if TRACE_RUNTIME.run_dir:
            report_file.write(f"**Trace目录**: `{TRACE_RUNTIME.run_dir}`\n\n")
        report_file.write("---\n\n## 1. 节点执行详细日志\n\n")

        for update in app.stream(initial_input, config=config, stream_mode="updates"):
            for node_name, state_update in update.items():
                # LangGraph emits None for nodes returning an empty update
                # (e.g. a lifecycle selector outside the quality domain).
                state_update = state_update or {}
                print(f"--- 节点完成: {node_name} ---")
                report_file.write(f"### [节点完成]: {node_name}\n\n")
                if "generated_sql" in state_update:
                    report_file.write(f"**生成的探测/最终 SQL**:\n```sql\n{state_update['generated_sql']}\n```\n\n")
                if "keywords" in state_update:
                    report_file.write(f"**提取的关键词对**: {state_update['keywords']}\n\n")
                if "iteration_results" in state_update:
                    report_file.write(f"**数据库探测证据**:\n{state_update['iteration_results']}\n\n")

        final_state = app.get_state(config)
        final_values = final_state.values
        report_file.write("---\n\n## 2. 最终输出结果\n\n")
        if TRACE_RUNTIME.run_dir:
            write_trace_json(str(Path(TRACE_RUNTIME.run_dir) / "final_state.json"), final_values)
            write_trace_json(
                str(Path(TRACE_RUNTIME.run_dir) / "final_state_summary.json"),
                {key: trace_summary(value) for key, value in final_values.items()},
            )

        if "final_execution_result" in final_values:
            report_file.write("### 最终报表 (Markdown):\n\n")
            report_file.write(final_values["final_execution_result"] + "\n\n")
        if "generated_sql" in final_values:
            report_file.write(f"\n### 最终生成的完整 SQL:\n\n```sql\n{final_values.get('generated_sql', '')}\n```\n")
        if final_values.get("lifecycle_event_id"):
            report_file.write(f"\n### 模板生命周期\n\n版本：{final_values.get('lifecycle_template_version', 0)}；命中分数：{final_values.get('lifecycle_match_score', 0)}；探索：{final_values.get('lifecycle_exploration', False)}；事件：{final_values['lifecycle_event_id']}。\n")
        return dict(final_values)

