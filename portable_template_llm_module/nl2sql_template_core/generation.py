from __future__ import annotations
from .intent_contracts import accuracy_mode
import re
from typing import Dict
from langchain_core.prompts import ChatPromptTemplate
from .config import CUT_BATCH_DETERMINISTIC_FASTPATH, LOW_RISK_TEMPLATE_MODE, TEMPLATE_LLM_ONLY_MODE
from .domain_query_hints import domain_specific_guardrails
from .llm_client import llm
from .state import NL2SQLState
from .text_utils import condense_context, extract_sql_text, safe_json_loads
from .lifecycle_runtime import correction_guidance
SQL_GENERATION_MAX_TOKENS = 4096 if accuracy_mode() else 1024

def _resolved_fact_constraints(state: NL2SQLState) -> str:
    parts: list[str] = []
    primary_fact_table = str(state.get('primary_fact_table', '')).strip()
    secondary_fact_tables = [str(item).strip() for item in state.get('secondary_fact_tables', []) if str(item).strip()]
    metric_grain = str(state.get('metric_grain', '')).strip()
    time_grain = str(state.get('time_grain', '')).strip()
    business_semantics = str(state.get('business_semantics', '')).strip()
    aggregation_scope = str(state.get('aggregation_scope', '')).strip()
    fact_table_conflict_notes = str(state.get('fact_table_conflict_notes', '')).strip()
    if primary_fact_table:
        parts.append(f'- primary_fact_table: {primary_fact_table}')
    if secondary_fact_tables:
        parts.append(f"- secondary_fact_tables: {', '.join(secondary_fact_tables)}")
    if metric_grain:
        parts.append(f'- metric_grain: {metric_grain}')
    if time_grain:
        parts.append(f'- time_grain: {time_grain}')
    if business_semantics:
        parts.append(f'- business_semantics: {business_semantics}')
    if aggregation_scope:
        parts.append(f'- aggregation_scope: {aggregation_scope}')
    if fact_table_conflict_notes:
        parts.append(f'- fact_table_conflict_notes: {fact_table_conflict_notes}')
    return '\n'.join(parts) or 'None'

def _split_top_level_csv(text: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quote = ''
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 1
                else:
                    quote = ''
            elif char == '\\':
                i += 1
        elif char in {"'", '"', '`'}:
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')' and depth:
            depth -= 1
        elif char == ',' and depth == 0:
            items.append(text[start:i])
            start = i + 1
        i += 1
    tail = text[start:]
    if tail.strip():
        items.append(tail)
    return items

def clean_generated_sql(sql_code: str, query: str) -> str:
    if any((keyword in query for keyword in ['ID', 'id', '主键', '编号', '编码'])):
        return sql_code
    match = re.search('(?i)SELECT\\s+(.*?)\\s+FROM', sql_code, re.DOTALL)
    if not match:
        return sql_code
    select_part = match.group(1)
    columns = _split_top_level_csv(select_part)
    kept = []
    for column in columns:
        column_text = column.strip()
        if 'RawMatPrepareDetail_ID' in column_text or '(' in column_text or '*' in column_text:
            kept.append(column)
        elif re.search('(?i)_ID\\b', column_text) or re.search('(?i)EntryOn\\b', column_text):
            continue
        else:
            kept.append(column)
    if not kept:
        return sql_code
    new_select_part = ', '.join(kept)
    return sql_code[:match.start(1)] + new_select_part + sql_code[match.end(1):]

def _route_domain(state: NL2SQLState) -> str:
    parsed = safe_json_loads(state.get('domain_hint', ''), {})
    if isinstance(parsed, dict):
        return str(parsed.get('domain', '')).strip().lower()
    return ''

def _semantic_repair_fastpath(state: NL2SQLState) -> str:
    return ''

def _validation_repair_fastpath(state: NL2SQLState) -> str:
    return ''

def _is_cut_batch_progress_query(state: NL2SQLState) -> bool:
    return False

def _domain_specific_hints(state: NL2SQLState) -> str:
    return ''

def generate_sql_from_plan(state: NL2SQLState) -> Dict:
    print('-> generate_sql_from_plan')
    query = state.get('normalized_query', state['query'])
    prompt = 'You are an industrial MySQL expert.\nGenerate exactly one executable MySQL query from the provided plan.\n\nRules:\n1. Output JSON only.\n2. Format must be {{"sql": "SELECT ..."}}.\n3. The SQL must be complete and executable.\n4. Do not output markdown, comments, placeholders, or explanations.\n5. Use only tables and columns that exist in the expanded domain schema.\n6. Follow the SQL plan closely.\n7. Respect field mapping hints and avoid guessed columns such as Plan_Date, Result_Value, Process_Date, IsTrial, and Test_Score unless they truly exist in schema.\n8. If aggregate metrics and dimension columns appear together, ensure GROUP BY is correct.\n9. Do not output analysis, chain-of-thought, or any prose before/after the JSON.\n10. The first character of your response must be {{ and the last character must be }}.\n\nUser query:\n{query}\n\nSQL plan:\n{sql_plan}\n\nPlan validation feedback:\n{plan_feedback}\n\nExpanded domain schema:\n{domain_schema}\n\nDomain rules:\n{domain_rules}\n\nField mapping hints:\n{field_mapping_hints}\n\nDomain-specific guardrails:\n{domain_specific_hints}\n\nResolved fact-table and grain constraints:\n{fact_constraints}\n\nReference examples:\n{domain_examples}\n'
    response = (ChatPromptTemplate.from_template(prompt) | llm.bind(max_tokens=SQL_GENERATION_MAX_TOKENS)).invoke({'query': query, 'sql_plan': state.get('sql_plan', '') if accuracy_mode() else condense_context(query, state.get('sql_plan', ''), max_chars=3600, chunk_size=1000, overlap=100, max_chunks=4), 'plan_feedback': condense_context(query, state.get('plan_feedback', '') or 'None', max_chars=1400, chunk_size=600, overlap=60, max_chunks=3), 'domain_schema': state.get('domain_schema', '') if accuracy_mode() else condense_context(query, state.get('domain_schema', ''), max_chars=8500, chunk_size=1400, overlap=120, max_chunks=7), 'domain_rules': state.get('domain_rules', '')[:30000] if accuracy_mode() else condense_context(query, state.get('domain_rules', ''), max_chars=2400, chunk_size=800, overlap=80, max_chunks=4), 'field_mapping_hints': state.get('field_mapping_hints', '') if accuracy_mode() else condense_context(query, state.get('field_mapping_hints', ''), max_chars=2200, chunk_size=800, overlap=80, max_chunks=4), 'domain_specific_hints': _domain_specific_hints(state), 'fact_constraints': _resolved_fact_constraints(state), 'domain_examples': state.get('domain_examples', '')[:20000] if accuracy_mode() else condense_context(query, state.get('domain_examples', ''), max_chars=2200, chunk_size=900, overlap=80, max_chunks=3)})
    sql_code = extract_sql_text(response.content)
    return {'generated_sql': clean_generated_sql(sql_code.strip(), state['query'])}

def repair_sql(state: NL2SQLState) -> Dict:
    print('-> repair_sql')
    validation_passed = state.get('validation_passed', False)
    precheck_passed = state.get('precheck_passed', False)
    query = state.get('normalized_query', state['query'])
    sql = extract_sql_text(state.get('validated_sql', state.get('generated_sql', ''))).strip()
    correction_rules = correction_guidance(state, '\n'.join(state.get('validation_errors', [])) + ' ' + str(state.get('precheck_error_message') or ''))
    if validation_passed and precheck_passed and sql:
        return {'validated_sql': sql, 'generated_sql': sql, 'repair_notes': 'No repair needed.', 'needs_rerun': False}
    fastpath_sql = _validation_repair_fastpath(state)
    if fastpath_sql:
        return {'validated_sql': fastpath_sql, 'generated_sql': fastpath_sql, 'repair_notes': 'SQL repaired from validation or preview errors using deterministic fastpath.', 'needs_rerun': True, 'repair_attempt_count': state.get('repair_attempt_count', 0) + 1, 'precheck_passed': False, 'precheck_error_message': None, 'precheck_sql': '', 'precheck_execution_result': '', 'precheck_raw_execution_data': []}
    prompt = 'You are a MySQL SQL repair assistant.\nRepair the SQL using the original SQL, plan feedback, validation errors, preview execution error, schema, rules, mapping hints, and examples.\n\nRules:\n1. Output JSON only.\n2. Format must be {{"sql": "SELECT ..."}}.\n3. The SQL must be complete and executable.\n4. Do not output markdown, comments, placeholders, or explanations.\n5. Fix unknown columns, wrong time fields, wrong table joins, missing GROUP BY, guessed aliases, and incomplete SQL.\n6. Do not reuse fields or tables already proven invalid by the errors unless the schema clearly supports them.\n7. Do not output analysis, chain-of-thought, or any prose before/after the JSON.\n8. The first character of your response must be {{ and the last character must be }}.\n\nUser query:\n{query}\n\nOriginal SQL:\n{sql}\n\nPlan validation feedback:\n{plan_feedback}\n\nValidation / preview errors:\n{errors}\n\nVerified correction strategies (use only when applicable):\n{correction_rules}\n\nSQL plan:\n{sql_plan}\n\nExpanded domain schema:\n{domain_schema}\n\nDomain rules:\n{domain_rules}\n\nField mapping hints:\n{field_mapping_hints}\n\nDomain-specific guardrails:\n{domain_specific_hints}\n\nResolved fact-table and grain constraints:\n{fact_constraints}\n\nReference examples:\n{domain_examples}\n'
    repair_query = '\n'.join([query, sql, '\n'.join(state.get('validation_errors', []))])
    response = (ChatPromptTemplate.from_template(prompt) | llm.bind(max_tokens=SQL_GENERATION_MAX_TOKENS)).invoke({'query': query, 'sql': condense_context(repair_query, sql, max_chars=2500, chunk_size=900, overlap=60, max_chunks=2), 'plan_feedback': condense_context(repair_query, state.get('plan_feedback', '') or 'None', max_chars=1400, chunk_size=600, overlap=60, max_chunks=3), 'errors': condense_context(repair_query, '\n'.join(state.get('validation_errors', [])), max_chars=1800, chunk_size=700, overlap=60, max_chunks=3), 'correction_rules': correction_rules or 'None', 'sql_plan': state.get('sql_plan', '') if accuracy_mode() else condense_context(repair_query, state.get('sql_plan', ''), max_chars=3200, chunk_size=1000, overlap=100, max_chunks=4), 'domain_schema': state.get('domain_schema', '') if accuracy_mode() else condense_context(repair_query, state.get('domain_schema', ''), max_chars=7600, chunk_size=1400, overlap=120, max_chunks=6), 'domain_rules': state.get('domain_rules', '')[:30000] if accuracy_mode() else condense_context(repair_query, state.get('domain_rules', ''), max_chars=2200, chunk_size=800, overlap=80, max_chunks=4), 'field_mapping_hints': state.get('field_mapping_hints', '') if accuracy_mode() else condense_context(repair_query, state.get('field_mapping_hints', ''), max_chars=2000, chunk_size=800, overlap=80, max_chunks=3), 'domain_specific_hints': _domain_specific_hints(state), 'fact_constraints': _resolved_fact_constraints(state), 'domain_examples': state.get('domain_examples', '')[:20000] if accuracy_mode() else condense_context(repair_query, state.get('domain_examples', ''), max_chars=1800, chunk_size=800, overlap=60, max_chunks=3)})
    fixed_sql = extract_sql_text(response.content)
    fixed_sql = clean_generated_sql(fixed_sql.strip(), state['query'])
    return {'validated_sql': fixed_sql, 'generated_sql': fixed_sql, 'repair_notes': 'SQL repaired from validation or preview errors.', 'needs_rerun': True, 'repair_attempt_count': state.get('repair_attempt_count', 0) + 1, 'precheck_passed': False, 'precheck_error_message': None, 'precheck_sql': '', 'precheck_execution_result': '', 'precheck_raw_execution_data': []}

def repair_sql_after_execution(state: NL2SQLState) -> Dict:
    print('-> repair_sql_after_execution')
    query = state.get('normalized_query', state['query'])
    sql = extract_sql_text(state.get('validated_sql', state.get('generated_sql', ''))).strip()
    error_message = state.get('error_message') or ''
    correction_rules = correction_guidance(state, error_message)
    if not sql or not error_message:
        return {'execution_repair_attempted': True}
    prompt = 'You are a MySQL SQL repair assistant.\nThe SQL passed static validation, but real database execution failed.\nRepair it using the runtime database error plus all available plan, schema, rule, mapping, and example context.\n\nRules:\n1. Output JSON only.\n2. Format must be {{"sql": "SELECT ..."}}.\n3. The SQL must be complete and executable.\n4. Do not output markdown, comments, placeholders, or explanations.\n5. Pay close attention to unknown columns, missing tables, wrong aliases, wrong date fields, and candidate-schema constraints.\n6. Do not output analysis, chain-of-thought, or any prose before/after the JSON.\n7. The first character of your response must be {{ and the last character must be }}.\n\nUser query:\n{query}\n\nFailed SQL:\n{sql}\n\nDatabase error:\n{error_message}\n\nVerified correction strategies (use only when applicable):\n{correction_rules}\n\nPlan validation feedback:\n{plan_feedback}\n\nValidation errors:\n{validation_errors}\n\nSQL plan:\n{sql_plan}\n\nExpanded domain schema:\n{domain_schema}\n\nDatabase knowledge:\n{db_knowledge}\n\nDomain rules:\n{domain_rules}\n\nField mapping hints:\n{field_mapping_hints}\n\nDomain-specific guardrails:\n{domain_specific_hints}\n\nResolved fact-table and grain constraints:\n{fact_constraints}\n\nReference examples:\n{domain_examples}\n'
    repair_query = '\n'.join([query, sql, error_message, '\n'.join(state.get('validation_errors', []))])
    response = (ChatPromptTemplate.from_template(prompt) | llm.bind(max_tokens=SQL_GENERATION_MAX_TOKENS)).invoke({'query': query, 'sql': condense_context(repair_query, sql, max_chars=2500, chunk_size=900, overlap=60, max_chunks=2), 'error_message': condense_context(repair_query, error_message, max_chars=1800, chunk_size=700, overlap=60, max_chunks=3), 'correction_rules': correction_rules or 'None', 'plan_feedback': condense_context(repair_query, state.get('plan_feedback', '') or 'None', max_chars=1400, chunk_size=600, overlap=60, max_chunks=3), 'validation_errors': condense_context(repair_query, '\n'.join(state.get('validation_errors', [])), max_chars=1600, chunk_size=700, overlap=60, max_chunks=3), 'sql_plan': state.get('sql_plan', '') if accuracy_mode() else condense_context(repair_query, state.get('sql_plan', ''), max_chars=2800, chunk_size=1000, overlap=100, max_chunks=4), 'domain_schema': state.get('domain_schema', '') if accuracy_mode() else condense_context(repair_query, state.get('domain_schema', ''), max_chars=7000, chunk_size=1400, overlap=120, max_chunks=6), 'db_knowledge': condense_context(repair_query, state.get('db_knowledge', ''), max_chars=3000, chunk_size=1000, overlap=100, max_chunks=4), 'domain_rules': state.get('domain_rules', '')[:30000] if accuracy_mode() else condense_context(repair_query, state.get('domain_rules', ''), max_chars=2000, chunk_size=800, overlap=80, max_chunks=3), 'field_mapping_hints': state.get('field_mapping_hints', '') if accuracy_mode() else condense_context(repair_query, state.get('field_mapping_hints', ''), max_chars=1800, chunk_size=700, overlap=60, max_chunks=3), 'domain_specific_hints': _domain_specific_hints(state), 'fact_constraints': _resolved_fact_constraints(state), 'domain_examples': state.get('domain_examples', '')[:20000] if accuracy_mode() else condense_context(repair_query, state.get('domain_examples', ''), max_chars=1500, chunk_size=700, overlap=60, max_chunks=2)})
    fixed_sql = clean_generated_sql(extract_sql_text(response.content).strip(), state['query'])
    return {'validated_sql': fixed_sql, 'generated_sql': fixed_sql, 'validation_errors': [], 'validation_passed': False, 'precheck_passed': False, 'precheck_error_message': None, 'precheck_sql': '', 'precheck_execution_result': '', 'precheck_raw_execution_data': [], 'repair_notes': 'SQL repaired from runtime database error.', 'needs_rerun': True, 'repair_attempt_count': state.get('repair_attempt_count', 0) + 1, 'execution_repair_attempted': True, 'final_execution_result': '', 'raw_execution_data': [], 'error_message': None}

def repair_sql_after_result_check(state: NL2SQLState) -> Dict:
    print('-> repair_sql_after_result_check')
    query = state.get('normalized_query', state['query'])
    sql = extract_sql_text(state.get('validated_sql', state.get('generated_sql', ''))).strip()
    result_check_notes = state.get('result_check_notes', '').strip()
    correction_rules = correction_guidance(state, result_check_notes)
    if not sql or not result_check_notes:
        return {'result_check_repair_attempted': True}
    fastpath_sql = _semantic_repair_fastpath(state)
    if fastpath_sql:
        return {'validated_sql': fastpath_sql, 'generated_sql': fastpath_sql, 'validation_errors': [], 'validation_passed': False, 'precheck_passed': False, 'precheck_error_message': None, 'precheck_sql': '', 'precheck_execution_result': '', 'precheck_raw_execution_data': [], 'repair_notes': 'SQL repaired from semantic result-check feedback using deterministic fastpath.', 'needs_rerun': True, 'result_check_repair_attempted': True, 'final_execution_result': '', 'raw_execution_data': [], 'error_message': None}
    prompt = 'You are a MySQL SQL repair assistant.\nThe SQL executed, but the downstream result check judged the result suspicious.\nRepair it using the result-check feedback plus plan, schema, rule, mapping, and example context.\n\nRules:\n1. Output JSON only.\n2. Format must be {{"sql": "SELECT ..."}}.\n3. The SQL must be complete and executable.\n4. Do not output markdown, comments, placeholders, or explanations.\n5. Focus on fixing empty-result overfiltering, missing ORDER BY for TopN/ranking, wrong aggregation grain, and overly narrow time filters.\n6. Keep changes minimal and preserve correct business intent from the original query.\n7. Do not output analysis, chain-of-thought, or any prose before/after the JSON.\n8. The first character of your response must be {{ and the last character must be }}.\n\nUser query:\n{query}\n\nCurrent SQL:\n{sql}\n\nResult-check feedback:\n{result_check_notes}\n\nVerified correction strategies (use only when applicable):\n{correction_rules}\n\nPlan validation feedback:\n{plan_feedback}\n\nSQL plan:\n{sql_plan}\n\nExpanded domain schema:\n{domain_schema}\n\nDomain rules:\n{domain_rules}\n\nField mapping hints:\n{field_mapping_hints}\n\nDomain-specific guardrails:\n{domain_specific_hints}\n\nResolved fact-table and grain constraints:\n{fact_constraints}\n\nReference examples:\n{domain_examples}\n'
    repair_query = '\n'.join([query, sql, result_check_notes])
    response = (ChatPromptTemplate.from_template(prompt) | llm.bind(max_tokens=SQL_GENERATION_MAX_TOKENS)).invoke({'query': query, 'sql': condense_context(repair_query, sql, max_chars=2500, chunk_size=900, overlap=60, max_chunks=2), 'result_check_notes': condense_context(repair_query, result_check_notes, max_chars=1400, chunk_size=600, overlap=60, max_chunks=3), 'correction_rules': correction_rules or 'None', 'plan_feedback': condense_context(repair_query, state.get('plan_feedback', '') or 'None', max_chars=1400, chunk_size=600, overlap=60, max_chunks=3), 'sql_plan': state.get('sql_plan', '') if accuracy_mode() else condense_context(repair_query, state.get('sql_plan', ''), max_chars=2800, chunk_size=1000, overlap=100, max_chunks=4), 'domain_schema': state.get('domain_schema', '') if accuracy_mode() else condense_context(repair_query, state.get('domain_schema', ''), max_chars=7000, chunk_size=1400, overlap=120, max_chunks=6), 'domain_rules': state.get('domain_rules', '')[:30000] if accuracy_mode() else condense_context(repair_query, state.get('domain_rules', ''), max_chars=2000, chunk_size=800, overlap=80, max_chunks=3), 'field_mapping_hints': state.get('field_mapping_hints', '') if accuracy_mode() else condense_context(repair_query, state.get('field_mapping_hints', ''), max_chars=1800, chunk_size=700, overlap=60, max_chunks=3), 'domain_specific_hints': _domain_specific_hints(state), 'fact_constraints': _resolved_fact_constraints(state), 'domain_examples': state.get('domain_examples', '')[:20000] if accuracy_mode() else condense_context(repair_query, state.get('domain_examples', ''), max_chars=1500, chunk_size=700, overlap=60, max_chunks=2)})
    fixed_sql = clean_generated_sql(extract_sql_text(response.content).strip(), state['query'])
    return {'validated_sql': fixed_sql, 'generated_sql': fixed_sql, 'validation_errors': [], 'validation_passed': False, 'precheck_passed': False, 'precheck_error_message': None, 'precheck_sql': '', 'precheck_execution_result': '', 'precheck_raw_execution_data': [], 'repair_notes': 'SQL repaired from semantic result-check feedback.', 'needs_rerun': True, 'result_check_repair_attempted': True, 'final_execution_result': '', 'raw_execution_data': [], 'error_message': None}
