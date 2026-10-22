"""Schema- and database-grounded query evidence; never reads benchmark answers."""
from __future__ import annotations

import json
import os
import re

from .intent_contracts import accuracy_mode
from .quality_contracts import read_schema
from .experiment_controls import mechanism_enabled


def catalog_context(complete_catalog, ranked_tables, max_chars=60000):
    """Avoid recall omissions when the complete catalog fits the prompt budget."""
    if len(complete_catalog) <= max_chars:
        return complete_catalog
    chosen = []
    size = 0
    for block in ranked_tables:
        if size + len(block) + 2 > max_chars:
            continue
        chosen.append(block)
        size += len(block) + 2
    return '\n\n'.join(chosen)


def attach_schema_metadata(selected, full_schema):
    """Retain authoritative column semantics after model-based table selection."""
    from copy import deepcopy
    result = deepcopy(selected)
    # Preserve selector suggestions in a separate trace field, not alongside
    # authoritative metadata in downstream generation/review prompts.
    result.pop('notes', None)
    result.pop('joins', None)
    catalog = read_schema(full_schema)['tables']
    catalog = {name.lower(): table for name, table in catalog.items()}
    for table in result.get('tables', []):
        source = catalog.get(table['name'].lower(), {})
        names = {str(name).lower() for name in table.get('columns', [])}
        table['column_metadata'] = {
            name: metadata for name, metadata in source.get('columns', {}).items()
            if name.lower() in names
        }
        table['primary_keys'] = source.get('primary_keys', [])
        table['foreign_keys'] = source.get('foreign_keys', [])
        table.pop('role', None)
        table['time_columns'] = [name for name, meta in table['column_metadata'].items()
                                 if re.search(r'date|time', str(meta.get('type', '')), re.I)
                                 or re.search(r'(?:year|month)$', name, re.I)]
    result['metadata_provenance'] = 'Authoritative database schema. Fact/dimension roles and filters must be decided from the current user request and explicit business contracts.'
    return result


def reconcile_table_spelling(plan, table_names):
    """Repair underscore-only typos only when one selected physical table matches."""
    declared=[str(t) for key in ('fact_tables','dimension_tables','secondary_fact_tables') for t in (plan.get(key) or [])]
    declared.append(str(plan.get('primary_fact_table','')))
    changes={}
    for name in declared:
        if not name or name in table_names:continue
        matches=[table for table in table_names if table.replace('_','').lower()==name.replace('_','').lower()]
        if len(matches)==1:changes[name]=matches[0]
    def visit(value):
        if isinstance(value,list):return [visit(item) for item in value]
        if isinstance(value,dict):return {key:visit(item) for key,item in value.items()}
        if isinstance(value,str):
            if value in changes:return changes[value]
            for wrong,right in changes.items():
                value=re.sub(r"(?<![\w'\"])"+re.escape(wrong)+r"(?![\w'\"])",right,value)
        return value
    result=visit(plan)
    if changes:result['notes']=list(result.get('notes') or [])+['Unique schema-backed table spelling corrections: '+json.dumps(changes,ensure_ascii=False)]
    return result


def scoped_schema_errors(sql, columns):
    """Resolve columns/derived aliases per SELECT scope, including reused aliases."""
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.qualify import qualify
    from sqlglot.errors import OptimizeError, ParseError, SchemaError
    try:
        tree=sqlglot.parse_one(sql,read='mysql')
        # Dictionary cursors otherwise overwrite duplicate output column names.
        outer=tree if isinstance(tree,exp.Select) else None
        if outer:
            names=[name.lower() for name in outer.named_selects if name and name!='*']
            duplicates=sorted({name for name in names if names.count(name)>1})
            if duplicates:return ['Repeated result column names require distinct aliases: '+', '.join(duplicates)]
        tree=tree.transform(lambda node: exp.to_identifier(node.name.lower()) if isinstance(node,exp.Identifier) else node)
        schema={name.lower():{column.lower():'UNKNOWN' for column in names} for name,names in columns.items()}
        qualify(tree,dialect='mysql',schema=schema,expand_stars=False,validate_qualify_columns=True)
        return []
    except (OptimizeError,ParseError,SchemaError) as exc:
        return ['Schema column resolution failed: '+str(exc)]


def entities(query):
    return list(dict.fromkeys(v.strip() for v in re.findall(r'[“”"「」]([^“”"「」]{2,100})[“”"「」]', query)))[:6]


def verified_values(query, schema, selected_tables, execute):
    """Exact quoted-entity checks on actual textual name columns, bounded to 30 probes."""
    from .lifecycle import build_value_probe, execute_readonly_probe
    evidence = []; calls = 0
    for entity in entities(query):
        for table in selected_tables:
            columns = schema.get('tables', {}).get(table, {}).get('columns', {})
            for field, meta in columns.items():
                if 'char' not in str(meta.get('type', '')).lower(): continue
                if not (field.lower().endswith('name') or '名称' in str(meta.get('comment', ''))): continue
                if calls >= 30: return evidence
                calls += 1
                sql, params = build_value_probe(table, field, entity, schema, mode='exact', limit=2)
                try:
                    rows = execute_readonly_probe(sql, params, execute)
                    if rows: evidence.append({'user_text': entity, 'field': f'{table}.{field}', 'verified_value': entity, 'match': 'exact existence', 'verified': True})
                except Exception as exc:
                    evidence.append({'field': f'{table}.{field}', 'verified': False, 'probe_error': type(exc).__name__})
    return evidence


def hierarchy_facts(query, values, schema, execute):
    # P0 does not assume hierarchy semantics from conventional column names.
    return []


def literal_probe_tables(query, schema, selected_tables, full_schema):
    """Rank a few omitted fact sources by real comments for named requests."""
    if not re.search(r'[“”「」"()（）]', query):
        return []
    from .schema_catalog import parse_full_schema_catalog
    catalog = parse_full_schema_catalog(full_schema)
    scored = []
    for name, table in schema.get('tables', {}).items():
        if name in selected_tables:
            continue
        if not any(col.lower().endswith('name') and 'char' in str(meta.get('type', '')).lower()
                   for col, meta in table.get('columns', {}).items()):
            continue
        info = catalog.get(name.lower(), {})
        comments = info.get('comment', '') + ' ' + ' '.join(info.get('column_comments', {}).values())
        terms = {word[i:i+2] for word in re.findall(r'[\u3400-\u9fff]+', comments) for i in range(len(word)-1)}
        score = sum(term in query for term in terms)
        if score:
            scored.append((-score, name))
    return [name for _, name in sorted(scored)[:4]]


def literal_name_facts(query, schema, selected_tables, execute, max_calls=8, related_tables=()):
    """Find real fact-table names occurring verbatim in the request, even unquoted."""
    from .lifecycle import execute_readonly_probe
    found = []
    calls = 0
    candidates = []
    for table in dict.fromkeys(list(selected_tables) + list(related_tables)):
        if not re.fullmatch(r'[A-Za-z_]\w*', table):
            continue
        columns = schema.get('tables', {}).get(table, {}).get('columns', {})
        for column, meta in columns.items():
            if (not column.lower().endswith('name') or 'char' not in str(meta.get('type', '')).lower()
                    or not re.fullmatch(r'[A-Za-z_]\w*', column)):
                continue
            candidates.append((table, column, columns))
    if related_tables:
        selected = [item for item in candidates if item[0] in selected_tables]
        related = [item for item in candidates if item[0] not in selected_tables]
        reserved = min(len(related), max_calls // 2)
        candidates = selected[:max_calls - reserved] + related[:reserved]
    for table, column, columns in candidates:
        if calls >= max_calls:
            return found
        calls += 1
        active = ''  # Existence evidence makes no implicit business-status assumption.
        sql = (f'SELECT DISTINCT `{column}` AS value FROM `{table}` '
               f'WHERE CHAR_LENGTH(`{column}`)>=4 AND INSTR(%s, `{column}`)>0{active} '
               f'ORDER BY CHAR_LENGTH(`{column}`) DESC LIMIT 3')
        try:
            rows = execute_readonly_probe(sql, (query,), execute)
            for row in rows:
                value = row.get('value')
                # Database collations may be insensitive; require an exact
                # Python substring before treating the spelling as evidence.
                if isinstance(value, str) and len(value) >= 4 and value in query:
                    found.append({'user_text': value, 'field': f'{table}.{column}',
                                  'verified_value': value, 'verified': True,
                                  'match': 'stored full name occurs verbatim in user request'})
        except Exception as exc:
            found.append({'field': f'{table}.{column}', 'verified': False,
                          'probe_error': type(exc).__name__})
    return found


def evidence_sql_errors(sql, dictionary, values):
    """Validate declared business join mappings and positively verified name variants."""
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
    try:
        tree=sqlglot.parse_one(sql,read='mysql')
    except sqlglot.errors.ParseError:
        return []
    mappings=re.findall(r'业务表\.([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)', dictionary)
    errors=[]
    for scope in traverse_scope(tree):
        aliases={alias.lower(): src.name.lower() for alias,src in scope.sources.items() if isinstance(src,exp.Table)}
        for node in scope.expression.find_all(exp.EQ):
            if node.find_ancestor(exp.Select) is not scope.expression:continue
            for left,right in ((node.this,node.expression),(node.expression,node.this)):
                if not isinstance(left,exp.Column):continue
                table=aliases.get(left.table.lower(),'')
                if isinstance(right,exp.Column):
                    for source_col,target_table,target_col in mappings:
                        if table==target_table.lower() and right.name.lower()==source_col.lower() and aliases.get(right.table.lower())!=table and left.name.lower()!=target_col.lower():
                            errors.append(f'Existing business dictionary requires {right.sql()} = {left.table}.{target_col}; {left.sql()} is not the declared join target.')
                elif isinstance(right,exp.Literal) and right.is_string:
                    for value in values:
                        if not value.get('verified') or value['user_text']==value['verified_value']:continue
                        if value['field'].lower()==table+'.'+left.name.lower() and right.this==value['user_text']:
                            errors.append(f'Name variant {right.this!r} has verified database spelling {value["verified_value"]!r} for {value["field"]}.')
    return list(dict.fromkeys(errors))


def advanced_rules(query, dictionary):
    """Retrieve complete dictionary sections without a hand-coded topic-to-section map."""
    from langchain_core.documents import Document
    from .retrieval import retrieve_top_documents
    sections = [s for s in re.split(r'(?m)(?=^###? )', dictionary) if s.strip()]
    return '\n\n'.join(d.page_content for d in retrieve_top_documents(query, [Document(page_content=s) for s in sections], k=4))


def review_answer_text(content, thinking=False, finish_reason=None):
    """Parse only the final answer, never a JSON draft inside a reasoning block."""
    text = str(content or '').strip()
    if thinking:
        if finish_reason == 'length':
            return ''
        if '</think>' in text:
            return text.rsplit('</think>', 1)[1].strip()
        if '<think>' in text:
            return ''
        # With a configured reasoning parser the response may already be JSON.
        if not text.startswith(('{', '```json', '```')):
            return ''
    return text


def intent_reference_examples(query, records, values, limit=2):
    """Supplement vector recall with question-only lexical intent retrieval.

    Strip only explicit or positively verified names and calendar literals.
    Rank existing questions; SQL is returned intact, never used for scoring.
    """
    if not mechanism_enabled('intent_recall'):
        return []
    import math
    from collections import Counter
    from .analysis import example_matches_query
    def normalize(text, verified=False):
        if verified:
            names = {str(v.get('verified_value', '')) for v in values if v.get('verified')}
            for name in sorted(names, key=len, reverse=True):
                if len(name) >= 2:
                    text = text.replace(name, ' ')
        text = re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"', ' ', text)
        text = re.sub(r'\d{4}\s*[-年]\s*\d{1,2}(?:\s*[-月]\s*\d{1,2}\s*[日号]?)?\s*月?|\d{4}\s*年', ' ', text)
        return text
    def terms(text):
        return {word[i:i+n] for word in re.findall(r'[\u3400-\u9fff]+', text)
                for n in (2, 3) for i in range(len(word)-n+1)}
    query_terms = terms(normalize(query, verified=True))
    rows = [(item, terms(normalize(str(item.get('question', ''))))) for item in records
            if item.get('question') and item.get('SQL') and example_matches_query(query, item['SQL'])]
    frequency = Counter(term for _, ts in rows for term in ts)
    ranked = []
    for index, (item, ts) in enumerate(rows):
        overlap = query_terms & ts
        if len(overlap) < 2:
            continue
        score = sum((len(term)-1) * math.log(1 + len(rows)/(1+frequency[term])) for term in overlap)
        score /= math.sqrt(max(1, len(ts)))
        ranked.append((-score, index, item))
    return [f"Historical question: {item['question']}\nSQL:\n{item['SQL']}"
            for _, _, item in sorted(ranked)[:limit]]


def expand_verified_name_schema(state, values, related_tables, decisions=None):
    """Expose verified full-name fact sources before planning, without pinning them."""
    # Decisions are trace-only: they are never inserted into model prompts.
    decisions = decisions if decisions is not None else []
    from .domain_query_hints import query_family_forbidden_fact_tables
    from .schema_catalog import expand_selected_schema
    selected = json.loads(state.get('domain_schema') or '{}')
    catalog = read_schema(state['full_db_schema'])['tables']
    existing = {t['name'] for t in selected.get('tables', [])}
    forbidden = set() if state.get('lifecycle_exploration') else set(query_family_forbidden_fact_tables(state))
    literal = [v for v in values if v.get('verified') and
               v.get('match') == 'stored full name occurs verbatim in user request']
    longest = max((len(v.get('verified_value', '')) for v in literal), default=0)
    additions = []
    for value in literal:
        name, _, field = value.get('field', '').partition('.')
        spelling = value.get('verified_value', '')
        query = state.get('normalized_query') or state['query']
        if not mechanism_enabled('name_expansion'):
            reason = 'ablation_disabled'
        elif state.get('lifecycle_template'):
            reason = 'fixed_contract'
        elif name in forbidden:
            reason = 'forbidden_table'
        elif name in existing or name in additions:
            reason = 'already_available'
        elif name not in catalog or field not in catalog[name].get('columns', {}):
            reason = 'unknown_schema_field'
        elif len(spelling) < 4 or spelling not in query:
            reason = 'not_verbatim_full_name'
        elif name not in related_tables:
            reason = 'outside_related_candidates'
        elif len(spelling) != longest:
            reason = 'shorter_name'
        elif len(additions) >= 2:
            reason = 'expansion_budget'
        else:
            reason = 'added_candidate'
            additions.append(name)
        decisions.append({'mechanism': 'name_expansion', 'table': name,
                          'field': value.get('field'), 'verified_value': spelling,
                          'source': 'parameterized_database_probe', 'decision': reason})
    if not additions:
        return None
    expanded, _ = expand_selected_schema({'tables': [{'name': name} for name in additions[:2]]}, state['full_db_schema'])
    selected.setdefault('tables', []).extend(expanded['tables'])
    return attach_schema_metadata(selected, state['full_db_schema'])


def enrich_query_evidence(state):
    if not accuracy_mode():return {}
    query=state.get('normalized_query') or state['query']
    schema=read_schema(state['full_db_schema'])
    selected=json.loads(state.get('domain_schema') or '{}')
    names=[t['name'] for t in selected.get('tables',[])]
    relationships=[]
    for name in names:
        table=schema['tables'].get(name,{})
        relationships.append({'table':name,'primary_keys':table.get('primary_keys',[]),'foreign_keys':table.get('foreign_keys',[]),
                              'name_fields':{col:meta for col,meta in table.get('columns',{}).items() if col.lower().endswith(('_name','_sname'))}})
    values=[]
    expanded = None
    decisions = []
    if os.getenv('NL2SQL_VALUE_PROBES_ENABLED','0')=='1':
        from .readonly_db import run_probe
        values=verified_values(query,schema,names,run_probe)
        related = literal_probe_tables(query, schema, names, state['full_db_schema'])
        values += literal_name_facts(query,schema,names,run_probe,related_tables=related)
        expanded = expand_verified_name_schema(state, values, related, decisions)
    facts='Authoritative schema keys and relationships (same-named audit fields are not automatically join keys):\n'+json.dumps(relationships,ensure_ascii=False)
    if values:
        facts+='\nValues checked by parameterized read-only existence probes (a missing match is not proof of absence):\n'+json.dumps(values,ensure_ascii=False)
        facts+='\nVerified dimension hierarchy evidence:\n'+json.dumps(hierarchy_facts(query,values,schema,run_probe),ensure_ascii=False,default=str)
    rules=advanced_rules(query,state.get('data_dictionary',''))
    result = {
        'query_evidence':facts,
        'verified_query_values':values,
        'schema_evidence_events': list(state.get('schema_evidence_events', [])) + decisions,
        'field_mapping_hints':facts+'\n\n'+state.get('field_mapping_hints',''),
        'domain_rules':rules+'\n\n'+state.get('domain_rules',''),
    }
    if expanded is not None:
        result.update(domain_schema=json.dumps(expanded, ensure_ascii=False),
                      schema_links=json.dumps(expanded, ensure_ascii=False))
    # Keep fixed-template and exploration knowledge policies unchanged.
    if not state.get('lifecycle_template') and (not state.get('lifecycle_exploration') or os.getenv('NL2SQL_EXPLORATION_GENERIC_KNOWLEDGE', '0') == '1'):
        from pathlib import Path
        from .config import QS_EXAMPLES_PATH
        if QS_EXAMPLES_PATH and Path(QS_EXAMPLES_PATH).is_file():
            try:
                records = json.loads(Path(QS_EXAMPLES_PATH).read_text(encoding='utf-8'))
                examples = state.get('domain_examples', '')
                added = [example for example in intent_reference_examples(query, records, values)
                         if example not in examples]
                if added:
                    examples += '\n\nAdditional existing references retrieved by business-intent terms (verify applicability):\n' + '\n\n====================\n\n'.join(added)
                    result.update(domain_examples=examples, few_shot_examples=examples)
            except (OSError, ValueError, TypeError):
                pass  # Supplemental recall is optional; original references remain.
    return result


def align_reference_column_order(sql, query, examples):
    """Preserve a compatible existing output interface without changing values.

    Only reorder the same complete projection over the same unique real tables.
    Ambiguous columns, nested queries, stars, positional clauses, conflicting
    references, and explicit user column-order requests all cause abstention.
    """
    if not mechanism_enabled('column_alignment'):
        return sql, False
    import sqlglot
    from sqlglot import exp
    if re.search(r'第[一二三四五六七八九十\d]+列|列顺序|依次|按顺序|顺序为', query):
        return sql, False
    def shape(text):
        tree = sqlglot.parse_one(text, read='mysql')
        if not isinstance(tree, exp.Select) or tree.args.get('with_') or len(list(tree.find_all(exp.Select))) != 1:
            return None
        tables = list(tree.find_all(exp.Table))
        names = [t.name.lower() for t in tables]
        if not names or len(set(names)) != len(names):
            return None
        aliases = {t.alias_or_name.lower(): t.name.lower() for t in tables}
        # Reordering would change positional GROUP BY / ORDER BY semantics.
        for clause in [tree.args.get('group'), tree.args.get('order')]:
            if clause and any(isinstance(n, exp.Literal) and not n.is_string for n in clause.walk()):
                return None
        signatures = []
        for projection in tree.expressions:
            expr = (projection.this if isinstance(projection, exp.Alias) else projection).copy()
            if expr.find(exp.Star):
                return None
            for col in expr.find_all(exp.Column):
                table = aliases.get(col.table.lower()) if col.table else (names[0] if len(names) == 1 else None)
                if not table:
                    return None
                col.set('table', exp.to_identifier(table))
            signatures.append(expr.sql(dialect='mysql', normalize=True))
        if len(signatures) < 2 or len(set(signatures)) != len(signatures):
            return None
        return tree, set(names), signatures
    try:
        candidate = shape(sql)
        if candidate is None:
            return sql, False
        tree, names, signatures = candidate
        orders = set()
        for block in re.split(r'Historical question:', examples)[1:]:
            if '\nSQL:\n' not in block:
                continue
            text = block.split('\nSQL:\n', 1)[1].split('\n\n====================', 1)[0]
            text = text.split('\n\nAdditional existing references', 1)[0].strip()
            try:
                reference = shape(text)
            except sqlglot.errors.ParseError:
                continue
            if reference and reference[1] == names and set(reference[2]) == set(signatures):
                orders.add(tuple(reference[2]))
        if len(orders) != 1:
            return sql, False
        order = next(iter(orders))
        if list(order) == signatures:
            return sql, False
        projections = dict(zip(signatures, tree.expressions))
        tree.set('expressions', [projections[key] for key in order])
        return tree.sql(dialect='mysql'), True
    except (sqlglot.errors.ParseError, ValueError):
        return sql, False


def review_generated_sql(state):
    if not accuracy_mode() or os.getenv('NL2SQL_SEMANTIC_REVIEW_ENABLED','0')!='1':return {}
    from langchain_core.prompts import ChatPromptTemplate
    from .llm_client import llm
    from .text_utils import parse_json_with_diagnostics
    from .schema_catalog import parse_full_schema_catalog, format_schema_catalog
    complete_catalog = format_schema_catalog(parse_full_schema_catalog(state.get('full_db_schema', '')), detailed=True)
    selected_names = {t['name'] for t in json.loads(state.get('domain_schema') or '{}').get('tables', [])}
    blocks = complete_catalog.split('\n\n')
    blocks.sort(key=lambda block: 0 if block.splitlines() and block.splitlines()[0].removeprefix('Table: ').strip() in selected_names else 1)
    review_catalog = catalog_context(complete_catalog, blocks, max_chars=int(os.getenv('NL2SQL_ACCURACY_CATALOG_MAX_CHARS', '60000')))
    prompt='''Independently audit this MySQL query against the user's actual request.
The candidate and earlier table selection can contain errors. Correct errors using ONLY the supplied schema,
verified values, and existing business rules. Never use any evaluation answer.

Check all requested output fields, row/record vs distinct entity counts, date boundaries,
primary/foreign key joins, active-row filters, and named entity spelling. A named filter
does not imply GROUP BY. TopN aggregate ranking does require GROUP BY. For a multi-measurement
request preserve each requested measurement column; do not replace it with a vertical list.
Use a full entity name unless the user requests an abbreviation; grouping by a shared short
name can incorrectly merge distinct materials or product varieties. Arithmetic across columns
of one measurement row does not require SQL GROUP BY.
Every output column must have a distinct name; alias repeated measurement fields separately.
Use verified dimension hierarchy records to distinguish parent links from same-parent siblings.
For person-level aggregation preserve the person's primary key as well as display name.
Audit the population BEFORE accepting the plan: header records and detail records are
different objects. When the requested fields exist on the requested record's own table,
do not substitute another table with similarly named fields. Use column_metadata comments
to distinguish reported, actual, planned and remaining quantities.
For EACH joined table, identify the requested output, filter or explicit contract that
requires it. Remove joins used only to check a parent/dimension's archive status when the
query requests statistics on the fact records alone. Such joins change the population.
A COUNT(DISTINCT fact.foreign_key) alone does not require joining the dimension table.
For a requested object's record identifier, distinguish its own primary key from a parent's
sample/order identifier and from its business code. Check the full catalog if its actual
table or fields were omitted from the selected schema. A corrected SQL may use an actual
table in that catalog; the subsequent schema and contract checks still apply.
Do not add classification/business-type filters merely because an example used them.
Schema-selection notes and joins are model suggestions and can themselves be incorrect.
Do not silently broaden filters to obtain data. Respect pinned quality metric contracts.
Use complete historical examples as evidence for schema relationships and established
business definitions, replacing incidental dates and entities with the current request.
When a reference describes the same kind of event, preserve category-defining predicates
unless the current request changes that category. Distinguish such mappings from incidental
filters in an unrelated example. Return JSON only: {{"sql":"SELECT ...", "changes":["brief correction"]}}.

For a historical example of the SAME requested business result, preserve its established
projection contract: contextual names/identifiers, sample counts, aliases, and column order.
Do not remove those fields solely because the current wording abbreviates the same request.
Listing some fields without an explicit ONLY restriction does not remove established context fields.
编号 does not automatically mean 编码: preserve an established record ID when the same-business
example uses it. Do not replace _ID with _Code merely because the wording says 编号.
Grouping a contextual name constrained to a single literal in WHERE is allowed for aggregates.
An explicit narrower request overrides the reference. Replace reference dates/entities/limits,
and never inherit a different metric, row population, or unrelated detail/grouped result shape.

User request: {query}
Candidate SQL: {sql}
Expanded schema: {schema}
Full database catalog for checking omitted or mistaken fact sources: {catalog}
Verified schema/value facts: {facts}
Business rules: {rules}
Historical examples: {examples}
'''
    thinking = os.getenv('NL2SQL_SEMANTIC_REVIEW_THINKING', '0') == '1'
    options = {'max_tokens': max(4096, min(32768, int(os.getenv('NL2SQL_SEMANTIC_REVIEW_MAX_TOKENS', '16384' if thinking else '4096'))))}
    if thinking:
        options['extra_body'] = {'chat_template_kwargs': {'enable_thinking': True}}
    response=(ChatPromptTemplate.from_template(prompt)|llm.bind(**options)).invoke({
        'query':state['query'],'sql':state.get('generated_sql',''),
        'schema':state.get('domain_schema',''),'catalog':review_catalog,'facts':state.get('query_evidence',''),
        'rules':state.get('domain_rules','')[:30000],'examples':state.get('domain_examples','')[:20000],
    })
    final_text = review_answer_text(response.content, thinking, response.response_metadata.get('finish_reason'))
    parsed,error=parse_json_with_diagnostics(final_text,{})
    if not isinstance(parsed,dict) or not isinstance(parsed.get('sql'),str) or not parsed['sql'].strip():
        return {'semantic_review_notes':'Review response could not be parsed; original candidate goes through normal validation. '+str(error)}
    sql, reordered = align_reference_column_order(parsed['sql'].strip(), state['query'], state.get('domain_examples', ''))
    changes = parsed.get('changes', [])
    if reordered:
        changes = (changes if isinstance(changes, list) else [str(changes)]) + ['Aligned the same complete output expressions to the unambiguous existing reference column order; no filters or values changed.']
    return {'generated_sql':sql, 'semantic_review_notes':json.dumps(changes,ensure_ascii=False)}
