from __future__ import annotations

import json
import re
from typing import Any, Dict

from .schema_catalog import parse_full_schema_catalog
from .state import NL2SQLState
from .intent_contracts import accuracy_mode, record_count, simple_aggregate
from .text_utils import safe_json_loads


def query_traits(query: str) -> Dict[str, Any]:
    query = query or ""
    has_day = bool(re.search(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*(?:日|号)|\d{4}-\d{1,2}-\d{1,2}", query))
    has_month = bool(re.search(r"\d{4}\s*年\s*\d{1,2}\s*月(?!\s*\d{1,2}\s*(?:日|号))|\d{4}-\d{1,2}(?!-\d{1,2})", query))
    group_terms = ["各", "按", "每", "每日", "每天", "每月", "分组", "排名", "Top", "top", "明细", "列表", "分别"]
    no_group_terms = ["总", "汇总", "合计", "总计", "数量", "个数", "次数", "百分比", "完成率", "产量"]
    duration_terms = ["时长", "小时", "耗时", "超时小时", "分钟", "超时分钟", "延迟"]
    count_terms = ["次数", "个数", "数量", "批次", "工单数"]
    output_grain = "total"
    has_duration_metric = any(term in query for term in duration_terms)
    has_count_metric = any(term in query for term in count_terms)

    if any(term in query for term in ["每日", "每天", "按日", "每日"]):
        output_grain = "daily"
    elif any(term in query for term in ["每月", "按月"]):
        output_grain = "monthly"
    elif any(term in query for term in ["工段", "牌号", "品牌", "机台", "设备", "按"]):
        if any(term in query for term in group_terms):
            output_grain = "by_dimension"

    top_ranking = bool(re.search(r"(?i)top\s*\d+|前\s*\d+|(?:最高|最低|最多|最少|最长|最短).{0,4}\d+|排名", query))
    return {
        "allow_group_by": (accuracy_mode() and top_ranking) or any(term in query for term in group_terms)
        and not (any(term in query for term in no_group_terms) and not any(term in query for term in ["各", "按", "每"])),
        "has_day": has_day,
        "has_month": has_month,
        "metric_type": "mixed"
        if has_duration_metric and has_count_metric
        else "duration"
        if has_duration_metric
        else "count"
        if has_count_metric
        else "other",
        "output_grain": output_grain,
    }


def text_traits(text: str) -> Dict[str, Any]:
    text = text or ""
    return {
        "has_group_by": bool(re.search(r"\bgroup\s+by\b", text, re.I)),
        "has_limit": bool(re.search(r"\blimit\s+\d+", text, re.I)),
        "has_comment": "--" in text or "/*" in text or "*/" in text,
        "uses_duration": bool(re.search(r"timestampdiff|timediff|/\s*3600|/\s*60|hour\s*\(|minute\s*\(", text, re.I)),
        "uses_count": bool(re.search(r"\bcount\s*\(", text, re.I)),
        "uses_day_filter": bool(re.search(r"\bdate\s*\(|'\d{4}-\d{2}-\d{2}'", text, re.I)),
        "uses_month_filter": bool(re.search(r"\byear\s*\(|\bmonth\s*\(|'\d{4}-\d{2}-01'", text, re.I)),
    }


def example_matches_query(query: str, example_text: str) -> bool:
    query_info = query_traits(query)
    text_info = text_traits(example_text)
    if accuracy_mode():
        if simple_aggregate(query) and record_count(query):
            return not text_info["has_group_by"]
        return True
    if not query_info["allow_group_by"] and text_info["has_group_by"]:
        return False
    if query_info["has_month"] and not query_info["has_day"] and re.search(r"\d{4}-\d{2}-\d{2}", example_text):
        return False
    if query_info["metric_type"] == "duration" and text_info["uses_count"] and not text_info["uses_duration"]:
        return False
    if query_info["metric_type"] == "count" and text_info["uses_duration"] and not text_info["uses_count"]:
        return False
    return True


def review_sql_plan(plan: Dict[str, Any], query: str) -> Dict[str, Any]:
    traits = query_traits(query)
    notes = list(plan.get("notes") or [])
    if not traits["allow_group_by"] and plan.get("group_by"):
        notes.append("Removed group_by because the user did not request grouped/detail output.")
        plan["group_by"] = []
    if not re.search(r"(?i)\btop\s*\d+|前\s*\d+|最新|最大|最小|最高|最低|最多|最少|最长|最短|排名", query) and plan.get("top_n"):
        notes.append("Removed top_n because the user did not request TopN/latest/ranking.")
        plan["top_n"] = None
    if traits["has_month"] and not traits["has_day"]:
        filters = plan.get("filters")
        if isinstance(filters, list):
            plan["filters"] = [
                item
                for item in filters
                if not re.search(r"\d{4}-\d{2}-\d{2}.*\d{4}-\d{2}-\d{2}", json.dumps(item, ensure_ascii=False))
            ] or filters
        notes.append("Monthly query: date filters must cover the full month, not a single day inherited from examples.")
    plan["notes"] = notes
    return plan


def schema_table_columns(state: NL2SQLState) -> Dict[str, set]:
    table_columns: Dict[str, set] = {}
    parsed = safe_json_loads(state.get("domain_schema", ""), {})
    for table in parsed.get("tables", []) if isinstance(parsed, dict) else []:
        name = table.get("name")
        if not name:
            continue
        cols = set()
        for key in ("columns", "time_columns", "join_keys"):
            value = table.get(key, [])
            if isinstance(value, list):
                cols.update(str(col).strip("`") for col in value if col)
        table_columns[str(name).lower()] = {col.lower() for col in cols}

    schema_text = "\n".join([state.get("full_db_schema", ""), state.get("db_knowledge", "")])
    for table_name, table_info in parse_full_schema_catalog(schema_text).items():
        cols = table_columns.setdefault(table_name, set())
        cols.update(column.lower() for column in table_info.get("columns", []))
    return table_columns


def sql_alias_table_map(sql: str) -> Dict[str, str]:
    alias_map: Dict[str, str] = {}
    pattern = re.compile(r"(?i)\b(?:from|join)\s+`?([a-zA-Z0-9_]+)`?(?:\s+(?:as\s+)?`?([a-zA-Z_][A-Za-z0-9_]*)`?)?")
    for table, alias in pattern.findall(sql):
        alias_map[table.lower()] = table.lower()
        if alias and alias.lower() not in {"on", "where", "left", "right", "inner", "join"}:
            alias_map[alias.lower()] = table.lower()
    return alias_map
