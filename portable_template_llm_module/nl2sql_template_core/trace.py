from __future__ import annotations

import json
import os
import re
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Dict

from .runtime import TRACE_RUNTIME
from .state import NL2SQLState


def trace_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        return f"<max_depth:{type(value).__name__}>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        max_chars = TRACE_RUNTIME.max_string_chars
        if max_chars and len(value) > max_chars:
            return value[:max_chars] + f"\n...<truncated {len(value) - max_chars} chars>"
        return value
    if isinstance(value, dict):
        return {str(k): trace_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [trace_jsonable(v, depth + 1) for v in value]
    try:
        return json.loads(json.dumps(value, default=str, ensure_ascii=False))
    except Exception:
        return repr(value)


def trace_summary(value: Any) -> Any:
    if isinstance(value, str):
        preview = value.replace("\n", "\\n")
        if len(preview) > 240:
            preview = preview[:240] + "..."
        return {"type": "str", "chars": len(value), "preview": preview}
    if isinstance(value, dict):
        return {"type": "dict", "keys": list(value.keys()), "size": len(value)}
    if isinstance(value, list):
        return {"type": "list", "items": len(value), "preview": trace_jsonable(value[:3])}
    return {"type": type(value).__name__, "value": trace_jsonable(value)}


def write_trace_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(trace_jsonable(payload), file, ensure_ascii=False, indent=2, default=str)


def append_trace_jsonl(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(trace_jsonable(payload), ensure_ascii=False, default=str) + "\n"
    with TRACE_RUNTIME.lock:
        with open(path, "a", encoding="utf-8") as file:
            file.write(line)


def trace_node(node_name: str, node_func: Callable[[NL2SQLState], Dict]) -> Callable[[NL2SQLState], Dict]:
    def wrapped(state: NL2SQLState) -> Dict:
        if not TRACE_RUNTIME.run_dir:
            return node_func(state)

        with TRACE_RUNTIME.lock:
            TRACE_RUNTIME.node_counter += 1
            sequence = TRACE_RUNTIME.node_counter

        safe_name = re.sub(r"[^0-9A-Za-z_\-]+", "_", node_name)
        node_dir = os.path.join(TRACE_RUNTIME.run_dir, "nodes", f"{sequence:03d}_{safe_name}")
        started_at = datetime.now().isoformat(timespec="seconds")
        start_time = time.perf_counter()
        state_dict = dict(state)

        write_trace_json(os.path.join(node_dir, "input.json"), state_dict)
        write_trace_json(
            os.path.join(node_dir, "input_summary.json"),
            {key: trace_summary(value) for key, value in state_dict.items()},
        )

        meta = {
            "seq": sequence,
            "node": node_name,
            "started_at": started_at,
            "input_keys": list(state_dict.keys()),
            "status": "running",
        }

        try:
            output = node_func(state) or {}
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            meta.update(
                {
                    "status": "success",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_ms": elapsed_ms,
                    "output_keys": list(output.keys()),
                }
            )
            write_trace_json(os.path.join(node_dir, "output.json"), output)
            write_trace_json(
                os.path.join(node_dir, "output_summary.json"),
                {key: trace_summary(value) for key, value in output.items()},
            )
            write_trace_json(os.path.join(node_dir, "meta.json"), meta)
            append_trace_jsonl(os.path.join(TRACE_RUNTIME.run_dir, "summary.jsonl"), meta)
            return output
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            meta.update(
                {
                    "status": "error",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_ms": elapsed_ms,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
            write_trace_json(
                os.path.join(node_dir, "error.json"),
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
            write_trace_json(os.path.join(node_dir, "meta.json"), meta)
            append_trace_jsonl(os.path.join(TRACE_RUNTIME.run_dir, "summary.jsonl"), meta)
            raise

    return wrapped
