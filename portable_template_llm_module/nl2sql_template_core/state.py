from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict
from uuid import uuid4


class NL2SQLState(TypedDict):
    query: str
    normalized_query: str
    data_dictionary: str
    db_knowledge: str
    full_db_schema: str
    enriched_rules: str
    domain_hint: str
    domain_schema: str
    domain_rules: str
    domain_examples: str
    field_mapping_hints: str
    primary_fact_table: str
    secondary_fact_tables: List[str]
    metric_grain: str
    time_grain: str
    business_semantics: str
    aggregation_scope: str
    fact_table_conflict_notes: str
    vector_recall_notes: str
    vector_domain_hint: str
    vector_schema_recall: str
    vector_template_recall: str
    vector_field_alias_recall: str
    sql_plan: str
    plan_validation_errors: List[str]
    plan_validation_passed: bool
    plan_feedback: str
    plan_rebuild_count: int
    schema_retry_count: int
    schema_retry_requested: bool
    schema_retry_reason: str
    schema_expansion_mode: str
    schema_links: str
    keywords: List[str]
    code_exec_results: List[Dict[str, Any]]
    iteration_results: List[str]
    evidence_docs: str
    generated_evidence: str
    few_shot_examples: str
    planner_raw_response: str
    planner_parse_error: str
    planner_used_fallback: bool
    generated_sql: str
    validated_sql: str
    validation_errors: List[str]
    validation_passed: bool
    precheck_passed: bool
    precheck_error_message: Optional[str]
    precheck_sql: str
    precheck_execution_result: str
    precheck_raw_execution_data: List[Dict[str, Any]]
    repair_attempt_count: int
    result_check_notes: str
    result_check_passed: bool
    result_check_repair_attempted: bool
    repair_notes: str
    needs_rerun: bool
    execution_repair_attempted: bool
    final_execution_result: str
    raw_execution_data: List[Dict[str, Any]]
    error_message: Optional[str]
    lifecycle_request_id: str
    lifecycle_template: str
    lifecycle_template_version: int
    lifecycle_match_score: float
    lifecycle_exploration: bool
    lifecycle_event_id: str
    lifecycle_values: str
    query_evidence: str
    schema_selection_suggestions: str
    schema_evidence_events: List[Dict[str, Any]]
    verified_query_values: List[Dict[str, Any]]
    semantic_review_notes: str


def initial_state(query: str) -> NL2SQLState:
    return {
        "query": query,
        "normalized_query": "",
        "data_dictionary": "",
        "db_knowledge": "",
        "full_db_schema": "",
        "enriched_rules": "",
        "domain_hint": "",
        "domain_schema": "",
        "domain_rules": "",
        "domain_examples": "",
        "field_mapping_hints": "",
        "primary_fact_table": "",
        "secondary_fact_tables": [],
        "metric_grain": "",
        "time_grain": "",
        "business_semantics": "",
        "aggregation_scope": "",
        "fact_table_conflict_notes": "",
        "vector_recall_notes": "",
        "vector_domain_hint": "",
        "vector_schema_recall": "",
        "vector_template_recall": "",
        "vector_field_alias_recall": "",
        "sql_plan": "",
        "plan_validation_errors": [],
        "plan_validation_passed": False,
        "plan_feedback": "",
        "plan_rebuild_count": 0,
        "schema_retry_count": 0,
        "schema_retry_requested": False,
        "schema_retry_reason": "",
        "schema_expansion_mode": "normal",
        "schema_links": "",
        "keywords": [],
        "code_exec_results": [],
        "iteration_results": [],
        "evidence_docs": "",
        "generated_evidence": "",
        "few_shot_examples": "",
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
        "repair_notes": "",
        "needs_rerun": False,
        "execution_repair_attempted": False,
        "final_execution_result": "",
        "raw_execution_data": [],
        "error_message": None,
        "lifecycle_request_id": uuid4().hex,
        "lifecycle_template": "",
        "lifecycle_template_version": 0,
        "lifecycle_match_score": 0.0,
        "lifecycle_exploration": False,
        "lifecycle_event_id": "",
        "lifecycle_values": "[]",
        "query_evidence": "",
        "schema_selection_suggestions": "",
        "schema_evidence_events": [],
        "verified_query_values": [],
        "semantic_review_notes": "",
    }
