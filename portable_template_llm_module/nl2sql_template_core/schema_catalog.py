from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from .rule_catalog import high_frequency_field_mappings

HIGH_FREQUENCY_FIELD_MAPPINGS: dict[str, dict[str, Any]] = high_frequency_field_mappings()

TIME_COLUMN_PATTERN = re.compile(r"(date|time|day|month|year|calendar)", re.I)


def _normalize_name(name: str) -> str:
    return name.strip("` ").lower()


def _extract_column_comment(line: str) -> str:
    match = re.search(r"COMMENT\s+'([^']*)'", line, re.I)
    return match.group(1).strip() if match else ""


def infer_time_columns(columns: Iterable[str]) -> list[str]:
    return [column for column in columns if TIME_COLUMN_PATTERN.search(column)]


def infer_join_keys(columns: Iterable[str]) -> list[str]:
    keys: list[str] = []
    for column in columns:
        lower = column.lower()
        if (
            lower.endswith("_id")
            or lower in {"id", "batchcode", "batch_code", "billno", "relationbillno"}
            or lower.endswith("_code")
        ):
            keys.append(column)
    return keys


def parse_full_schema_catalog(schema_text: str) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    pattern = re.compile(
        r"(?is)create\s+table\s+`?([a-zA-Z0-9_]+)`?\s*\((.*?)\)\s*ENGINE=.*?(?:COMMENT='([^']*)')?\s*;",
    )
    for match in pattern.finditer(schema_text or ""):
        table_name = match.group(1).strip()
        body = match.group(2)
        table_comment = (match.group(3) or "").strip()
        columns: list[str] = []
        column_comments: dict[str, str] = {}
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            column_match = re.match(r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s+", line)
            if not column_match:
                continue
            column_name = column_match.group(1)
            if column_name.lower() in {"primary", "key", "unique", "constraint", "index"}:
                continue
            columns.append(column_name)
            column_comments[column_name] = _extract_column_comment(line)
        lower_name = table_name.lower()
        catalog[lower_name] = {
            "name": table_name,
            "comment": table_comment,
            "columns": columns,
            "column_comments": column_comments,
            "time_columns": infer_time_columns(columns),
            "join_keys": infer_join_keys(columns),
        }
    return catalog


def format_schema_catalog(catalog: dict[str, dict[str, Any]], *, detailed: bool = False) -> str:
    lines: list[str] = []
    for table_name in sorted(catalog):
        table = catalog[table_name]
        columns = table["columns"]
        preview_columns = ", ".join(columns[:16])
        if len(columns) > 16:
            preview_columns += ", ..."
        if detailed:
            preview_columns = "; ".join(
                name + (": " + table["column_comments"][name] if table["column_comments"].get(name) else "")
                for name in columns
            )
        time_columns = ", ".join(table["time_columns"][:6]) or "-"
        join_keys = ", ".join(table["join_keys"][:8]) or "-"
        comment = table.get("comment") or "-"
        lines.append(
            "\n".join(
                [
                    f"Table: {table['name']}",
                    f"Comment: {comment}",
                    f"Time columns: {time_columns}",
                    f"Join keys: {join_keys}",
                    f"Columns: {preview_columns}",
                ]
            )
        )
    return "\n\n".join(lines)


def expand_selected_schema(selection: dict[str, Any], full_schema_text: str) -> tuple[dict[str, Any], list[str]]:
    catalog = parse_full_schema_catalog(full_schema_text)
    selected_tables = selection.get("tables", []) if isinstance(selection, dict) else []
    expanded_tables: list[dict[str, Any]] = []
    missing_tables: list[str] = []
    seen: set[str] = set()

    for item in selected_tables:
        if isinstance(item, dict):
            raw_name = str(item.get("name", "")).strip()
            role = str(item.get("role", "fact") or "fact")
        else:
            raw_name = str(item).strip()
            role = "fact"
        if not raw_name:
            continue
        normalized = _normalize_name(raw_name)
        if normalized in seen:
            continue
        seen.add(normalized)
        table_info = catalog.get(normalized)
        if not table_info:
            missing_tables.append(raw_name)
            continue
        expanded_tables.append(
            {
                "name": table_info["name"],
                "role": role,
                "comment": table_info.get("comment", ""),
                "columns": list(table_info["columns"]),
                "time_columns": list(table_info["time_columns"]),
                "join_keys": list(table_info["join_keys"]),
            }
        )

    notes = list(selection.get("notes", [])) if isinstance(selection, dict) else []
    if missing_tables:
        notes.append("Some selected tables were not found in the full schema and were dropped.")

    expanded = {
        "tables": expanded_tables,
        "joins": list(selection.get("joins", [])) if isinstance(selection, dict) else [],
        "notes": notes,
    }
    return expanded, missing_tables


def selected_schema_columns(domain_schema_text: str) -> dict[str, set[str]]:
    try:
        import json

        parsed = json.loads(domain_schema_text) if domain_schema_text.strip() else {}
    except Exception:
        parsed = {}
    table_columns: dict[str, set[str]] = {}
    for table in parsed.get("tables", []) if isinstance(parsed, dict) else []:
        table_name = _normalize_name(str(table.get("name", "")))
        if not table_name:
            continue
        columns = {str(column).strip("`").lower() for column in table.get("columns", []) if column}
        table_columns[table_name] = columns
    return table_columns


def available_columns_for_selected_schema(domain_schema_text: str) -> set[str]:
    columns: set[str] = set()
    for table_columns in selected_schema_columns(domain_schema_text).values():
        columns.update(table_columns)
    return columns


def mapped_field_candidates(field_name: str, domain_schema_text: str) -> list[str]:
    mapping = HIGH_FREQUENCY_FIELD_MAPPINGS.get(field_name)
    if not mapping:
        return []
    selected_columns = available_columns_for_selected_schema(domain_schema_text)
    candidates = mapping.get("candidates", [])
    matched = [candidate for candidate in candidates if candidate.lower() in selected_columns]
    return matched or candidates


def mapped_field_message(field_name: str, domain_schema_text: str) -> str | None:
    mapping = HIGH_FREQUENCY_FIELD_MAPPINGS.get(field_name)
    if not mapping:
        return None
    candidates = mapped_field_candidates(field_name, domain_schema_text)
    reason = str(mapping.get("reason", "")).strip()
    candidate_text = ", ".join(candidates)
    if candidate_text and reason:
        return f"Suggested replacements: {candidate_text}. {reason}"
    if candidate_text:
        return f"Suggested replacements: {candidate_text}."
    return reason or None


def relevant_field_mapping_hints(domain_schema_text: str) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for wrong_field in HIGH_FREQUENCY_FIELD_MAPPINGS:
        candidates = mapped_field_candidates(wrong_field, domain_schema_text)
        if not candidates:
            continue
        hints.append(
            {
                "wrong_field": wrong_field,
                "suggested_fields": candidates,
                "reason": HIGH_FREQUENCY_FIELD_MAPPINGS[wrong_field].get("reason", ""),
            }
        )
    return hints


def render_field_mapping_hints(domain_schema_text: str) -> str:
    hints = relevant_field_mapping_hints(domain_schema_text)
    if not hints:
        return ""
    lines = []
    for hint in hints:
        lines.append(
            f"- {hint['wrong_field']} -> {', '.join(hint['suggested_fields'])} | {hint['reason']}"
        )
    return "\n".join(lines)
