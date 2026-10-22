"""P0: retrieve business information exclusively from the four supplied files."""
import json
import os
import re
from typing import Dict, List
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from .intent_contracts import accuracy_mode, simple_aggregate, intent_guidance
from .analysis import example_matches_query
from .config import QE_RULES_PATH, QS_EXAMPLES_PATH
from .llm_client import llm
from .retrieval import retrieve_top_chunks, retrieve_top_documents
from .schema_catalog import parse_full_schema_catalog, format_schema_catalog, expand_selected_schema
from .state import NL2SQLState
from .text_utils import condense_context, parse_json_with_diagnostics
SCHEMA_SELECTOR_MAX_TOKENS = 2048
SCHEMA_FALLBACK_TABLE_LIMIT = 6
SCHEMA_FALLBACK_TABLE_LIMIT_BROAD = 10
def normalize_query(state: NL2SQLState) -> Dict:
    print("-> normalize_query")
    query = state["query"].strip()
    normalized = query.replace("（", "(").replace("）", ")").replace("，", ",").replace("：", ":")
    normalized = re.sub(r"\s+", " " if accuracy_mode() else "", normalized)
    return {"normalized_query": normalized}

def _merge_required_tables(selection: dict, required_tables: list[str]) -> dict:
    merged = dict(selection) if isinstance(selection, dict) else {}
    tables = list(merged.get("tables", [])) if isinstance(merged.get("tables", []), list) else []
    existing = set()
    for item in tables:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip().lower()
        else:
            name = str(item).strip().lower()
        if name:
            existing.add(name)

    required_entries = []
    for name in required_tables:
        lowered = name.strip().lower()
        if not lowered or lowered in existing:
            continue
        required_entries.append(
            {
                "name": name,
                "role": "candidate",
                "reason": "required table from deterministic or domain-specific hints",
            }
        )
        existing.add(lowered)

    merged["tables"] = required_entries + tables
    notes = list(merged.get("notes", [])) if isinstance(merged.get("notes", []), list) else []
    if required_tables:
        notes.append("prioritized required tables into schema selection")
    merged["notes"] = notes
    return merged

def enrich_rules(state):
    return {'enriched_rules': intent_guidance(state.get('normalized_query') or state['query'])}

def domain_router(state):
    return {'domain_hint': '{"domain":"generic","reason":"four-file knowledge only"}'}

def _build_schema_result(state, expanded_schema):
    from .query_evidence import attach_schema_metadata
    suggestions = {'notes': expanded_schema.get('notes', []), 'joins': expanded_schema.get('joins', []),
                   'roles': {t['name']: t.get('role') for t in expanded_schema.get('tables', [])}}
    actual = attach_schema_metadata(expanded_schema, state['full_db_schema'])
    return {'domain_schema': json.dumps(actual, ensure_ascii=False, indent=2),
            'schema_links': json.dumps(actual, ensure_ascii=False), 'field_mapping_hints': '',
            'schema_selection_suggestions': json.dumps(suggestions, ensure_ascii=False)}

def _fallback_table_selection(query, route, schema_context, business_context, full_schema_text, max_tables=6):
    names = re.findall(r'(?im)^Table:\s*([A-Za-z0-9_]+)\s*$', schema_context)
    return {'tables': [{'name': n} for n in list(dict.fromkeys(names))[:max_tables]], 'notes': ['selector parse fallback'], 'joins': []}

def retrieve_domain_schema(state):
    query = state.get('normalized_query') or state['query']
    route = state.get('domain_hint', '')
    full_schema_text = state['full_db_schema']
    schema_expansion_mode = state.get('schema_expansion_mode', '')
    broad_schema_mode = schema_expansion_mode == 'broad'
    schema_catalog = parse_full_schema_catalog(full_schema_text)
    compact_catalog = format_schema_catalog(schema_catalog, detailed=accuracy_mode())

    if accuracy_mode():
        # Keep table boundaries and column definitions intact; arbitrary chunks
        # can detach a measurement's comment from its owning table.
        catalog_docs = [Document(page_content=block) for block in compact_catalog.split('\n\n') if block.strip()]
        catalog_chunks = [doc.page_content for doc in retrieve_top_documents(f"{query}\n{route}", catalog_docs, k=12 if broad_schema_mode else 8)]
    else:
        catalog_chunks = retrieve_top_chunks(f"{query}\n{route}", compact_catalog, k=12 if broad_schema_mode else 8)
    dictionary_chunks = retrieve_top_chunks(query, state.get("data_dictionary", ""), k=8 if broad_schema_mode else 6)
    schema_context = condense_context(
        f"{query}\n{route}",
        "\n\n".join(catalog_chunks),
        max_chars=18000 if broad_schema_mode else 12000,
        chunk_size=1400,
        overlap=120,
        max_chunks=12 if broad_schema_mode else 8,
    )
    business_context = condense_context(
        query,
        "\n\n".join(dictionary_chunks),
        max_chars=5000 if broad_schema_mode else 3500,
        chunk_size=900,
        overlap=80,
        max_chunks=6 if broad_schema_mode else 4,
    )
    if accuracy_mode():
        from .query_evidence import catalog_context
        schema_context = catalog_context(compact_catalog, catalog_chunks,
                                         max_chars=int(os.getenv('NL2SQL_ACCURACY_CATALOG_MAX_CHARS', '60000')))
        if broad_schema_mode:
            feedback = state.get('plan_feedback', '')
            for table_name, table in schema_catalog.items():
                if re.search(r'(?<!\w)' + re.escape(table_name) + r'(?!\w)', feedback, re.I):
                    block = format_schema_catalog({table_name: table}, detailed=True)
                    if block not in schema_context:
                        schema_context += '\n\n' + block

    prompt = """You are a schema selector for an NL2SQL pipeline.
Choose only the tables that are necessary to answer the query.
Use the business notes to understand semantics, and use the candidate table catalog to choose real table names.

Rules:
1. Output JSON only.
2. Format must be:
   {{
     "tables": [{{"name": "real_table_name", "role": "fact|dimension", "reason": "short reason"}}],
     "joins": ["a.col = b.col"],
     "notes": ["rule 1", "rule 2"]
   }}
3. Only use table names that appear in the candidate table catalog.
4. Do not invent columns here; select tables only.
5. Prefer the smallest table set that can answer the query.
6. Do not output analysis, chain-of-thought, or any prose before/after the JSON.
7. The first character of your response must be {{ and the last character must be }}.

User query:
{query}

Domain hint:
{domain_hint}

Heuristic rules:
{enriched_rules}

Business notes:
{business_context}

Candidate table catalog:
{schema_context}

Schema expansion mode:
{schema_expansion_mode}

Previous schema/plan feedback (check actual tables and requested fields, not proxies):
{schema_feedback}
"""

    response = (ChatPromptTemplate.from_template(prompt) | llm.bind(max_tokens=SCHEMA_SELECTOR_MAX_TOKENS)).invoke(
        {
            "query": query,
            "domain_hint": route,
            "enriched_rules": state.get("enriched_rules", ""),
            "business_context": business_context,
            "schema_context": schema_context,
            "schema_expansion_mode": schema_expansion_mode,
            "schema_feedback": state.get('plan_feedback', '') if accuracy_mode() else '',
        }
    )

    parsed, _ = parse_json_with_diagnostics(response.content, {})
    if not isinstance(parsed, dict) or not parsed:
        parsed = _fallback_table_selection(
            query,
            route,
            schema_context,
            business_context,
            full_schema_text,
            max_tables=SCHEMA_FALLBACK_TABLE_LIMIT_BROAD if broad_schema_mode else SCHEMA_FALLBACK_TABLE_LIMIT,
        )
    feedback_tables = []
    if accuracy_mode() and broad_schema_mode:
        # A selector must not repeatedly discard a real table explicitly named
        # by schema validation. This widens availability, not SQL requirements;
        # the normal forbidden-table and pinned-contract checks still apply.
        feedback = state.get('plan_feedback', '')
        feedback_tables = [table['name'] for name, table in schema_catalog.items()
                           if re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', feedback, re.I)]
        from .experiment_controls import mechanism_enabled
        if feedback_tables and mechanism_enabled('feedback_retention'):
            parsed = _merge_required_tables(parsed, feedback_tables)
    expanded_schema, missing_tables = expand_selected_schema(parsed, full_schema_text)
    if missing_tables:
        expanded_schema["notes"].append(
            "Dropped tables not present in the full schema: " + ", ".join(missing_tables)
        )
    if broad_schema_mode:
        expanded_schema["notes"].append("schema retry broad mode enabled")

    result = _build_schema_result(state, expanded_schema)
    if feedback_tables:
        final_names = {t['name'] for t in expanded_schema['tables']}
        events = [{'mechanism': 'feedback_retention', 'table': name,
                   'source': 'schema_validation_feedback',
                   'decision': ('ablation_disabled' if not mechanism_enabled('feedback_retention')
                                else 'retained_candidate' if name in final_names else 'rejected_by_constraint')}
                  for name in feedback_tables]
        result['schema_evidence_events'] = list(state.get('schema_evidence_events', [])) + events
    return result


def retrieve_domain_rules(state):
    query = state.get("normalized_query") or state["query"]
    vector_notes = ""
    rule_chunks = retrieve_top_chunks(query, state.get("data_dictionary", ""), k=5)

    qe_texts: List[str] = []
    if QE_RULES_PATH and os.path.exists(QE_RULES_PATH):
        try:
            with open(QE_RULES_PATH, "r", encoding="utf-8") as file:
                qe_data = json.load(file)
            qe_docs = [f"Question: {item.get('question', '')}\nEvidence: {item.get('evidence', '')}" for item in qe_data]
            qe_texts = retrieve_top_chunks(query, "\n\n".join(qe_docs), k=3)
        except Exception:
            qe_texts = []

    combined = "\n\n".join(
        part
        for part in [
            state.get("enriched_rules", ""),
            "\n\n".join(rule_chunks + qe_texts),
            vector_notes,
        ]
        if part
    ) or state.get("enriched_rules", "")
    return {
        "domain_rules": combined,
        "evidence_docs": combined,
        "generated_evidence": combined,
    }


def retrieve_domain_examples(state):
    query = state.get("normalized_query") or state["query"]
    vector_notes = ""
    if not QS_EXAMPLES_PATH or not os.path.exists(QS_EXAMPLES_PATH):
        return {"domain_examples": vector_notes, "few_shot_examples": vector_notes}

    try:
        with open(QS_EXAMPLES_PATH, "r", encoding="utf-8") as file:
            qs_data = json.load(file)
    except Exception:
        return {"domain_examples": vector_notes, "few_shot_examples": vector_notes}

    docs = [
        f"Historical question: {item.get('question', '')}\nSQL:\n{item.get('SQL', '')}"
        for item in qs_data
        if example_matches_query(query, item.get("SQL", ""))
    ]
    if not docs:
        return {"domain_examples": vector_notes, "few_shot_examples": vector_notes}

    if accuracy_mode():
        top_examples = [doc.page_content for doc in retrieve_top_documents(query, [Document(page_content=text) for text in docs], k=1 if simple_aggregate(query) else 2)]
    else:
        top_examples = retrieve_top_chunks(query, "\n\n".join(docs), k=3)
    examples_text = "\n\n====================\n\n".join(top_examples)
    if vector_notes:
        examples_text = "\n\n".join(part for part in [examples_text, vector_notes] if part)
    return {"domain_examples": examples_text, "few_shot_examples": examples_text}
