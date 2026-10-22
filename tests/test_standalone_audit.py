import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/audit_p0.py'
SPEC = importlib.util.spec_from_file_location('standalone_audit', SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class StandaloneAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'standalone package'
        self.knowledge = self.root / 'portable_template_llm_module/data/knowledge'
        self.knowledge.mkdir(parents=True)
        sources = {}
        for name in AUDIT.ALLOWED_NAMES:
            path = self.knowledge / name
            path.write_text('[]')
            sources['knowledge/' + name] = {'path': str(path), 'sha256': AUDIT.digest(path)}
        self.run = self.root / 'outputs/test'
        trace = self.run / 'questions/q001/trace/run1'
        trace.mkdir(parents=True)
        (self.run / 'manifest.json').write_text(json.dumps({'source_files': sources}))
        (self.run / 'completion.json').write_text(json.dumps({'selected_ids': [1]}))
        self.access = trace / 'generation_access_audit.json'
        files = [str(self.knowledge / name) for name in AUDIT.ALLOWED_NAMES]
        self.access.write_text(json.dumps({'allowed_files': files, 'generation_knowledge_reads': files,
                                          'blocked_reads': [], 'ground_truth_read_during_generation': False}))

    def test_runs_without_v15_or_original_checkout(self):
        self.assertTrue(AUDIT.audit_run(self.run, self.root)['passed'])

    def test_detects_changed_knowledge_and_forbidden_access(self):
        (self.knowledge / 'qs.json').write_text('[1]')
        access = json.loads(self.access.read_text())
        access['blocked_reads'] = ['/tmp/ground_truth/answers.xlsx']
        self.access.write_text(json.dumps(access))
        result = AUDIT.audit_run(self.run, self.root)
        self.assertFalse(result['passed'])
        self.assertIn('knowledge/qs.json', result['source_changes'])
        self.assertTrue(result['access_violations'])

    def test_rejects_extra_knowledge_and_missing_trace(self):
        (self.knowledge / 'templates.json').write_text('{}')
        self.access.unlink()
        result = AUDIT.audit_run(self.run, self.root)
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['access_violations']), 2)


if __name__ == '__main__':
    unittest.main()
