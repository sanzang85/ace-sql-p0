from __future__ import annotations
from .intent_contracts import accuracy_mode, intent_guidance
import json
from typing import Any, Dict
from langchain_core.prompts import ChatPromptTemplate
from .analysis import review_sql_plan
from .config import CUT_BATCH_DETERMINISTIC_FASTPATH, TEMPLATE_LLM_ONLY_MODE
from .domain_query_hints import domain_specific_guardrails, query_family_fact_metadata, query_family_forbidden_fact_tables, query_family_primary_fact_tables, query_family_secondary_fact_tables
from .llm_client import llm
from .state import NL2SQLState
from .text_utils import condense_context, parse_json_with_diagnostics
PLAN_BUILDER_MAX_TOKENS = 4096 if accuracy_mode() else 1024

def _as_clean_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif value in (None, ''):
        raw_items = []
    else:
        raw_items = [value]
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item).strip()
        normalized = text.lower()
        if not text or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(text)
    return cleaned

def _normalize_plan_structure(state: NL2SQLState, plan: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(plan) if isinstance(plan, dict) else {}
    if accuracy_mode():
        from .query_evidence import reconcile_table_spelling
        from .schema_catalog import selected_schema_columns
        normalized = reconcile_table_spelling(normalized, selected_schema_columns(state.get('domain_schema', '')))
    fact_tables = _as_clean_list(normalized.get('fact_tables', []))
    dimension_tables = _as_clean_list(normalized.get('dimension_tables', []))
    metadata = query_family_fact_metadata(state)
    primary_candidates = query_family_primary_fact_tables(state)
    secondary_candidates = query_family_secondary_fact_tables(state)
    forbidden_candidates = query_family_forbidden_fact_tables(state)
    primary_fact_table = str(normalized.get('primary_fact_table', '')).strip()
    if primary_fact_table and primary_fact_table not in fact_tables:
        fact_tables.insert(0, primary_fact_table)
    if not primary_fact_table:
        for candidate in primary_candidates:
            if candidate in fact_tables:
                primary_fact_table = candidate
                break
        if not primary_fact_table and primary_candidates:
            primary_fact_table = primary_candidates[0]
            if primary_fact_table not in fact_tables:
                fact_tables.insert(0, primary_fact_table)
        if not primary_fact_table and fact_tables:
            primary_fact_table = fact_tables[0]
    secondary_fact_tables = _as_clean_list(normalized.get('secondary_fact_tables', []))
    if not secondary_fact_tables:
        secondary_fact_tables = [name for name in secondary_candidates if name != primary_fact_table]
    if primary_fact_table:
        secondary_fact_tables = [name for name in secondary_fact_tables if name != primary_fact_table]
    metric_grain = str(normalized.get('metric_grain', '') or metadata.get('metric_grain', '') or state.get('metric_grain', '')).strip()
    time_grain = str(normalized.get('time_grain', '') or metadata.get('time_grain', '') or state.get('time_grain', '')).strip()
    business_semantics = str(normalized.get('business_semantics', '') or metadata.get('business_semantics', '') or state.get('business_semantics', '')).strip()
    aggregation_scope = str(normalized.get('aggregation_scope', '') or metadata.get('aggregation_scope', '') or state.get('aggregation_scope', '')).strip()
    fact_table_conflict_notes = str(normalized.get('fact_table_conflict_notes', '')).strip()
    if not fact_table_conflict_notes:
        note_parts: list[str] = []
        if primary_fact_table:
            note_parts.append(f'Prefer primary fact table {primary_fact_table}.')
        if secondary_fact_tables:
            note_parts.append(f"Use supporting tables only when needed: {', '.join(secondary_fact_tables)}.")
        if forbidden_candidates:
            note_parts.append(f"Avoid conflicting fact tables: {', '.join(forbidden_candidates)}.")
        fact_table_conflict_notes = ' '.join(note_parts).strip()
    normalized['fact_tables'] = fact_tables
    normalized['dimension_tables'] = dimension_tables
    normalized['time_field_candidates'] = _as_clean_list(normalized.get('time_field_candidates', []))
    normalized['filters'] = _as_clean_list(normalized.get('filters', []))
    metrics = normalized.get('metrics', [])
    if isinstance(metrics, list):
        metrics = [item.get('expression', '') if isinstance(item, dict) else item for item in metrics]
    normalized['metrics'] = _as_clean_list(metrics)
    normalized['group_by'] = _as_clean_list(normalized.get('group_by', []))
    normalized['order_by'] = _as_clean_list(normalized.get('order_by', []))
    normalized['output_columns'] = _as_clean_list(normalized.get('output_columns', []))
    normalized['join_paths'] = _as_clean_list(normalized.get('join_paths', []))
    standards = normalized.get('quality_standards', [])
    normalized['quality_standards'] = standards if isinstance(standards, list) else []
    normalized['notes'] = _as_clean_list(normalized.get('notes', []))
    normalized['primary_fact_table'] = primary_fact_table
    normalized['secondary_fact_tables'] = secondary_fact_tables
    normalized['metric_grain'] = metric_grain
    normalized['time_grain'] = time_grain
    normalized['business_semantics'] = business_semantics
    normalized['aggregation_scope'] = aggregation_scope
    normalized['fact_table_conflict_notes'] = fact_table_conflict_notes
    return normalized

def _build_plan_result(state: NL2SQLState, plan: dict[str, Any], *, raw_response: str, parse_error: str='', used_fallback: bool=False) -> Dict:
    normalized_plan = _normalize_plan_structure(state, review_sql_plan(plan, state.get('normalized_query', state['query'])))
    return {'planner_raw_response': raw_response, 'planner_parse_error': parse_error or '', 'planner_used_fallback': used_fallback, 'sql_plan': json.dumps(normalized_plan, ensure_ascii=False, indent=2), 'primary_fact_table': normalized_plan.get('primary_fact_table', ''), 'secondary_fact_tables': normalized_plan.get('secondary_fact_tables', []), 'metric_grain': normalized_plan.get('metric_grain', ''), 'time_grain': normalized_plan.get('time_grain', ''), 'business_semantics': normalized_plan.get('business_semantics', ''), 'aggregation_scope': normalized_plan.get('aggregation_scope', ''), 'fact_table_conflict_notes': normalized_plan.get('fact_table_conflict_notes', ''), 'plan_validation_errors': [], 'plan_validation_passed': False}

def build_sql_plan(state: NL2SQLState) -> Dict:
    print('-> build_sql_plan')
    query = state.get('normalized_query', state['query'])
    prompt = 'You are an NL2SQL planner.\nDo not write a full SQL query yet. Produce a structured query plan first.\nReview every requested output, grouping grain, and required schema join key.\nHistorical examples provide schema relationships and established result contracts.\nFor the same requested business result, preserve the example\'s contextual output fields,\naliases and column order unless the user explicitly narrows or changes the output.\nUse the current request for dates, entities, limits and metrics; never inherit an unrelated result shape.\n\nRules:\n1. Output JSON only.\n2. Format must be:\n   {{\n     "domain": "",\n     "fact_tables": [],\n     "dimension_tables": [],\n     "primary_fact_table": "",\n     "secondary_fact_tables": [],\n     "metric_grain": "",\n     "time_grain": "",\n     "business_semantics": "",\n     "aggregation_scope": "",\n     "fact_table_conflict_notes": "",\n     "time_field_candidates": [],\n     "filters": [],\n     "metrics": [],\n     "group_by": [],\n     "order_by": [],\n     "top_n": null,\n     "output_columns": [],\n     "join_paths": [],\n     "quality_standards": [],\n     "notes": []\n   }}\n3. Use only tables and fields that exist in the provided schema.\n4. Use concrete field names, not guessed aliases such as Plan_Date or Result_Value.\n5. If the query has time constraints, provide real time_field_candidates from schema.\n6. If aggregate metrics and dimension outputs appear together, ensure group_by is not empty.\n7. primary_fact_table must be one of fact_tables and should be the dominant business fact source.\n8. secondary_fact_tables should list only supporting fact/dimension tables that are actually needed.\n9. Respect the provided fact-table preference, forbidden-table list, metric_grain, time_grain, and business_semantics.\n10. If previous plan validation feedback exists, minimally repair the previous plan instead of inventing a brand-new plan.\n11. Do not output analysis, chain-of-thought, or any prose before/after the JSON.\n    notes must contain at most three brief factual phrases, never an internal debate.\n    Use full table.column names in this plan; SQL aliases belong to the later SQL stage.\n12. The first character of your response must be {{ and the last character must be }}.\n13. metrics must contain SQL expression strings, e.g. ["COUNT(table.id)"], not metric names.\n    When a lifecycle contract is provided, repeat its expressions and grain exactly;\n    use its required result column aliases in output_columns.\n\nUser query:\n{query}\n\nWhole historical examples:\n{domain_examples}\n\nHeuristic rules:\n{enriched_rules}\n\nDomain hint:\n{domain_hint}\n\nExpanded domain schema:\n{domain_schema}\n\nDomain rules:\n{domain_rules}\n\nField mapping hints:\n{field_mapping_hints}\n\nDomain-specific guardrails:\n{domain_specific_hints}\n\nFact-table and grain preferences:\n{family_fact_metadata}\n\nPrevious plan validation feedback:\n{plan_feedback}\n\nPrevious plan to repair if needed:\n{prior_sql_plan}\n'
    response = (ChatPromptTemplate.from_template(prompt) | llm.bind(max_tokens=PLAN_BUILDER_MAX_TOKENS)).invoke({'query': query, 'domain_examples': state.get('domain_examples', '')[:18000] if accuracy_mode() else '', 'enriched_rules': state.get('enriched_rules', '') if accuracy_mode() else condense_context(query, state.get('enriched_rules', ''), max_chars=1600, chunk_size=600, overlap=60, max_chunks=3), 'domain_hint': condense_context(query, state.get('domain_hint', ''), max_chars=600, chunk_size=300, overlap=40, max_chunks=2), 'domain_schema': state.get('domain_schema', '') if accuracy_mode() else condense_context(query, state.get('domain_schema', ''), max_chars=9000, chunk_size=1400, overlap=120, max_chunks=7), 'domain_rules': state.get('domain_rules', '')[:30000] if accuracy_mode() else condense_context(query, state.get('domain_rules', ''), max_chars=2800, chunk_size=900, overlap=80, max_chunks=4), 'field_mapping_hints': state.get('field_mapping_hints', '') if accuracy_mode() else condense_context(query, state.get('field_mapping_hints', ''), max_chars=2200, chunk_size=800, overlap=80, max_chunks=4), 'domain_specific_hints': condense_context(query, domain_specific_guardrails(state), max_chars=2400, chunk_size=800, overlap=80, max_chunks=4), 'family_fact_metadata': condense_context(query, json.dumps({**query_family_fact_metadata(state), 'preferred_primary_fact_tables': query_family_primary_fact_tables(state), 'preferred_secondary_fact_tables': query_family_secondary_fact_tables(state), 'forbidden_fact_tables': query_family_forbidden_fact_tables(state), 'current_primary_fact_table': state.get('primary_fact_table', '')}, ensure_ascii=False, indent=2), max_chars=2200, chunk_size=800, overlap=80, max_chunks=4), 'plan_feedback': condense_context(query, state.get('plan_feedback', '') or 'None', max_chars=1400, chunk_size=600, overlap=60, max_chunks=3), 'prior_sql_plan': condense_context(query, state.get('sql_plan', '') or 'None', max_chars=2400, chunk_size=900, overlap=80, max_chunks=3)})
    raw_response = response.content if isinstance(response.content, str) else str(response.content)
    parsed, parse_error = parse_json_with_diagnostics(response.content, {})
    used_fallback = not isinstance(parsed, dict) or not parsed
    if used_fallback:
        parsed = {'domain': state.get('domain_hint', ''), 'fact_tables': [], 'dimension_tables': [], 'primary_fact_table': '', 'secondary_fact_tables': [], 'metric_grain': '', 'time_grain': '', 'business_semantics': '', 'aggregation_scope': '', 'fact_table_conflict_notes': '', 'time_field_candidates': [], 'filters': [], 'metrics': [], 'group_by': [], 'order_by': [], 'top_n': None, 'output_columns': [], 'join_paths': [], 'notes': ['planner fallback used']}
    return _build_plan_result(state, parsed, raw_response=raw_response, parse_error=parse_error or '', used_fallback=used_fallback)
