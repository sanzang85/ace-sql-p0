"""DDL metadata and exact result comparison. No business contracts in P0."""
import json
import re
from typing import Mapping

def quality_domain(query):
    return 'generic'

def read_schema(text: str) -> dict:
    """Accept the public JSON format or extract MySQL CREATE TABLEs from Markdown."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    import sqlglot
    from sqlglot import exp
    tables = {}
    for ddl in re.findall(r"(?ims)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?[A-Za-z_]\w*`?\s*\(.*?;", text):
        try:
            tree = sqlglot.parse_one(ddl, read="mysql")
        except sqlglot.errors.ParseError:
            continue
        table = tree.find(exp.Table)
        if not table:
            continue
        columns = {}
        for col in tree.find_all(exp.ColumnDef):
            comment = col.find(exp.CommentColumnConstraint)
            columns[col.name] = {"type": col.args["kind"].sql(dialect="mysql"), "comment": str(comment.this) if comment else ""}
        primary_keys = [col.name for col in tree.find_all(exp.ColumnDef) if col.find(exp.PrimaryKeyColumnConstraint)]
        for key in tree.find_all(exp.PrimaryKey):
            primary_keys.extend(item.name for item in key.expressions)
        foreign_keys = []
        for key in tree.find_all(exp.ForeignKey):
            reference = key.args.get("reference")
            if reference and isinstance(reference.this, exp.Schema):
                foreign_keys.append({"columns": [item.name for item in key.expressions], "table": reference.this.this.name,
                                     "referenced_columns": [item.name for item in reference.this.expressions]})
        tables[table.name] = {"columns": columns, "primary_keys": primary_keys, "foreign_keys": foreign_keys}
    if not tables:
        raise ValueError("No schema tables found; supply JSON schema or MySQL CREATE TABLE statements")
    return {"tables": tables}

def columns_metadata(schema: Mapping) -> dict[str, dict[str, dict]]:
    tables = schema.get("tables", schema)
    rows = [(t["name"], t) for t in tables] if isinstance(tables, list) else tables.items()
    result = {}
    for name, table in rows:
        columns = table.get("columns", []) if isinstance(table, dict) else table
        if isinstance(columns, dict):
            result[name] = {k: v if isinstance(v, dict) else {"type": str(v)} for k, v in columns.items()}
        else:
            result[name] = {c["name"] if isinstance(c, dict) else c: c if isinstance(c, dict) else {} for c in columns}
    return result

def compare_results(predicted, expected) -> bool:
    """Compare named columns, values, NULLs and row multiplicity; ignore row order."""
    from collections import Counter
    from decimal import Decimal
    from datetime import date, datetime
    def value_key(value):
        if value is None:
            return ('null', '')
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return ('number', str(Decimal(str(value)).normalize()))
        if isinstance(value, (date, datetime)):
            return ('datetime', value.isoformat())
        return (type(value).__name__, str(value))
    def rows_key(rows):
        return Counter(tuple(sorted((str(key), value_key(value)) for key, value in row.items())) for row in rows)
    return rows_key(predicted) == rows_key(expected)

def compare_result_values(predicted, expected) -> bool:
    """Diagnostic execution match: ignore aliases, preserve output column order.

    Retains the strict comparator's numeric, text, NULL and duplicate semantics.
    Empty/empty is a result match only, not proof of equivalent SQL semantics.
    """
    def positional(rows):
        return [{str(index): value for index, value in enumerate(row.values())} for row in rows]
    return compare_results(positional(predicted), positional(expected))
