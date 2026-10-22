"""P0 SQL validation: read-only AST, real schema, evidence, and database preview."""
from typing import Dict
from .state import NL2SQLState
from .config import SQL_PRECHECK_ENABLED
from .execution import execute_sql_once
from .schema_catalog import selected_schema_columns
from .lifecycle import validate_readonly_sql
from .query_evidence import scoped_schema_errors, evidence_sql_errors

def _static_validate_sql(state, sql):
    errors = validate_readonly_sql(sql)
    if errors:
        return errors
    from sqlglot import parse_one, exp
    from sqlglot.optimizer.scope import traverse_scope
    columns = selected_schema_columns(state.get('domain_schema', ''))
    actual = {t.lower() for t in columns}
    tree = parse_one(sql, read='mysql')
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table) and source.name.lower() not in actual:
                errors.append('Table is not in candidate schema: ' + source.name)
    errors += scoped_schema_errors(sql, columns)
    errors += evidence_sql_errors(sql, state.get('data_dictionary', ''), state.get('verified_query_values', []))
    return list(dict.fromkeys(errors))

def validate_sql(state: NL2SQLState) -> Dict:
    print("-> validate_sql")
    sql = state.get("generated_sql", "").strip()
    if not sql:
        return {
            "validated_sql": "",
            "validation_errors": ["SQL is empty."],
            "validation_passed": False,
            "precheck_passed": False,
            "precheck_error_message": "SQL is empty.",
            "precheck_sql": "",
            "precheck_execution_result": "",
            "precheck_raw_execution_data": [],
        }

    errors = _static_validate_sql(state, sql)
    precheck_passed = False
    precheck_error_message = None
    precheck_execution_result = ""
    precheck_raw_execution_data = []

    if not errors and SQL_PRECHECK_ENABLED:
        print("   [precheck] running preview SQL execution...")
        preview_result, preview_rows, preview_error = execute_sql_once(sql)
        if preview_error:
            precheck_error_message = preview_error
            errors.append(f"Preview execution failed: {preview_error}")
        else:
            precheck_passed = True
            precheck_execution_result = preview_result
            precheck_raw_execution_data = preview_rows
    elif not errors:
        precheck_passed = True

    return {
        "validated_sql": sql,
        "validation_errors": errors,
        "validation_passed": len(errors) == 0,
        "precheck_passed": precheck_passed,
        "precheck_error_message": precheck_error_message,
        "precheck_sql": sql if precheck_passed else "",
        "precheck_execution_result": precheck_execution_result,
        "precheck_raw_execution_data": precheck_raw_execution_data,
    }
