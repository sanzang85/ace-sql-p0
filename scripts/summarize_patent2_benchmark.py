"""Produce auditable metrics and patent examples from the full benchmark traces."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'portable_template_llm_module'))
from nl2sql_template_core.quality_contracts import compare_result_values

def quality_domain(query):
    return ""  # P0 does not assign hard-coded business subdomains.



def read(path, default=None):
    return json.loads(path.read_text()) if path and path.exists() else default


def table(headers, rows):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows])


def normalized_sql(sql):
    import sqlglot
    try:
        return sqlglot.parse_one(sql, read='mysql').sql(dialect='mysql', normalize=True)
    except (sqlglot.errors.ParseError, AttributeError):
        return ' '.join(str(sql).split())


def display_sql(sql):
    import sqlglot
    try:
        return sqlglot.parse_one(sql, read='mysql').sql(dialect='mysql', pretty=True)
    except (sqlglot.errors.ParseError, AttributeError):
        return sql or '-- 未生成可执行 SQL'


def matching_projection(predicted, expected):
    """Diagnostic only: one global column mapping, preserving complete row tuples."""
    pkeys, gkeys = list(predicted[0]), list(expected[0])
    if not (len(pkeys) <= len(gkeys) <= 12 and len(gkeys) - len(pkeys) <= 3):
        return None
    candidates = [
        [gkey for gkey in gkeys if compare_result_values(
            [{'v': row[pkey]} for row in predicted], [{'v': row[gkey]} for row in expected])]
        for pkey in pkeys
    ]
    if any(not choices for choices in candidates):
        return None
    attempts = 0
    def search(chosen):
        nonlocal attempts
        if attempts >= 2000:
            return None
        if len(chosen) == len(pkeys):
            attempts += 1
            projection = [{key: row[key] for key in chosen} for row in expected]
            return chosen if compare_result_values(predicted, projection) else None
        for key in candidates[len(chosen)]:
            if key not in chosen:
                result = search(chosen + [key])
                if result is not None:
                    return result
        return None
    return search([])


def mismatch_description(evaluation):
    if not evaluation.get('evaluation_available') or evaluation.get('result_values_match'):
        return ''
    if evaluation.get('is_correct'):
        return '列名与值的对应关系一致，但输出列顺序不同；仍按保留列顺序的结果值口径计为不匹配'
    predicted = evaluation.get('predicted_data', [])
    expected = evaluation.get('correct_data', [])
    if not predicted and expected:
        return '预测无结果，标准结果非空'
    if len(predicted) != len(expected):
        return f'结果行数不同：预测 {len(predicted)}，标准 {len(expected)}'
    if predicted and expected:
        chosen = matching_projection(predicted, expected)
        if chosen is not None:
            omitted = [key for key in expected[0] if key not in chosen]
            if omitted:
                return '完整行在统一列映射后与标准子列匹配，标准另含列：' + ', '.join(omitted) + '；仅差异诊断，仍计为不匹配'
            return '全部结果行在统一列重排后匹配；仅差异诊断，仍按固定列顺序计为不匹配'
    if predicted and expected and len(predicted[0]) != len(expected[0]):
        pcols,gcols=len(predicted[0]),list(expected[0])
        if pcols<len(gcols)<=12 and len(gcols)-pcols<=3:
            from itertools import combinations
            for chosen in combinations(gcols,pcols):
                projection=[{col:row[col] for col in chosen} for row in expected]
                if compare_result_values(predicted,projection):
                    omitted=[col for col in gcols if col not in chosen]
                    return '预测结果与标准结果的子列完全匹配，标准另含列：'+', '.join(omitted)+'；仅用于差异诊断，仍计为结果值不匹配'
        return f'输出列数不同：预测 {len(predicted[0])}，标准 {len(expected[0])}；需审阅题目要求和双方 SQL'
    return '行列数量相同，列顺序或结果值不同'


def collect(run):
    records, nodes, details = [], [], {}
    gt = pd.read_excel(run / 'inputs/ground_truth.xlsx')
    difficulty = dict(zip(gt.iloc[:, 1].astype(str).str.strip(), gt.get('difficulty', pd.Series(dtype=str))))
    for proc_path in sorted(run.glob('questions/*/process.json')):
        proc = read(proc_path)
        qid = proc['question_id']
        traces = sorted((proc_path.parent / 'trace').glob('*'))
        trace = traces[-1] if traces else None
        state = read(trace / 'final_state.json' if trace else None, {})
        ev = read(trace / 'evaluation.json' if trace else None, {})
        summary_path = trace / 'summary.jsonl' if trace else None
        events = [json.loads(line) for line in summary_path.read_text().splitlines()] if summary_path and summary_path.exists() else []
        for node in events:
            nodes.append({'question_id': qid, 'node': node['node'], 'seconds': node.get('elapsed_ms', 0) / 1000,
                          'status': node.get('status'), 'error': node.get('error_message', '')})
        try:
            template = json.loads(state.get('lifecycle_template') or '{}')
            route = json.loads(state.get('domain_hint') or '{}')
        except ValueError:
            template, route = {}, {}
        generated = sorted((trace / 'nodes').glob('*generate_sql/output.json')) if trace else []
        first_sql = read(generated[0], {}).get('generated_sql', '') if generated else ''
        schema_counts = []
        reviews = []
        if trace:
            for path in sorted((trace / 'nodes').glob('*review_sql_semantics/output.json')):
                before = read(path.parent / 'input.json', {})
                after = read(path, {})
                reviewed = after.get('generated_sql')
                if reviewed and normalized_sql(before.get('generated_sql', '')) != normalized_sql(reviewed):
                    reviews.append({'before': before.get('generated_sql', ''), 'after': reviewed,
                                    'notes': after.get('semantic_review_notes', '')})
            for path in sorted((trace / 'nodes').glob('*retrieve_domain_schema/output.json')):
                raw = read(path, {}).get('domain_schema', '')
                try:
                    schema_counts.append(len(json.loads(raw).get('tables', [])))
                except (ValueError, TypeError):
                    pass
        error = ev.get('predicted_error') or state.get('error_message') or ''
        terminal_ok = proc['status'] == 'completed' and bool(state) and not error
        executed = terminal_ok and bool(state.get('validation_passed')) and any(e['node'] == 'sql_execution' for e in events)
        repair_nodes = [e['node'] for e in events if 'repair_sql' in e['node']]
        category = ('process_failure' if proc['status'] != 'completed' else
                    'plan_rejected' if error == 'Plan validation failed' else
                    'sql_or_result_rejected' if error else
                    'gt_unavailable' if not ev.get('evaluation_available') else
                    'strict_match' if ev.get('is_correct') and ev.get('result_values_match') else
                    'alias_only_match' if ev.get('result_values_match') else
                    'named_match_column_order_diff' if ev.get('is_correct') else 'value_mismatch')
        workflow_seconds = None
        if events:
            workflow_seconds = (max(datetime.fromisoformat(e['finished_at']) for e in events) -
                                min(datetime.fromisoformat(e['started_at']) for e in events)).total_seconds()
        record = {
            'question_id': qid, 'question': proc['query'], 'difficulty': difficulty.get(proc['query'], ''),
            'process_status': proc['status'], 'category': category, 'executed_successfully': executed,
            'result_check_passed': terminal_ok and bool(state.get('result_check_passed')),
            'result_check_notes': state.get('result_check_notes', ''),
            'evaluation_available': bool(ev.get('evaluation_available')),
            'strict_match': terminal_ok and bool(ev.get('is_correct')), 'result_values_match': terminal_ok and bool(ev.get('result_values_match')),
            'both_results_empty': bool(ev.get('both_results_empty')),
            'predicted_rows': len(ev.get('predicted_data', [])), 'gt_rows': len(ev.get('correct_data', [])),
            'quality_domain': quality_domain(proc['query']), 'route_domain': route.get('domain', ''),
            'template_id': template.get('template_id', ''), 'template_version': state.get('lifecycle_template_version', 0),
            'template_score': state.get('lifecycle_match_score', 0), 'exploration': bool(state.get('lifecycle_exploration')),
            'repair_calls': len(repair_nodes), 'plan_rebuilds': state.get('plan_rebuild_count', 0),
            'semantic_reviews': sum(e['node'] == 'review_sql_semantics' for e in events),
            'semantic_sql_changes': len(reviews),
            'semantic_review_notes': state.get('semantic_review_notes', ''),
            'verified_values': json.dumps(state.get('verified_query_values', []), ensure_ascii=False),
            'schema_evidence_events': json.dumps(state.get('schema_evidence_events', []), ensure_ascii=False),
            'schema_retries': state.get('schema_retry_count', 0), 'first_schema_tables': schema_counts[0] if schema_counts else None,
            'final_schema_tables': schema_counts[-1] if schema_counts else None,
            'wall_seconds': proc['wall_seconds'], 'workflow_seconds': workflow_seconds,
            'error': error, 'gt_error': ev.get('gt_execution_error') or '',
            'validation_errors': json.dumps(state.get('validation_errors', []), ensure_ascii=False),
            'plan_validation_errors': json.dumps(state.get('plan_validation_errors', []), ensure_ascii=False),
            'first_generated_sql': first_sql, 'final_sql': ev.get('predicted_sql', state.get('generated_sql', '')),
            'gt_sql': ev.get('correct_sql', ''),
            'mismatch_description': mismatch_description(ev),
            'predicted_result': json.dumps(ev.get('predicted_data', []), ensure_ascii=False),
            'gt_result': json.dumps(ev.get('correct_data', []), ensure_ascii=False),
            'trace_dir': str(trace.relative_to(run)) if trace else '',
        }
        records.append(record)
        details[qid] = {'record': record, 'state': state, 'evaluation': ev, 'events': events, 'reviews': reviews}
    return pd.DataFrame(records), pd.DataFrame(nodes), details


def metrics(frame):
    return {'questions': len(frame), 'executed': int(frame.executed_successfully.sum()),
            'result_checks_passed': int(frame.result_check_passed.sum()),
            'gt_available': int(frame.evaluation_available.sum()), 'strict_matches': int(frame.strict_match.sum()),
            'value_matches': int(frame.result_values_match.sum()), 'both_empty': int(frame.both_results_empty.sum()),
            'repaired_questions': int((frame.repair_calls > 0).sum()),
            'semantic_changed_questions': int((frame.semantic_sql_changes > 0).sum()),
            'semantic_changed_value_matches': int(((frame.semantic_sql_changes > 0) & frame.result_values_match).sum()),
            'repaired_value_matches': int(((frame.repair_calls > 0) & frame.result_values_match).sum())}


def charts(out, frame, workers=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    font_path = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    font = FontProperties(fname=str(font_path)) if font_path.exists() else FontProperties()
    plt.rcParams.update({'font.family': font.get_name(), 'axes.unicode_minus': False})
    values = [int((frame.strict_match & frame.result_values_match).sum()), int((frame.result_values_match & ~frame.strict_match).sum()),
              int((frame.executed_successfully & frame.evaluation_available & ~frame.result_values_match).sum()),
              int((~frame.executed_successfully).sum()), int((frame.executed_successfully & ~frame.evaluation_available).sum())]
    fig, ax = plt.subplots(figsize=(10, 4.8))
    bars = ax.bar(['列名及列序均匹配', '仅忽略别名后匹配', '按列序结果不匹配', '执行或校验失败', '标准答案不可用'], values,
                  color=['#2563a0', '#429e91', '#e3ae48', '#c55e5e', '#888888'])
    ax.bar_label(bars, padding=4)
    ax.set_ylabel('题数'); ax.set_title(f'新版链路实测结果（{len(frame)} 题）')
    ax.set_ylim(0, max(values + [1]) * 1.17)
    ax.text(0.0, -0.20, '结果值匹配保留列顺序、文本、NULL 和重复行；不表示 SQL 在所有数据上语义等价。',
            transform=ax.transAxes, fontsize=10)
    fig.tight_layout(); fig.savefig(out / 'results.png', dpi=180); fig.savefig(out / 'results.svg'); plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4))
    axes[0].hist(frame.wall_seconds, bins=20, color='#2563a0', edgecolor='white')
    axes[0].set_xlabel('单题进程耗时（秒）'); axes[0].set_ylabel('题数'); axes[0].set_title(f'{workers} 个请求并发时的耗时分布' if workers else '单题耗时分布')
    groups = [('质量子域', frame[frame.quality_domain.ne('')]), ('其他业务域', frame[frame.quality_domain.eq('')])]
    bars = axes[1].bar([name for name, _ in groups], [100 * part.result_values_match.mean() for _, part in groups], color=['#429e91', '#2563a0'])
    axes[1].bar_label(bars, labels=[f'{part.result_values_match.sum()}/{len(part)}' for _, part in groups], padding=4)
    axes[1].set_ylim(0, 105); axes[1].set_ylabel('结果值匹配率（%）'); axes[1].set_title('按新版质量关键词路由划分')
    fig.tight_layout(); fig.savefig(out / 'latency_domains.png', dpi=180); fig.savefig(out / 'latency_domains.svg'); plt.close(fig)


def choose_cases(frame):
    selected = []
    predicates = [
        ('基础聚合成功', frame.result_values_match & (frame.predicted_rows == 1) & (frame.repair_calls == 0)),
        ('质量查询结果匹配', frame.result_values_match & frame.quality_domain.ne('') & (frame.predicted_rows > 1)),
        ('经反馈修复后结果匹配', frame.result_values_match & (frame.repair_calls > 0) & (frame.first_generated_sql != frame.final_sql) & (frame.predicted_rows > 0)),
        ('语义复核修改 SQL，最终结果匹配', frame.result_values_match & (frame.semantic_sql_changes > 0)),
        ('事实表完整名称的只读核验', frame.result_values_match & frame.verified_values.str.contains('stored full name', regex=False, na=False)),
        ('探索路径通过通用选表取得匹配结果', frame.result_values_match & frame.exploration),
        ('生命周期模板命中', frame.template_id.ne('')),
        ('多表业务查询', frame.result_values_match & (frame.predicted_rows > 1) & frame.quality_domain.eq('')),
        ('校验拒绝或执行失败', ~frame.executed_successfully),
        ('执行成功但结果值不匹配', frame.executed_successfully & ~frame.result_values_match),
    ]
    for label, mask in predicates:
        for _, row in frame[mask].iterrows():
            if row.question_id not in [qid for _, qid in selected]:
                selected.append((label, int(row.question_id))); break
    return selected


def write_cases(out, selected, details):
    lines = ['# 专利2实测案例', '', '案例从本次已完成记录中选取，案例通过不代表全量通过；失败案例和口径差异一并保留。', '']
    for label, qid in selected:
        data = details[qid]; r = data['record']; ev = data['evaluation']
        lines += [f'## Q{qid:03d}：{label}', '', r['question'], '',
                  f"耗时 {r['wall_seconds']:.2f} 秒；SQL 修复调用 {r['repair_calls']} 次；计划重建 {r['plan_rebuilds']} 次；模板 {r['template_id'] or '未命中'}，快照版本 {r['template_version'] or '未进入固定质量模板匹配'}。", '',
                  f"探索请求：{r['exploration']}；首次候选表数 {r['first_schema_tables']}，最终候选表数 {r['final_schema_tables']}。", '',
                  f"含别名严格匹配：{r['strict_match']}；结果值匹配：{r['result_values_match']}；最终错误：{r['error'] or '无'}。", '',
                  f"[完整逐题追踪](../{r['trace_dir']}/final_state.json)", '']
        if label == '事实表完整名称的只读核验':
            verified = [v for v in json.loads(r['verified_values']) if v.get('verified')]
            lines += ['参数化只读查询确认的名称证据：', '', '```json', json.dumps(verified, ensure_ascii=False, indent=2), '```', '']
        if (r['repair_calls'] or r['semantic_reviews']) and normalized_sql(r['first_generated_sql']) != normalized_sql(r['final_sql']):
            lines += ['首次生成 SQL：', '', '```sql', display_sql(r['first_generated_sql']), '```', '']
            repair_feedback = []
            for e in data['events']:
                if 'repair_sql' in e['node']:
                    p = out.parent / r['trace_dir'] / 'nodes' / f"{e['seq']:03d}_{e['node']}" / 'input.json'
                    st = read(p, {})
                    repair_feedback.append({k: st.get(k) for k in ('validation_errors', 'result_check_notes', 'precheck_error_message') if st.get(k)})
            if repair_feedback:
                lines += ['修复前反馈（原始追踪）：', '', '```json', json.dumps(repair_feedback, ensure_ascii=False, indent=2), '```', '']
            if data['reviews']:
                notes = []
                for item in data['reviews']:
                    try:
                        parsed_notes = json.loads(item['notes'])
                    except (ValueError, TypeError):
                        parsed_notes = item['notes']
                    notes.extend(parsed_notes if isinstance(parsed_notes, list) else [parsed_notes])
                lines += ['独立语义复核的实际修改记录（已排除纯格式变化）：', '']
                lines += [f'- {note}' for note in notes if note]
                lines += ['']
        lines += ['最终 SQL：', '', '```sql', display_sql(r['final_sql']), '```', '',
                  '标准 SQL：', '', '```sql', display_sql(r['gt_sql']), '```', '',
                  f"预测结果（共 {r['predicted_rows']} 行，展示前 10 行）：", '', '```json',
                  json.dumps(ev.get('predicted_data', [])[:10], ensure_ascii=False, indent=2), '```', '',
                  f"标准结果（共 {r['gt_rows']} 行，展示前 10 行）：", '', '```json',
                  json.dumps(ev.get('correct_data', [])[:10], ensure_ascii=False, indent=2), '```', '']
        if r['template_id'] and not r['result_values_match']:
            lines += ['解释边界：模板命中与合同校验通过，均不能替代问题语义或业务过滤条件的正确性。本案例不得作为正确答案样例。', '']
    (out / '专利展示案例.md').write_text('\n'.join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--partial', action='store_true')
    parser.add_argument('--no-charts', action='store_true', help='Write tables and reports without matplotlib charts')
    args = parser.parse_args(); run = args.run_dir.resolve()
    manifest = read(run / 'manifest.json')
    frame, nodes, details = collect(run)
    if frame.empty:
        raise SystemExit('No completed process records')
    selection = read(run / 'selection.json', {})
    expected_ids = set(selection.get('ids', range(1, manifest['question_count'] + 1)))
    if not args.partial and set(frame.question_id) != expected_ids:
        raise SystemExit(f'Incomplete selection: {len(frame)}/{len(expected_ids)}')
    out = run / ('analysis_partial' if args.partial else 'analysis'); out.mkdir(exist_ok=True)
    summary = metrics(frame)
    summary.update(category_counts=frame.category.value_counts().to_dict(),
                   difficulty_groups={name: metrics(part) for name, part in frame.groupby('difficulty')},
                   quality_subset=metrics(frame[frame.quality_domain.ne('')]),
                   other_subset=metrics(frame[frame.quality_domain.eq('')]),
                   template_matches=int(frame.template_id.ne('').sum()), exploration=int(frame.exploration.sum()),
                   template_ids=frame[frame.template_id.ne('')].template_id.value_counts().to_dict(),
                   process_status=frame.process_status.value_counts().to_dict(),
                   latency={k: float(v) for k, v in {'mean': frame.wall_seconds.mean(), 'median': frame.wall_seconds.median(),
                             'p95': frame.wall_seconds.quantile(.95), 'min': frame.wall_seconds.min(), 'max': frame.wall_seconds.max()}.items()},
                   completion=read(run / 'completion.json', {}))
    frame.to_csv(out / '逐题结果.csv', index=False, encoding='utf-8-sig')
    frame.to_json(out / '逐题结果.json', orient='records', force_ascii=False, indent=2)
    nodes.to_csv(out / '节点耗时.csv', index=False, encoding='utf-8-sig')
    aggregate = nodes.groupby('node').seconds.agg(['count', 'sum', 'mean', 'median', 'max']).sort_values('sum', ascending=False)
    aggregate.to_csv(out / '节点耗时汇总.csv', encoding='utf-8-sig')
    with pd.ExcelWriter(out / '评测结果.xlsx') as writer:
        frame.to_excel(writer, sheet_name='逐题结果', index=False)
        pd.DataFrame([metrics(frame)]).to_excel(writer, sheet_name='总体指标', index=False)
        frame[~frame.result_values_match].to_excel(writer, sheet_name='未匹配或失败', index=False)
        aggregate.to_excel(writer, sheet_name='节点耗时')
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    selected = choose_cases(frame)
    write_cases(out, selected, details)
    charts_written = False
    if not args.partial and not args.no_charts:
        try:
            import matplotlib
        except ImportError:
            print('matplotlib not installed; writing tabular reports without charts.', file=sys.stderr)
        else:
            charts(out, frame, manifest['workers'])
            charts_written = True
    pct = lambda count, total: f'{count}/{total}（{100 * count / total:.2f}%）' if total else '不可用'
    m = summary; total = len(frame); qual = m['quality_subset']
    profile_env = manifest['profile_settings']['env']
    thinking_description = (f"计划与初次生成关闭 thinking；独立语义复核启用 thinking，上限 {profile_env.get('NL2SQL_SEMANTIC_REVIEW_MAX_TOKENS', '16384')} token"
                            if profile_env.get('NL2SQL_SEMANTIC_REVIEW_THINKING') == '1' else '关闭 thinking')
    lines = [f'# 专利2：Qwen3.8-27B 新版链路 {total} 题实测报告' + ('（部分记录，非全量结论）' if args.partial else ''), '',
             f"本报告包含 {total} 道已结束题目。模型为 Qwen3.8-27B，Embedding 为 Qwen3-Embedding-8B；模板版本 {manifest['template_version']}，实验期间不演化。", '',
             '## 实测结果', '', table(['指标', '实测值'], [
                 ['SQL 通过校验且执行成功', pct(m['executed'], total)],
                 ['最终结果检查通过', pct(m['result_checks_passed'], total)],
                 ['标准 SQL 可执行', pct(m['gt_available'], total)],
                 ['列名—值对应关系匹配（忽略行序和列序）', pct(m['strict_matches'], total)],
                 ['忽略别名、保留列顺序的结果值匹配', pct(m['value_matches'], total)],
                 ['双方均为空结果', m['both_empty']], ['质量子域结果值匹配', pct(qual['value_matches'], qual['questions'])],
                 ['生命周期模板命中', m['template_matches']], ['探索请求', m['exploration']],
                 ['触发 SQL 修复的题目', m['repaired_questions']], ['修复题目中最终结果值匹配', m['repaired_value_matches']],
                 ['语义复核实际修改 SQL 的题目（排除纯格式变化）', m['semantic_changed_questions']],
                 ['上述语义复核修改题目中最终结果值匹配', m['semantic_changed_value_matches']],
                 ['单题耗时：平均 / 中位 / P95（秒）', ' / '.join(f"{m['latency'][k]:.2f}" for k in ('mean', 'median', 'p95'))],
             ]), '', table(['难度', '题数', '结果值匹配', '含别名严格匹配'], [
                 [name, group['questions'], pct(group['value_matches'], group['questions']), pct(group['strict_matches'], group['questions'])]
                 for name, group in m['difficulty_groups'].items()
             ]), '', '![全量结果](results.png)', '', '![耗时及分域结果](latency_domains.png)', '',
             '## 数据、配置与评测口径', '',
             '题目、标准答案和模板采用本轮 inputs/ 中的固定副本；实际来源见 manifest.json。输入、答案、模式、规则、代码和模板库的 SHA-256 均记录在 ../manifest.json，输入与初始模板库另有副本。', '',
             f"Chat：{profile_env['VLLM_BASE_URL']}；Embedding：{profile_env['VLLM_EMBEDDING_BASE_URL']}。{thinking_description}，常规生成上限 {profile_env['VLLM_MAX_TOKENS']} token；并发 {manifest['workers']}；每题超时 {manifest['per_question_timeout_seconds']} 秒；数据库只读、查询超时 15 秒、最多 5000 行。", '',
             '标准 SQL 在生成流程完成后由评测模块执行，不进入本次查询的生成提示。已有项目示例库仍参与检索，因此这是项目内回归评测，不是独立保留集评测。现有历史示例包含与多道评测题语义高度相近、仅表述或参数不同的条目；回归结果不能代表独立新题上的泛化能力。', '',
             '含列名匹配比较列名与值的对应关系，保留数值、文本、NULL 和重复行，忽略行序和列序（内部字段名 strict_match）。结果值匹配忽略列别名，但保留输出列顺序、文本、NULL、重复行，不做数值舍入，不删除文本列。因此两种指标并非严格包含关系：列名和值相同但列序交换时，前者通过、后者失败。图表按结果值口径互斥分组。指标分母使用全部题目；标准答案不可用单列报告。双方空集单列报告，空集匹配不能证明 SQL 语义正确；排序、TopN 及业务语义仍需结合 SQL 审阅。数据库为在线数据，无整个实验的一致性快照。', '',
             'P0 仅使用 qs.json、qe.json、siames信息.md、数据表信息.md；无业务模板、生命周期种子或固定业务表映射。Embedding 用于四文件内容检索。', '',
             '## 失败分布与应用边界', '', table(['分类', '题数'], list(m['category_counts'].items())), '',
             '执行成功与语义正确分别统计。本单轮报告不估计相对提升；如另有完整同题目、同答案、同评分口径的配对报告，只报告两轮实测差值，不归因于某个模块、并发调整或自演化。事件只作观察，未使用本批标准答案训练模板或自动晋升示例。命中模板的统计范围、归档过滤和新增维度仍须逐项核验。', '',
             '## 可复核案例与产物', '',
             '[专利展示案例](专利展示案例.md)包含成功、修复和失败案例的 SQL、结果和追踪路径；[评测结果.xlsx](评测结果.xlsx)和[逐题结果.csv](逐题结果.csv)覆盖全部题目。逐题目录保留完整节点输入/输出、控制台日志、首次及最终 SQL、标准 SQL 与结果。', '',
             '## 专利中可采用的表述', '',
             f"在本项目的 {total} 道回归查询上，使用 Qwen3.8-27B 与 Qwen3-Embedding-8B 运行包含模板快照、查询计划、SQL 校验、只读执行及结果反馈的转换链路。{m['executed']} 道 SQL 通过校验且执行成功，{m['result_checks_passed']} 道通过最终结果检查；{m['value_matches']} 道在忽略列别名但保留列顺序、完整值及重复行的口径下与标准 SQL 结果匹配，其中 {m['both_empty']} 道双方为空集；包含列别名的严格匹配为 {m['strict_matches']} 道。该结果用于说明当前实施例的运行行为，不用于推断未测业务或相对基线的提升。", '']
    if (run / 'review_replay/复核重放报告.md').exists():
        lines += ['## 语义复核的独立重放证据', '',
                  '[复核重放报告](../review_replay/复核重放报告.md)对实际修改前后 SQL、最终 SQL 与标准 SQL 作独立只读重放，区分新增匹配、保持匹配和回退。它不改变本轮评分，不将所有修改都计作有效纠错。', '']
    if not charts_written:
        lines = [line for line in lines if not line.startswith('![')]
    (out / '实验报告.md').write_text('\n'.join(lines))
    from export_patent2_answers import export_answers
    export_answers(frame, run, out)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
