"""Extract candidate-update evidence without reading GT during generation."""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    scores = {r['question_id']: r for r in read(run / 'analysis/逐题结果.json')}
    completion = read(run / 'completion.json')
    if set(scores) != set(completion['selected_ids']):
        raise ValueError('Incomplete evaluation')
    updates, violations, nodes_checked = [], [], 0
    event_counts, event_questions = Counter(), {}
    forbidden_keys = {'correct_sql', 'ground_truth_sql', 'correct_data', 'ground_truth_data'}
    for qid in sorted(scores):
        traces = list((run / f'questions/q{qid:03d}/trace').glob('*/final_state.json'))
        if len(traces) != 1:
            raise ValueError(f'Expected one trace for {qid}')
        final_path = traces[0]
        state = read(final_path)
        for node in sorted((final_path.parent / 'nodes').iterdir()):
            for filename in ('input.json', 'output.json'):
                path = node / filename
                if not path.exists():
                    continue
                content = read(path)
                nodes_checked += 1
                leaked = sorted(forbidden_keys.intersection(content))
                if leaked:
                    violations.append({'question_id': qid, 'path': str(path), 'keys': leaked})
            if node.name.endswith('_enrich_query_evidence') and (node / 'output.json').exists():
                before, after = read(node / 'input.json'), read(node / 'output.json')
                if 'domain_schema' in after:
                    tables = lambda value: [t['name'] for t in json.loads(value or '{}').get('tables', [])]
                    old, new = tables(before.get('domain_schema')), tables(after['domain_schema'])
                    added = sorted(set(new) - set(old))
                    if added:
                        updates.append({'question_id': qid, 'before': old, 'after': new, 'added': added,
                                        'verified_values': after.get('verified_query_values', []),
                                        'result_values_match': scores[qid]['result_values_match'],
                                        'sql': scores[qid]['final_sql'], 'node': str(node)})
        for event in state.get('schema_evidence_events', []):
            key = event['mechanism'] + ':' + event['decision']
            event_counts[key] += 1
            event_questions.setdefault(key, set()).add(qid)
    manifest = read(run / 'manifest.json')
    unchanged = all(hashlib.sha256(Path(info['path']).read_bytes()).hexdigest() == info['sha256']
                    for info in manifest['source_files'].values())
    result = {'questions': len(scores), 'generation_state_files_checked': nodes_checked,
              'answer_field_violations': violations, 'frozen_source_hashes_unchanged': unchanged,
              'event_counts': dict(event_counts),
              'event_questions': {key: sorted(value) for key, value in event_questions.items()},
              'name_expansions': updates,
              'scope': 'Trace field audit and frozen-source audit; absence of these keys alone is not a proof against all possible leakage.'}
    output = run / 'evidence_audit'
    output.mkdir(exist_ok=True)
    (output / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    lines = ['# 候选模式更新证据审计', '',
             f"审阅 {len(scores)} 道题、{nodes_checked} 个节点输入/输出文件；标准答案字段违规 {len(violations)} 项；冻结源文件哈希保持：{unchanged}。", '',
             '此检查核对节点状态中是否出现标准答案字段，结合代码读取顺序和输入清单评估隔离；不将字段不存在视作所有潜在泄漏的数学证明。', '',
             '| 机制及决策 | 事件条数 | 涉及题数 | 题号 |', '| --- | --- | --- | --- |']
    for key, count in sorted(event_counts.items()):
        qs = sorted(event_questions[key])
        lines.append(f'| {key} | {count} | {len(qs)} | {qs} |')
    lines += ['', '## 实际生成前候选扩展', '', '以下逐条来自节点输入/输出，记录发生扩展的所有情况，包括最终仍不匹配的情况。', '']
    for item in updates:
        lines += [f"### Q{item['question_id']}", '',
                  f"原候选：`{item['before']}`。新增：`{item['added']}`。最终值匹配：{item['result_values_match']}。", '',
                  f"原始节点：`{item['node']}`。", '', '```sql', item['sql'], '```', '']
    (output / '候选更新证据审计.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('name_expansions', 'event_questions')}, ensure_ascii=False, indent=2))
    if violations or not unchanged:
        raise SystemExit('Evidence audit failed')


if __name__ == '__main__':
    main()
