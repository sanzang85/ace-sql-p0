"""Generic SQL safety helpers retained from V15. No template store in P0."""
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

def feature_enabled():
    # Compatibility flag for strict result comparison and read-only execution.
    return True

def _normalized_schema(schema: Mapping[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    raw_tables = schema.get("tables", schema)
    if isinstance(raw_tables, list):
        items = ((str(x.get("name", "")), x) for x in raw_tables if isinstance(x, Mapping))
    elif isinstance(raw_tables, Mapping):
        items = ((str(name), value) for name, value in raw_tables.items())
    else:
        return result
    for name, value in items:
        if not name:
            continue
        cols = value.get("columns", []) if isinstance(value, Mapping) else value
        if isinstance(cols, Mapping):
            names = cols.keys()
        elif isinstance(cols, list):
            names = (col.get("name", "") if isinstance(col, Mapping) else col for col in cols)
        else:
            names = []
        result[name.lower()] = {str(col).lower() for col in names if str(col).strip()}
    return result

def build_value_probe(table: str, column: str, value: str, schema: Mapping[str, Any], *, mode: str = "fuzzy", limit: int = 20) -> tuple[str, tuple[Any, ...]]:
    """Build a parameterized, schema-whitelisted value probe (never execute it)."""
    tables = _normalized_schema(schema)
    if table.lower() not in tables or column.lower() not in tables[table.lower()]:
        raise ValueError("probe target is not present in the supplied schema")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column):
        raise ValueError("unsupported identifier")
    safe_limit = max(1, min(int(limit), 100))
    if mode == "exact":
        sql = f"SELECT DISTINCT `{column}` FROM `{table}` WHERE `{column}` = %s LIMIT %s"
        return sql, (value, safe_limit)
    if mode == "fuzzy":
        sql = f"SELECT DISTINCT `{column}` FROM `{table}` WHERE `{column}` LIKE %s LIMIT %s"
        return sql, (f"%{value}%", safe_limit)
    if mode == "distribution":
        sql = f"SELECT MIN(`{column}`) AS min_value, MAX(`{column}`) AS max_value, AVG(`{column}`) AS avg_value, SUM(`{column}` IS NULL) AS null_count, COUNT(*) AS row_count FROM `{table}`"
        return sql, ()
    raise ValueError("probe mode must be exact, fuzzy or distribution")

def execute_readonly_probe(sql: str, params: Sequence[Any], runner: Callable[[str, Sequence[Any]], Any]) -> Any:
    """Delegate a SELECT-only probe to an injected read-only runner."""
    if ";" in sql or re.match(r"(?is)^\s*select\b", sql) is None or re.search(r"(?i)\b(?:insert|update|delete|drop|alter|create|replace|outfile|dumpfile)\b", sql):
        raise ValueError("probe runner only accepts a read-only SELECT")
    return runner(sql, params)

def validate_readonly_sql(sql: str) -> list[str]:
    """Require exactly one read-only SELECT/CTE statement using a SQL AST."""
    try:
        import sqlglot
        from sqlglot import exp

        statements = sqlglot.parse(sql, read="mysql")
    except Exception as exc:
        return [f"SQL parser rejected candidate: {exc}"]
    if len(statements) != 1 or statements[0] is None:
        return ["SQL must contain exactly one statement"]
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        return ["Only SELECT or WITH queries are allowed"]
    if tree.find(exp.Lock):
        return ["Locking reads are not allowed"]
    if tree.find(exp.Into):
        return ["SELECT INTO is not allowed"]
    if any(node.comments for node in tree.walk()):
        return ["SQL comments are not allowed"]
    if re.search(r"(?i)\b(?:sleep|benchmark|load_file|get_lock|release_lock|sys_exec|sys_eval)\s*\(", sql):
        return ["Side-effecting or resource-intensive function is not allowed"]
    if ":=" in sql:
        return ["SQL variable assignments are not allowed"]
    blocked = (exp.DDL, exp.DML, exp.Command)
    if any(isinstance(node, blocked) for node in tree.walk()):
        return ["Data-changing or administrative SQL is not allowed"]
    return []
