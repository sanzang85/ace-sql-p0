"""Run the frozen patent2 pipeline in isolated, resumable per-question processes.

Generation reads no GT; the normal CLI evaluates only after workflow completion.
Each process has its own Excel and trace directory. Only four business knowledge
files are permitted; sources are frozen and no template store exists.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "portable_template_llm_module"


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")
    tmp.replace(path)


def prepare(run, profile, input_path, gt_path, workers, timeout, ablation="none"):
    run.mkdir(parents=True, exist_ok=True)
    manifest_path = run / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["profile"] != profile:
            raise ValueError("Cannot resume with a different profile")
        if manifest.get("ablation", "none") != ablation:
            raise ValueError("Cannot resume with a different ablation")
        for key, path in (("questions", input_path), ("ground_truth", gt_path)):
            if digest(path) != manifest["source_files"][key]["sha256"]:
                raise ValueError(f"Cannot resume with different {key}")
        for name, info in manifest["source_files"].items():
            if digest(info["path"]) != info["sha256"]:
                raise ValueError(f"Source changed since run was frozen: {name}")
        return manifest
    q = pd.read_csv(input_path).iloc[:, 0].astype(str).str.strip()
    gt = pd.read_excel(gt_path)
    matched = gt.iloc[:, 1].astype(str).str.strip()
    if q.duplicated().any() or matched.duplicated().any() or not q.isin(matched).all():
        raise ValueError("Require unique questions with exactly matching GT questions")
    (run / "inputs").mkdir()
    source_files = {}
    paths = {
        "questions": input_path, "ground_truth": gt_path,
        "runner": Path(__file__).resolve(),
        "entrypoint": ROOT / "ace_sql_patent2.py",
        "profiles": PACKAGE / "data/config/model_profiles.json",
    }
    for path in sorted((ROOT / "scripts").glob("*")):
        if path.is_file():
            paths[f"scripts/{path.name}"] = path
    for path in sorted(ROOT.glob("*.sh")):
        paths[f"launchers/{path.name}"] = path
    for path in sorted((ROOT / "tests").glob("*.py")):
        paths[f"tests/{path.name}"] = path
    paths["experiment_plan"] = ROOT / "EXPERIMENT_PLAN.md"
    for path in sorted((PACKAGE / "nl2sql_template_core").glob("*.py")):
        paths[f"code/{path.name}"] = path
    for path in sorted((PACKAGE / "data/knowledge").rglob("*")):
        if path.is_file() and path.suffix in {".md", ".json"} and "vector_index" not in path.parts:
            paths[f"knowledge/{path.relative_to(PACKAGE / 'data/knowledge')}"] = path
    for name, path in paths.items():
        path = Path(path).resolve()
        source_files[name] = {"path": str(path), "sha256": digest(path)}
    shutil.copy2(input_path, run / "inputs/questions.csv")
    shutil.copy2(gt_path, run / "inputs/ground_truth.xlsx")
    profile_data = json.loads(paths["profiles"].read_text())["profiles"][profile]
    manifest = {
        "created_at": stamp(), "profile": profile, "profile_settings": profile_data,
        "ablation": ablation, "knowledge_mode": "four_files",
        "python": sys.executable, "question_count": len(q), "workers": workers,
        "per_question_timeout_seconds": timeout, "source_files": source_files,
        "template_version": 0,
        "template_ids": [],
        "request_id_scheme": "patent2-benchmark:qNNN (stable across runs)",
        "evaluation": "Strict named columns, exact text/NULL/duplicate rows, ignore row order; alias-independent diagnostics reported separately.",
        "scope": "Existing project regression set; no claims of independent held-out generalization or causal improvement.",
    }
    write_json(manifest_path, manifest)
    return manifest


def run_one(run, profile, index, query, timeout, ablation="none"):
    case = run / "questions" / f"q{index:03d}"
    case.mkdir(parents=True, exist_ok=True)
    result_path = case / "process.json"
    if result_path.exists():
        return json.loads(result_path.read_text())
    env = os.environ.copy()
    env.update(
               NL2SQL_MECHANISM_ABLATION=ablation,
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               PYTHONUNBUFFERED="1")
    command = [sys.executable, str(ROOT / "ace_sql_patent2.py"), "query",
               "--profile", profile, "--query", query,
               "--request-id", f"patent2-benchmark:q{index:03d}",
               "--thread", f"patent2-q{index:03d}",
               "--output", str(case / "report.md"),
               "--gt_file", str(run / "inputs/ground_truth.xlsx"),
               "--eval_output", str(case / "evaluation.xlsx"),
               "--trace_dir", str(case / "trace")]
    result = {"question_id": index, "query": query, "started_at": stamp(), "command": command}
    start = time.monotonic()
    try:
        with (case / "console.log").open("w") as log:
            proc = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=timeout)
        result.update(returncode=proc.returncode, status="completed" if proc.returncode == 0 else "process_error")
    except subprocess.TimeoutExpired:
        result.update(returncode=None, status="timeout")
    except Exception as exc:
        result.update(returncode=None, status="launcher_error", error=str(exc))
    result.update(finished_at=stamp(), wall_seconds=round(time.monotonic() - start, 3))
    write_json(result_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--profile", default="qwen38_p0")
    parser.add_argument("--input", default=str(PACKAGE / "data/inputs/q_0623.csv"))
    parser.add_argument("--gt", default=str(PACKAGE / "data/ground_truth/a_0623.xlsx"))
    parser.add_argument("--ids", help="Comma-separated question IDs; omitted means all")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=2700)
    sys.path.insert(0, str(PACKAGE))
    from nl2sql_template_core.experiment_controls import ABLATIONS
    parser.add_argument("--ablation", choices=sorted(ABLATIONS), default="none")
    args = parser.parse_args()
    run = Path(args.run_dir).resolve()
    manifest = prepare(run, args.profile, Path(args.input), Path(args.gt), args.workers, args.timeout, args.ablation)
    queries = pd.read_csv(run / "inputs/questions.csv").iloc[:, 0].astype(str).str.strip()
    ids = [int(i) for i in args.ids.split(",")] if args.ids else list(range(1, len(queries) + 1))
    if len(set(ids)) != len(ids) or any(i < 1 or i > len(queries) for i in ids):
        raise ValueError("Invalid question IDs")
    selection = run / "selection.json"
    if selection.exists() and json.loads(selection.read_text())["ids"] != ids:
        raise ValueError("Cannot resume with different selected question IDs")
    write_json(selection, {"ids": ids, "ablation": args.ablation})
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_one, run, args.profile, i, queries.iloc[i - 1], args.timeout, args.ablation) for i in ids]
        for n, future in enumerate(as_completed(futures), 1):
            r = future.result()
            print(f"[{stamp()}] {n}/{len(ids)} q{r['question_id']:03d} {r['status']} {r['wall_seconds']:.1f}s", flush=True)
    for name, info in manifest['source_files'].items():
        if digest(info['path']) != info['sha256']:
            raise RuntimeError('Frozen source changed: ' + name)
    write_json(run / "completion.json", {"finished_at": stamp(), "selected_ids": ids,
               "batch_wall_seconds": time.monotonic() - started,
               "template_version_unchanged": True, "knowledge_mode": "four_files", "source_unchanged": True})
    print(f"Artifacts: {run}", flush=True)


if __name__ == "__main__":
    main()
