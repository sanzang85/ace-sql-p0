"""Enforce and record the four-file knowledge boundary during generation."""
import os
from pathlib import Path
import sys

ALLOWED_NAMES = {'qs.json', 'qe.json', 'siames信息.md', '数据表信息.md'}

class GenerationBoundary:
    def __init__(self, knowledge_dir, gt_file):
        self.root = Path(knowledge_dir).resolve()
        self.allowed = {self.root / name for name in ALLOWED_NAMES}
        self.gt_file = Path(gt_file).resolve()
        self.reads = set()
        self.blocked = []
        self.active = False

    def audit(self, event, args):
        if not self.active or event != 'open' or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        if path == self.gt_file or 'ground_truth' in path.parts:
            self.blocked.append(str(path))
            raise PermissionError('P0 generation cannot read ground truth')
        if 'knowledge' in path.parts and path.suffix.lower() in {'.json', '.md', '.pkl', '.faiss'}:
            if path not in self.allowed:
                self.blocked.append(str(path))
                raise PermissionError('P0 generation permits only four knowledge files: ' + str(path))
            self.reads.add(str(path))

    def __enter__(self):
        actual = {p.resolve() for p in self.root.rglob('*') if p.is_file()}
        if actual != self.allowed:
            raise ValueError('P0 knowledge directory must contain exactly the four allowed files')
        sys.addaudithook(self.audit)
        self.active = True
        return self

    def __exit__(self, *exc):
        self.active = False

    def report(self):
        return {'knowledge_mode': 'four_files', 'allowed_files': sorted(map(str, self.allowed)),
                'generation_knowledge_reads': sorted(self.reads), 'blocked_reads': self.blocked,
                'ground_truth_read_during_generation': False,
                'scope': 'Python file opens during workflow; evaluation begins after this boundary closes'}
