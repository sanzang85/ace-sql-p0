"""Query-derived intent constraints; never reads evaluation questions or answers."""
from __future__ import annotations
from datetime import date
import os
import re

def accuracy_mode():
    return os.getenv('NL2SQL_ACCURACY_MODE', '0').lower() in {'1', 'true', 'yes'}

def routing_text(query):
    """Names inside explicit quotation marks are values, not query-family triggers."""
    return re.sub('“[^”]*”|「[^」]*」|"[^"]*"', ' ', query)

def simple_aggregate(query):
    """Single aggregate request, including a named entity used only as a filter."""
    if re.search('各|按(?:月|日|班|所|物|产|机|牌)|每天|每月|排名|前\\s*\\d+|最高|最多|最少|列出|展示|显示', query):
        return False
    return bool(re.search('统计|计算|合计|总量|平均|最早|最晚', query))

def record_count(query):
    return bool(re.search('(?:记录|单据|工单|明细|结果)(?:总)?(?:数量|数|条数)', query)) and (not re.search('不同|去重|合计|总量|平均|比例|率', query))

def explicit_time_bounds(query):
    """Return half-open calendar bounds; None means no bound on that side."""

    def after_month(y, m):
        return date(y + (m == 12), 1 if m == 12 else m + 1, 1).isoformat()
    m = re.search('(\\d{4})年(\\d{1,2})月至(?:(\\d{4})年)?(\\d{1,2})月', query)
    if m:
        y, month, ey, em = m.groups()
        ey = int(ey or y)
        return (date(int(y), int(month), 1).isoformat(), after_month(ey, int(em)))
    m = re.search('(\\d{4})年至(\\d{4})年', query)
    if m:
        return (f'{m[1]}-01-01', f'{int(m[2]) + 1}-01-01')
    m = re.search('(\\d{4})年([一二三四1234])季度', query)
    if m:
        n = '一二三四'.find(m[2]) + 1 if m[2] in '一二三四' else int(m[2])
        y = int(m[1])
        month = 3 * n - 2
        return (date(y, month, 1).isoformat(), after_month(y, month + 2))
    m = re.search('(\\d{4})年(上|下)半年', query)
    if m:
        y = int(m[1])
        return (f'{y}-01-01', f'{y}-07-01') if m[2] == '上' else (f'{y}-07-01', f'{y + 1}-01-01')
    m = re.search('(\\d{4})年(\\d{1,2})月(\\d{1,2})[日号](以来|起|开始)?', query)
    if m:
        start = date(int(m[1]), int(m[2]), int(m[3]))
        return (start.isoformat(), None if m[4] else date.fromordinal(start.toordinal() + 1).isoformat())
    m = re.search('(截至)?(\\d{4})年(\\d{1,2})月', query)
    if m:
        return (None if m[1] else date(int(m[2]), int(m[3]), 1).isoformat(), after_month(int(m[2]), int(m[3])))
    m = re.search('(\\d{4})年', query)
    if m:
        return (f'{m[1]}-01-01', f'{int(m[1]) + 1}-01-01')
    return None

def intent_guidance(query):
    pass
    lines = ['The user question overrides retrieved examples: transfer schema relationships, never unrelated metrics, filters or grouping.', '工单/记录/单据数量 counts rows (or their non-null primary key); 批次数量 counts distinct batch identifiers. Never substitute one grain for the other.', 'A named filter alone does not imply a new grouping. When an existing historical example describes the SAME requested business result, preserve its established output contract (contextual names, identifiers, sample counts, aliases and column order) unless the user explicitly narrows or changes it. Adapt dates, entities, limits and requested metrics to the current request. Do not copy projections or business filters from an unrelated example. A scalar record-count request must remain a scalar count, not inherit a detail-list or grouped result.', 'Listing some requested columns is not by itself an instruction to remove the same-result reference context columns. Explicit restrictions such as 仅/只输出/不要其他列 do restrict the output. A contextual name fixed to one literal in WHERE may be grouped with aggregates without adding a new result dimension.', 'Determine the requested record scope before selecting tables: a header record and its line/detail records are different populations even when they share metric names. Match the table and field comments to the requested object.', 'For a document/header record count, if both header and detail tables contain the requested measure and the header contains all requested filters, count the header unless the question explicitly requests line/detail or per-item records. A measure being called a total on the header does not make that header field unavailable.', 'If the requested metrics and filters are all on one table, use that table alone. Do not join its parent or dimensions merely to check their archive flags or validate foreign keys; this can discard valid fact records or multiply rows. A real filter or output on another table still requires the appropriate join.', 'COUNT(DISTINCT a fact foreign key) does not require a dimension join unless a dimension name, classification, or condition is requested. Do not import category, business-type or status filters from an unrelated example.', 'Choose metric fields by their schema comments and explicit business definition, not by a similar English column name. Reported, actual, planned and remaining quantities are distinct measurements.', 'Distinguish database record identifiers from business codes: 主键/ID and 编码 are not interchangeable. 编号 does NOT automatically mean 编码. For an ambiguous 编号, preserve the same-business historical convention (including a primary key/record ID) and actual field definitions; do not replace an established _ID with _Code merely because the request says 编号. An explicit request for 编码 calls for the business code.', 'When the query asks which records participated or were valid during a period and the schema has start/end validity fields, check interval overlap with that period; do not substitute record creation time or require the whole interval to lie inside the period.', 'Never replace an unavailable requested attribute with a NULL output or a merely similar field. Request the real schema field/table so validation can reopen schema selection; a successful query with fabricated empty columns is not a completed answer.', '已填写/已记录/已回传 means IS NOT NULL, including zero; 数量大于0 means > 0. 数量合计/总量 means SUM; 不同…数量 means COUNT(DISTINCT that entity).', 'Keep empty sets, zero counts and NULL aggregate results when the requested filters produce them; do not relax filters merely to obtain rows.', 'Preserve spaces and punctuation inside business names; names in examples are not names requested by this query.', 'Preserve a named entity in full, including its product suffix, parentheses and grade. A fact table may already store the requested name; do not replace it with a shorter dimension-table name or remove the suffix to obtain a match.', 'Use the full entity name for a requested 牌号/物料名称 or other named dimension; a 简称/short name is not interchangeable and can merge different entities. Use an abbreviated-name field only when the user asks for abbreviations.']
    if record_count(query):
        lines.append('This query requests a count of records, not batch/entity DISTINCT count and not a detail list.')
    bounds = explicit_time_bounds(query)
    if bounds:
        start, end = bounds
        lines.append(f"Calendar range from the query: start inclusive={start or 'unbounded'}; end exclusive={end or 'unbounded'}. Apply to the correct business time field; do not collapse a quarter or month range to one month/day.")
    return '\n'.join(('- ' + line for line in lines))

def conjunctive_predicates(node):
    """Only predicates required by AND; never descend into an OR or subquery."""
    from sqlglot import exp
    if node is None:
        return []
    if isinstance(node, (exp.Where, exp.Paren)):
        return conjunctive_predicates(node.this)
    if isinstance(node, exp.And):
        return conjunctive_predicates(node.this) + conjunctive_predicates(node.expression)
    return [node]

def fixed_value_grouping(sql, query):
    """Allow only direct grouping columns pinned by required WHERE literals."""
    import sqlglot
    from sqlglot import exp
    if record_count(query):
        return False
    try:
        tree = sqlglot.parse_one(sql, read='mysql')
    except sqlglot.errors.ParseError:
        return False
    if not isinstance(tree, exp.Select):
        return False
    group = tree.args.get('group')
    if not group or not group.expressions or any((group.args.get(k) for k in ('rollup', 'cube', 'grouping_sets', 'totals'))):
        return False
    fixed = set()
    for pred in conjunctive_predicates(tree.args.get('where')):
        pairs = []
        if isinstance(pred, exp.EQ):
            pairs = [(pred.this, pred.expression), (pred.expression, pred.this)]
        elif isinstance(pred, exp.In) and (not pred.args.get('query')) and (len(pred.expressions) == 1):
            pairs = [(pred.this, pred.expressions[0])]
        for col, value in pairs:
            if isinstance(col, exp.Column) and isinstance(value, exp.Literal):
                fixed.add(col.sql(dialect='mysql').lower())
    return all((isinstance(col, exp.Column) and col.sql(dialect='mysql').lower() in fixed for col in group.expressions))
