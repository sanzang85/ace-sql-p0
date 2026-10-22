"""Validate plan shape and actual candidate-table membership, without business contracts."""
import json
from .schema_catalog import selected_schema_columns
from .text_utils import safe_json_loads

def validate_sql_plan(state):
    plan = safe_json_loads(state.get('sql_plan', ''), {})
    errors = []
    selected = selected_schema_columns(state.get('domain_schema', ''))
    if not isinstance(plan, dict) or not plan:
        errors.append('SQL plan is empty or not a valid JSON object.')
    else:
        names = [str(n) for k in ('fact_tables', 'dimension_tables') for n in plan.get(k, [])]
        if not names:
            errors.append('SQL plan must select at least one actual table from the selected schema.')
        for name in names:
            if name.lower() not in {t.lower() for t in selected}:
                errors.append('Plan references an unselected table: ' + name)
        if not plan.get('output_columns') and not plan.get('metrics'):
            errors.append('SQL plan must describe requested output columns or metrics.')
    return {'plan_validation_errors': errors, 'plan_validation_passed': not errors,
            'plan_feedback': '\n'.join(errors),
            'plan_rebuild_count': state.get('plan_rebuild_count', 0) + bool(errors)}
