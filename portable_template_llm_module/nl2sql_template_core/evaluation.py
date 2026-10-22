from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from .config import get_db_config
from .lifecycle import feature_enabled
from .quality_contracts import compare_results, compare_result_values
from .runtime import TRACE_RUNTIME
from .trace import write_trace_json


def _normalize_data(data_list: List[Dict[str, Any]]) -> List[Tuple[str, ...]]:
    if not data_list:
        return []

    normalized_rows: List[Tuple[str, ...]] = []
    for row in data_list:
        numbers: List[str] = []
        for value in row.values():
            if value is None:
                continue
            try:
                numbers.append(f"{float(value):.4f}")
            except (ValueError, TypeError):
                continue
        normalized_rows.append(tuple(sorted(numbers)))
    return sorted(normalized_rows)


def evaluate_and_persist(
    query: str,
    predicted_sql: str,
    predicted_data: List[Dict[str, Any]],
    gt_file: Path,
    eval_output: Path,
    *,
    append: bool = False,
    predicted_error: str | None = None,
) -> Dict[str, Any]:
    print("\n======== 开始结果对齐与自动化测评 ========")
    correct_sql = "未找到对应的标准答案"
    correct_data: List[Dict[str, Any]] = []
    is_correct = False
    result_values_match = False
    evaluation_available = False
    gt_execution_error: str | None = None

    try:
        if gt_file.exists():
            gt_df = pd.read_excel(gt_file)
            q_col, s_col = gt_df.columns[1], gt_df.columns[2]
            matched_row = gt_df[gt_df[q_col].astype(str).str.strip() == query.strip()]
            if not matched_row.empty:
                correct_sql = matched_row.iloc[0][s_col]
                connection = None
                try:
                    import pymysql

                    if feature_enabled():
                        from .execution import execute_sql_once
                        _, correct_data, gt_execution_error = execute_sql_once(str(correct_sql))
                    else:
                        connection = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
                        with connection.cursor() as cursor:
                            cursor.execute(correct_sql)
                            correct_data = list(cursor.fetchall())
                    evaluation_available = gt_execution_error is None
                except Exception as db_error:
                    gt_execution_error = str(db_error)
                    print(f" [测评警告] 执行标准 SQL 时出错: {db_error}")
                finally:
                    if connection:
                        connection.close()

                if predicted_error:
                    is_correct = False
                    print(f" [测评警告] 预测 SQL 执行失败/未通过校验: {predicted_error}")
                elif gt_execution_error:
                    is_correct = False
                    print(" [测评警告] 标准 SQL 执行失败，本题不能按空结果相等判定为正确。")
                else:
                    is_correct = compare_results(predicted_data, correct_data) if feature_enabled() else _normalize_data(predicted_data) == _normalize_data(correct_data)
                    result_values_match = compare_result_values(predicted_data, correct_data)

                print(f" [测评结果] 判定为: {'正确' if is_correct else '错误'}")
            else:
                print(f" [测评警告] 未在 {gt_file} 中找到问题对应的标准答案。")
        else:
            print(f" [测评警告] 找不到标准答案文件: {gt_file}")
    except Exception as exc:
        print(f" [测评系统错误]: {exc}")

    record = {
        "问题 (Query)": query,
        "预测的SQL (Predicted)": predicted_sql,
        "正确的SQL (Ground Truth)": correct_sql,
        "是否正确 (Is_Correct)": is_correct,
        "Evaluation_Available": evaluation_available,
        "Result_Values_Match": result_values_match,
        "Both_Results_Empty": evaluation_available and not predicted_error and not predicted_data and not correct_data,
        "预测执行错误 (Predicted Error)": predicted_error or "",
        "标准SQL执行错误 (GT Execution Error)": gt_execution_error or "",
        "预测的返回结果 (Predicted JSON)": json.dumps(predicted_data, default=str, ensure_ascii=False),
        "正确的返回结果 (Correct JSON)": json.dumps(correct_data, default=str, ensure_ascii=False),
    }

    eval_output.parent.mkdir(parents=True, exist_ok=True)
    df_new = pd.DataFrame([record])
    if append and eval_output.exists():
        df_existing = pd.read_excel(eval_output)
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        query_col = "问题 (Query)"
        if query_col in df_final.columns:
            df_final[query_col] = df_final[query_col].astype(str).str.strip()
            current_query = str(query).strip()
            df_final = df_final[df_final[query_col] != current_query]
            df_final = pd.concat([df_final, df_new], ignore_index=True)
    else:
        df_final = df_new
    df_final.to_excel(eval_output, index=False)

    if TRACE_RUNTIME.run_dir:
        write_trace_json(
            str(Path(TRACE_RUNTIME.run_dir) / "evaluation.json"),
            {
                "query": query,
                "predicted_sql": predicted_sql,
                "correct_sql": correct_sql,
                "is_correct": is_correct,
                "result_values_match": result_values_match,
                "both_results_empty": evaluation_available and not predicted_error and not predicted_data and not correct_data,
                "evaluation_available": evaluation_available,
                "predicted_error": predicted_error,
                "gt_execution_error": gt_execution_error,
                "predicted_data": predicted_data,
                "correct_data": correct_data,
                "eval_output": str(eval_output.resolve()),
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        print(f" -> 节点 Trace 已保存至: {TRACE_RUNTIME.run_dir}")
    print(f" -> 测评记录已去重并保存至: {eval_output}\n")
    return record
