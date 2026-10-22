"""Audit a standalone P0 run without accessing an adjacent V15 checkout."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_NAMES = {'qs.json', 'qe.json', 'siames信息.md', '数据表信息.md'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return Path(path).read_text(encoding='utf-8')


def audit_run(run, root=ROOT):
    run, root = Path(run).resolve(), Path(root).resolve()
    completion = json.loads(read(run / 'completion.json'))
    manifest = json.loads(read(run / 'manifest.json'))
    audits, errors = [], []
    knowledge = root / 'portable_template_llm_module/data/knowledge'
    expected_paths = {str((knowledge / name).resolve()) for name in ALLOWED_NAMES}
    if {p.name for p in knowledge.iterdir()} != ALLOWED_NAMES:
        errors.append('Knowledge directory must contain exactly the four permitted files')
    knowledge_sources = {name.removeprefix('knowledge/'): info
                         for name, info in manifest['source_files'].items()
                         if name.startswith('knowledge/')}
    if set(knowledge_sources) != ALLOWED_NAMES:
        errors.append('Run manifest must identify exactly the four knowledge files')
    for name, info in knowledge_sources.items():
        if name not in ALLOWED_NAMES or Path(info['path']).resolve() != knowledge / name:
            errors.append(f'Unexpected knowledge source: {name}')
    for qid in completion['selected_ids']:
        files = list((run / f'questions/q{qid:03d}/trace').glob('*/generation_access_audit.json'))
        if len(files) != 1:
            errors.append(f'Q{qid}: missing/duplicate access audit')
            continue
        audit = json.loads(read(files[0]))
        audits.append({'question_id': qid, **audit})
        if audit['blocked_reads'] or audit['ground_truth_read_during_generation']:
            errors.append(f'Q{qid}: forbidden generation read attempted')
        if set(audit['allowed_files']) != expected_paths:
            errors.append(f'Q{qid}: allowed files differ from this standalone package')
        if set(audit['generation_knowledge_reads']) != expected_paths:
            errors.append(f'Q{qid}: did not read exactly the four knowledge files')
    source_changes = [name for name, info in manifest['source_files'].items()
                      if not Path(info['path']).is_file() or digest(info['path']) != info['sha256']]
    return {'questions': len(audits), 'source_changes': source_changes,
            'access_violations': errors, 'access_audits': audits,
            'passed': not source_changes and not errors,
            'scope': 'Standalone P0 four-file access and run-source hash audit. '
                     'No V15 directory required; historical QS overlap is not excluded.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    result = audit_run(args.run)
    out = args.run / 'evidence_audit'
    out.mkdir(exist_ok=True)
    (out / 'p0_isolation_audit.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'access_audits'}, ensure_ascii=False, indent=2))
    if not result['passed']:
        raise SystemExit('P0 isolation audit failed')


if __name__ == '__main__':
    main()
