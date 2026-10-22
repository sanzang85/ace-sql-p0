from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional


@dataclass
class TraceRuntime:
    run_dir: Optional[str] = None
    max_string_chars: int = 0
    node_counter: int = 0
    lock: threading.Lock = threading.Lock()


TRACE_RUNTIME = TraceRuntime()

