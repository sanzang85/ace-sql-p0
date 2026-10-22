"""Check package inputs and dependencies; --online also probes model IDs and SELECT 1."""
import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'portable_template_llm_module'
sys.path.insert(0, str(PACKAGE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true', help='Check /v1/models and read-only SELECT 1')
    args = parser.parse_args()
    failures = []
    print(f'Python: {sys.version.split()[0]} | {sys.executable}')
    for line in (ROOT / 'requirements.txt').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        name, expected = line.split('==')
        try:
            actual = importlib.metadata.version(name)
            print(f'{name}: {actual}' + ('' if actual == expected else f' (packaged pin: {expected})'))
        except importlib.metadata.PackageNotFoundError:
            if name == 'faiss-cpu' and importlib.util.find_spec('faiss'):
                try:
                    import faiss
                    print(f'faiss: {faiss.__version__} (existing CPU/GPU-compatible installation)')
                except Exception as exc:
                    failures.append(f'faiss import failed: {type(exc).__name__}')
            else:
                failures.append(f'Missing dependency: {name}')
    expected = {'qs.json', 'qe.json', 'siames信息.md', '数据表信息.md'}
    knowledge = PACKAGE / 'data/knowledge'
    if {p.name for p in knowledge.iterdir()} != expected:
        failures.append('Knowledge directory must contain exactly four files')
    for filename in ('qs.json', 'qe.json'):
        payload = json.loads((knowledge / filename).read_text())
        print(f'{filename}: {len(payload)} records')
    for rel in ('data/inputs/q_0623.csv', 'data/ground_truth/a_0623.xlsx'):
        if not (PACKAGE / rel).is_file():
            failures.append(f'Missing benchmark input: {rel}')
    from nl2sql_template_core.profiles import apply_profile_env
    apply_profile_env('qwen38_p0', force=True)
    import os
    for env_name in ('VLLM_BASE_URL', 'VLLM_EMBEDDING_BASE_URL'):
        url = os.environ[env_name].rstrip('/')
        print(f'{env_name}: {url}')
        if args.online:
            try:
                endpoint = url if url.endswith('/v1') else url + '/v1'
                headers = {'Authorization': 'Bearer ' + os.getenv('VLLM_API_KEY', 'EMPTY')}
                request = urllib.request.Request(endpoint + '/models', headers=headers)
                with urllib.request.urlopen(request, timeout=10) as response:
                    models = json.load(response).get('data', [])
                if not models:
                    raise ValueError('No models advertised')
                print('  Models: ' + ', '.join(str(m.get('id')) for m in models))
            except Exception as exc:
                failures.append(f'{env_name}: {type(exc).__name__}')
    if args.online:
        try:
            from nl2sql_template_core.config import get_db_config
            import pymysql
            settings = get_db_config()
            settings.update(read_timeout=10, write_timeout=10)
            with pymysql.connect(**settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT 1')
                    assert cursor.fetchone()[0] == 1
            print('Database: SELECT 1 passed')
        except Exception as exc:
            failures.append(f'Database: {type(exc).__name__}')
    for error in failures:
        print('FAIL: ' + error)
    print('CHECK PASSED' if not failures else 'CHECK FAILED')
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
