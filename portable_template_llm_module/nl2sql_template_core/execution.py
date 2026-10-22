from __future__ import annotations
from .intent_contracts import accuracy_mode, simple_aggregate
import json
import re
from typing import Dict, List
from .analysis import query_traits
from .config import get_db_config
from .state import NL2SQLState
from .text_utils import extract_sql_text, safe_json_loads
from .trace import trace_summary
from .lifecycle import feature_enabled

def execute_sql_once(sql: str) -> tuple[str, list[Dict], str | None]:
    connection = None
    try:
        import pymysql
        if feature_enabled():
            from .lifecycle import validate_readonly_sql
            from .readonly_db import run_probe
            errors = validate_readonly_sql(sql)
            if errors:
                raise ValueError('; '.join(errors))
            raw_rows = run_probe(sql)
        else:
            connection = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
            with connection.cursor() as cursor:
                cursor.execute(sql)
                raw_rows = cursor.fetchall()
        if not raw_rows:
            execution_result = 'Query executed successfully, but returned no rows.'
        else:
            headers = list(raw_rows[0].keys())
            header_row = '| ' + ' | '.join(headers) + ' |'
            separator_row = '| ' + ' | '.join(['---'] * len(headers)) + ' |'
            data_rows = []
            for row in raw_rows:
                values = [str(row[header]) if row[header] is not None else 'NULL' for header in headers]
                data_rows.append('| ' + ' | '.join(values) + ' |')
            execution_result = '\n'.join([header_row, separator_row] + data_rows)
        return (execution_result, list(raw_rows), None)
    except Exception as exc:
        error_message = str(exc)
        fallback_result = f'**Query execution failed**:\n```text\n{error_message}\n```'
        return (fallback_result, [], error_message)
    finally:
        if connection:
            connection.close()

def finalize_plan_failure(state: NL2SQLState) -> Dict:
    print('-> finalize_plan_failure')
    errors = state.get('plan_validation_errors', [])
    feedback = state.get('plan_feedback', '')
    details_list: List[str] = errors[:] if errors else [feedback] if feedback else []
    parse_error = state.get('planner_parse_error', '').strip()
    raw_response = state.get('planner_raw_response', '').strip()
    if state.get('planner_used_fallback'):
        details_list.append('Planner fallback was used because the model response could not be parsed into a valid plan JSON.')
    if parse_error:
        details_list.append(f'Planner parse diagnostics: {parse_error}')
    if raw_response:
        details_list.append(f'Planner raw response summary: {trace_summary(raw_response)}')
    details = '\n'.join(details_list) if details_list else 'Unknown plan validation failure.'
    final_message = f'**Workflow stopped before SQL generation**\n\nThe SQL plan did not pass validation after the retry limit.\n\n```text\n{details}\n```'
    return {'final_execution_result': final_message, 'raw_execution_data': [], 'error_message': 'Plan validation failed'}

def finalize_sql_failure(state: NL2SQLState) -> Dict:
    print('-> finalize_sql_failure')
    errors = list(state.get('validation_errors', []))
    if state.get('precheck_error_message'):
        errors.append(f"Preview execution failed: {state['precheck_error_message']}")
    details = '\n'.join(errors) if errors else 'Unknown SQL validation failure.'
    final_message = f'**Workflow stopped before final SQL execution**\n\nThe SQL did not pass validation after the retry limit.\n\n```text\n{details}\n```'
    return {'final_execution_result': final_message, 'raw_execution_data': [], 'error_message': 'SQL validation failed'}

def finalize_result_check_failure(state: NL2SQLState) -> Dict:
    print('-> finalize_result_check_failure')
    notes = state.get('result_check_notes', '').strip() or 'Result check failed.'
    current_result = state.get('final_execution_result', '').strip()
    if current_result:
        final_message = f'**Result check did not pass**\n\n{notes}\n\n{current_result}'
    else:
        final_message = f'**Result check did not pass**\n\n```text\n{notes}\n```'
    return {'final_execution_result': final_message, **({'error_message': 'Result check failed'} if accuracy_mode() else {})}

def final_sql_execution(state: NL2SQLState) -> Dict:
    effective_sql = extract_sql_text(state.get('validated_sql', state.get('generated_sql', '')))
    print(f'-> final_sql_execution\n   [SQL]:\n{effective_sql}')
    sql = effective_sql.strip()
    if not sql or sql.startswith('ERROR:'):
        print('   [blocked] SQL is empty or intentionally aborted.')
        return {'final_execution_result': 'No valid SQL was produced.', 'raw_execution_data': [], 'error_message': 'Invalid SQL'}
    if state.get('precheck_passed') and state.get('precheck_sql', '').strip() == sql and state.get('precheck_execution_result'):
        cached_rows = list(state.get('precheck_raw_execution_data', []))
        print(f'   [reuse precheck result] fetched {len(cached_rows)} rows.')
        return {'final_execution_result': state.get('precheck_execution_result', ''), 'raw_execution_data': cached_rows, 'error_message': None}
    execution_result, raw_rows, error_message = execute_sql_once(sql)
    if error_message:
        print(f'   [sql execution error] {error_message}')
        return {'final_execution_result': execution_result, 'raw_execution_data': [], 'error_message': error_message}
    print(f'   [done] fetched {len(raw_rows)} rows.')
    return {'final_execution_result': execution_result, 'raw_execution_data': list(raw_rows), 'error_message': None}

def result_check(state):
    ok = not state.get('error_message')
    return {'result_check_passed': ok, 'result_check_notes': '' if ok else str(state['error_message'])}
