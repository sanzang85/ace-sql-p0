"""Prepare uniquely matched external evaluation inputs without changing source files."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect(input_path, gt_path):
    questions = pd.read_csv(input_path, keep_default_na=False)
    gt = pd.read_excel(gt_path, keep_default_na=False)
    if questions.shape[1] < 1 or gt.shape[1] < 3:
        raise ValueError('Require CSV question column and XLSX ID/question/SQL first three columns')
    q = questions.iloc[:, 0].astype(str).str.strip()
    gq = gt.iloc[:, 1].astype(str).str.strip()
    if not len(q) or (q == '').any() or q.duplicated().any():
        raise ValueError('Input questions must be nonempty and unique after stripping whitespace')
    records, issues, duplicates = [], [], []
    for text, rows in gt.groupby(gq, sort=False):
        if len(rows) > 1:
            duplicates.append({'question': text, 'source_ids': rows.iloc[:, 0].astype(str).tolist(),
                               'distinct_sql_count': rows.iloc[:, 2].astype(str).str.strip().nunique()})
    for index, text in enumerate(q, 1):
        rows = gt[gq == text]
        sqls = rows.iloc[:, 2].astype(str).str.strip() if len(rows) else pd.Series(dtype=str)
        reason = ('missing_ground_truth' if rows.empty else
                  'conflicting_ground_truth_sql' if sqls.nunique() != 1 else
                  'empty_ground_truth_sql' if not sqls.iloc[0] else None)
        if reason:
            issues.append({'original_question_id': index, 'question': text, 'reason': reason,
                           'ground_truth_source_ids': rows.iloc[:, 0].astype(str).tolist()})
            continue
        # Duplicate reference rows are acceptable only if stripped SQL text is identical.
        row = rows.iloc[0]
        difficulty_col = 'difficulty' if 'difficulty' in gt else '难度等级' if '难度等级' in gt else None
        levels = rows[difficulty_col].astype(str).unique().tolist() if difficulty_col else []
        records.append({'original_question_id': index, 'question': text, 'SQL': sqls.iloc[0],
                        'difficulty': levels[0] if len(levels) == 1 else '',
                        'ground_truth_source_ids': rows.iloc[:, 0].astype(str).tolist()})
    return records, {'input_questions': len(q), 'ground_truth_rows': len(gt),
                     'uniquely_scorable_questions': len(records), 'unscorable_questions': issues,
                     'duplicate_reference_questions': duplicates}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--gt', required=True, type=Path)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--exclude-unscorable', action='store_true')
    args = parser.parse_args()
    records, report = inspect(args.input, args.gt)
    report.update({'input_path': str(args.input.resolve()), 'input_sha256': sha(args.input),
                   'gt_path': str(args.gt.resolve()), 'gt_sha256': sha(args.gt),
                   'exclude_unscorable': args.exclude_unscorable,
                   'scope': 'Exact stripped question matching only; no fuzzy answer assignment. '
                            'New input paths do not themselves establish independent holdout status.'})
    folder = args.run_dir.resolve() / 'prepared_inputs'
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder / 'preparation_report.json'
    # Never change prepared inputs beneath an existing benchmark manifest.
    if (args.run_dir / 'manifest.json').exists():
        old = json.loads(report_path.read_text(encoding='utf-8'))
        if old != report:
            raise ValueError('Dataset or exclusion settings changed; use a new RUN_DIR')
    else:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('Preparation report: ' + str(report_path))
    if report['unscorable_questions'] and not args.exclude_unscorable:
        print('BLOCKED: fix source mapping or explicitly use --exclude-unscorable with a new run directory.')
        return 2
    if not records:
        raise ValueError('No uniquely scorable questions')
    if args.check_only:
        print('CHECK PASSED; no model calls or database queries made.')
        return 0
    if not (args.run_dir / 'manifest.json').exists():
        pd.DataFrame({'question': [r['question'] for r in records]}).to_csv(
            folder / 'questions.csv', index=False, encoding='utf-8-sig')
        pd.DataFrame([{'序号': i, 'question': r['question'], 'SQL': r['SQL'], 'difficulty': r['difficulty']}
                      for i, r in enumerate(records, 1)]).to_excel(folder / 'ground_truth.xlsx', index=False)
        pd.DataFrame([{'run_question_id': i, **r} for i, r in enumerate(records, 1)]).drop(
            columns=['SQL']).to_csv(folder / 'question_id_mapping.csv', index=False, encoding='utf-8-sig')
    print(f'Prepared {len(records)} questions; original source files unchanged.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
