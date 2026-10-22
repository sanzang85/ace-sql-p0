from __future__ import annotations

import re
from .intent_contracts import accuracy_mode

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .config import PLAN_VALIDATION_MAX_RETRIES, SCHEMA_RETRY_MAX_RETRIES, SQL_PRECHECK_MAX_RETRIES
from .domain import (
    domain_router,
    enrich_rules,
    normalize_query,
    retrieve_domain_examples,
    retrieve_domain_schema,
    retrieve_domain_rules,
)
from .execution import (
    final_sql_execution,
    finalize_plan_failure,
    finalize_result_check_failure,
    finalize_sql_failure,
    result_check,
)
from .generation import (
    generate_sql_from_plan,
    repair_sql,
    repair_sql_after_execution,
    repair_sql_after_result_check,
)
from .query_evidence import enrich_query_evidence, review_generated_sql
from .lifecycle import feature_enabled
from .lifecycle_runtime import record_template_event, select_template, recall_template_values, observe_repair
from .knowledge import load_data_dictionary, retrieve_db_knowledge
from .plan_validation import validate_sql_plan
from .planning import build_sql_plan
from .state import NL2SQLState
from .trace import trace_node
from .validation import validate_sql
from .vector_retrieval import recall_vector_knowledge


def _normalized_text(parts: list[str]) -> str:
    text = "\n".join(part for part in parts if part).strip().lower()
    return text


def _schema_retry_available(state: NL2SQLState) -> bool:
    return state.get("schema_retry_count", 0) < SCHEMA_RETRY_MAX_RETRIES


def _should_retry_schema_from_plan(state: NL2SQLState) -> bool:
    if not _schema_retry_available(state):
        return False
    text = _normalized_text(
        [
            "\n".join(state.get("plan_validation_errors", [])),
            state.get("plan_feedback", ""),
            state.get("planner_parse_error", ""),
        ]
    )
    markers = [
        "unselected table",
        "selected schema",
        "missing required tables",
        "planner fallback",
        "time field references an unselected table",
        "time field is not in selected table schema",
    ]
    return any(marker in text for marker in markers)


def _should_retry_schema_from_validation(state: NL2SQLState) -> bool:
    if not _schema_retry_available(state):
        return False
    text = _normalized_text(
        [
            "\n".join(state.get("validation_errors", [])),
            state.get("precheck_error_message") or "",
        ]
    )
    markers = [
        "table is not in candidate schema",
        "column is not in candidate schema",
        "column ownership error",
        "preview execution failed: unknown column",
        "preview execution failed: table",
        "must use",
        "required tables",
        "high-risk guessed field",
    ]
    return any(marker in text for marker in markers)


def _should_retry_schema_from_execution(state: NL2SQLState) -> bool:
    if not _schema_retry_available(state):
        return False
    text = _normalized_text([state.get("error_message") or ""])
    markers = [
        "unknown column",
        "doesn't exist",
        "lost connection",
        "timed out",
    ]
    return any(marker in text for marker in markers)


def _should_retry_schema_from_result_check(state: NL2SQLState) -> bool:
    if not _schema_retry_available(state):
        return False
    text = _normalized_text([state.get("result_check_notes", "")])
    markers = [
        "returned no rows",
        "missing unit column",
        "missing grouping column",
        "detail-like query collapsed",
        "suspicious rate",
        "likely chose the wrong fact table",
    ]
    return any(marker in text for marker in markers)


def _schema_retry_reason(state: NL2SQLState) -> str:
    for label, text in [
        ("plan", "\n".join(state.get("plan_validation_errors", [])) or state.get("plan_feedback", "")),
        ("validation", "\n".join(state.get("validation_errors", [])) or (state.get("precheck_error_message") or "")),
        ("execution", state.get("error_message") or ""),
        ("result_check", state.get("result_check_notes", "")),
    ]:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if cleaned:
            if len(cleaned) > 220:
                cleaned = cleaned[:220] + "..."
            return f"{label}: {cleaned}"
    return "schema retry requested after repeated downstream failure"


def prepare_schema_retry(state: NL2SQLState) -> dict:
    print("-> prepare_schema_retry")
    reason = _schema_retry_reason(state)
    prior_feedback = state.get("plan_feedback", "").strip()
    combined_feedback = f"{prior_feedback}\nSchema retry reason: {reason}".strip() if prior_feedback else f"Schema retry reason: {reason}"
    if accuracy_mode():
        combined_feedback += '\n' + '\n'.join(state.get('validation_errors', []) + state.get('plan_validation_errors', []))[:6000]
    return {
        "schema_retry_count": state.get("schema_retry_count", 0) + 1,
        "schema_retry_requested": False,
        "schema_retry_reason": reason,
        "schema_expansion_mode": "broad",
        "domain_schema": "",
        "domain_rules": "",
        "domain_examples": "",
        "field_mapping_hints": "",
        "schema_links": "",
        "sql_plan": "",
        "plan_validation_errors": [],
        "plan_validation_passed": False,
        "plan_feedback": combined_feedback,
        "plan_rebuild_count": 0,
        "planner_raw_response": "",
        "planner_parse_error": "",
        "planner_used_fallback": False,
        "generated_sql": "",
        "validated_sql": "",
        "validation_errors": [],
        "validation_passed": False,
        "precheck_passed": False,
        "precheck_error_message": None,
        "precheck_sql": "",
        "precheck_execution_result": "",
        "precheck_raw_execution_data": [],
        "repair_attempt_count": 0,
        "result_check_notes": "",
        "result_check_passed": False,
        "result_check_repair_attempted": False,
        "repair_notes": f"Reopened schema selection in broad mode. {reason}",
        "needs_rerun": False,
        "execution_repair_attempted": False,
        "final_execution_result": "",
        "raw_execution_data": [],
        "error_message": None,
    }


def route_after_validate_plan(state: NL2SQLState) -> str:
    if state.get("plan_validation_passed"):
        return "generate_sql"
    if state.get("plan_rebuild_count", 0) <= PLAN_VALIDATION_MAX_RETRIES:
        return "build_sql_plan"
    if _should_retry_schema_from_plan(state):
        return "prepare_schema_retry"
    return "finalize_plan_failure"


def route_after_validate_sql(state: NL2SQLState) -> str:
    if state.get("validation_passed") and state.get("precheck_passed"):
        return "sql_execution"
    if accuracy_mode() and _schema_retry_available(state) and any('Table is not in candidate schema:' in error for error in state.get('validation_errors', [])):
        return "prepare_schema_retry"
    if _should_retry_schema_from_validation(state) and state.get("repair_attempt_count", 0) >= 1:
        return "prepare_schema_retry"
    if state.get("repair_attempt_count", 0) < SQL_PRECHECK_MAX_RETRIES:
        return "repair_sql"
    if _should_retry_schema_from_validation(state):
        return "prepare_schema_retry"
    return "finalize_sql_failure"


def route_after_sql_execution(state: NL2SQLState) -> str:
    if state.get("error_message") and not state.get("execution_repair_attempted", False):
        return "repair_sql_after_execution"
    if state.get("error_message") and _should_retry_schema_from_execution(state):
        return "prepare_schema_retry"
    return "result_check"


def route_after_result_check(state: NL2SQLState) -> str:
    if state.get("result_check_passed"):
        return "record_template_event" if feature_enabled() else END
    if not state.get("result_check_repair_attempted", False) and not state.get("error_message"):
        return "repair_sql_after_result_check"
    if _should_retry_schema_from_result_check(state):
        return "prepare_schema_retry"
    return "finalize_result_check_failure"


def build_workflow():
    workflow = StateGraph(NL2SQLState)
    workflow.add_node("normalize_query", trace_node("normalize_query", normalize_query))
    workflow.add_node("load_data_dict", trace_node("load_data_dict", load_data_dictionary))
    workflow.add_node("rule_enrich", trace_node("rule_enrich", enrich_rules))
    workflow.add_node("domain_router", trace_node("domain_router", domain_router))
    workflow.add_node("db_knowledge", trace_node("db_knowledge", retrieve_db_knowledge))
    workflow.add_node("vector_recall", trace_node("vector_recall", recall_vector_knowledge))
    workflow.add_node("select_lifecycle_template", trace_node("select_lifecycle_template", select_template))
    workflow.add_node("recall_template_values", trace_node("recall_template_values", recall_template_values))
    workflow.add_node("record_template_event", trace_node("record_template_event", record_template_event))
    workflow.add_node("retrieve_domain_schema", trace_node("retrieve_domain_schema", retrieve_domain_schema))
    workflow.add_node("retrieve_domain_rules", trace_node("retrieve_domain_rules", retrieve_domain_rules))
    workflow.add_node("retrieve_domain_examples", trace_node("retrieve_domain_examples", retrieve_domain_examples))
    workflow.add_node("enrich_query_evidence", trace_node("enrich_query_evidence", enrich_query_evidence))
    workflow.add_node("review_sql_semantics", trace_node("review_sql_semantics", review_generated_sql))
    workflow.add_node("build_sql_plan", trace_node("build_sql_plan", build_sql_plan))
    workflow.add_node("validate_plan", trace_node("validate_plan", validate_sql_plan))
    workflow.add_node("generate_sql", trace_node("generate_sql", generate_sql_from_plan))
    workflow.add_node("validate_sql", trace_node("validate_sql", validate_sql))
    workflow.add_node("repair_sql", trace_node("repair_sql", observe_repair("repair_sql", repair_sql)))
    workflow.add_node("repair_sql_after_execution", trace_node("repair_sql_after_execution", observe_repair("repair_sql_after_execution", repair_sql_after_execution)))
    workflow.add_node("repair_sql_after_result_check", trace_node("repair_sql_after_result_check", observe_repair("repair_sql_after_result_check", repair_sql_after_result_check)))
    workflow.add_node("prepare_schema_retry", trace_node("prepare_schema_retry", prepare_schema_retry))
    workflow.add_node("sql_execution", trace_node("sql_execution", final_sql_execution))
    workflow.add_node("result_check", trace_node("result_check", result_check))
    workflow.add_node("finalize_plan_failure", trace_node("finalize_plan_failure", finalize_plan_failure))
    workflow.add_node("finalize_sql_failure", trace_node("finalize_sql_failure", finalize_sql_failure))
    workflow.add_node("finalize_result_check_failure", trace_node("finalize_result_check_failure", finalize_result_check_failure))

    workflow.add_edge(START, "normalize_query")
    workflow.add_edge("normalize_query", "load_data_dict")
    workflow.add_edge("load_data_dict", "rule_enrich")
    workflow.add_edge("rule_enrich", "domain_router")
    workflow.add_edge("domain_router", "db_knowledge")
    workflow.add_edge("db_knowledge", "select_lifecycle_template")
    workflow.add_edge("select_lifecycle_template", "vector_recall")
    workflow.add_edge("vector_recall", "recall_template_values")
    workflow.add_edge("recall_template_values", "retrieve_domain_schema")
    workflow.add_edge("recall_template_values", "retrieve_domain_rules")
    workflow.add_edge("recall_template_values", "retrieve_domain_examples")
    workflow.add_edge(["retrieve_domain_schema", "retrieve_domain_rules", "retrieve_domain_examples"], "enrich_query_evidence")
    workflow.add_edge("enrich_query_evidence", "build_sql_plan")
    workflow.add_edge("build_sql_plan", "validate_plan")
    workflow.add_conditional_edges(
        "validate_plan",
        route_after_validate_plan,
        {
            "build_sql_plan": "build_sql_plan",
            "generate_sql": "generate_sql",
            "prepare_schema_retry": "prepare_schema_retry",
            "finalize_plan_failure": "finalize_plan_failure",
        },
    )
    workflow.add_edge("generate_sql", "review_sql_semantics")
    workflow.add_edge("review_sql_semantics", "validate_sql")
    workflow.add_conditional_edges(
        "validate_sql",
        route_after_validate_sql,
        {
            "repair_sql": "repair_sql",
            "sql_execution": "sql_execution",
            "prepare_schema_retry": "prepare_schema_retry",
            "finalize_sql_failure": "finalize_sql_failure",
        },
    )
    workflow.add_edge("repair_sql", "validate_sql")
    workflow.add_conditional_edges(
        "sql_execution",
        route_after_sql_execution,
        {
            "repair_sql_after_execution": "repair_sql_after_execution",
            "prepare_schema_retry": "prepare_schema_retry",
            "result_check": "result_check",
        },
    )
    workflow.add_edge("repair_sql_after_execution", "validate_sql")
    workflow.add_conditional_edges(
        "result_check",
        route_after_result_check,
        {
            END: END,
            "record_template_event": "record_template_event",
            "repair_sql_after_result_check": "repair_sql_after_result_check",
            "prepare_schema_retry": "prepare_schema_retry",
            "finalize_result_check_failure": "finalize_result_check_failure",
        },
    )
    workflow.add_edge("repair_sql_after_result_check", "validate_sql")
    workflow.add_edge("prepare_schema_retry", "retrieve_domain_schema")
    workflow.add_edge("prepare_schema_retry", "retrieve_domain_rules")
    workflow.add_edge("prepare_schema_retry", "retrieve_domain_examples")
    workflow.add_edge("finalize_sql_failure", "record_template_event")
    workflow.add_edge("finalize_result_check_failure", "record_template_event")
    workflow.add_edge("finalize_plan_failure", "record_template_event")
    workflow.add_edge("record_template_event", END)

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)
