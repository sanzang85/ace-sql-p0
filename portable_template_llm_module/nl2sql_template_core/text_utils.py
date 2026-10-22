from __future__ import annotations

from .intent_contracts import accuracy_mode

import json
import re
from typing import Any, Dict, List

from langchain_core.documents import Document


def _coerce_text(text: Any) -> str:
    if text is None:
        return ""
    if isinstance(text, str):
        return text.strip()
    try:
        return json.dumps(text, ensure_ascii=False, default=str).strip()
    except Exception:
        return str(text).strip()


def _balanced_json_candidates(text: str, *, limit: int = 12) -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    for opener, closer in (("{", "}"), ("[", "]")):
        for start, char in enumerate(text):
            if char != opener:
                continue
            depth = 0
            in_string = False
            escaped = False
            for end in range(start, len(text)):
                current = text[end]
                if in_string:
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == '"':
                        in_string = False
                    continue
                if current == '"':
                    in_string = True
                elif current == opener:
                    depth += 1
                elif current == closer:
                    depth -= 1
                    if depth == 0:
                        add(text[start : end + 1])
                        break
            if len(candidates) >= limit:
                return candidates
    return candidates


def json_candidate_texts(text: Any) -> List[str]:
    raw_text = _coerce_text(text)
    if not raw_text:
        return []

    candidates: List[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    for match in re.finditer(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", raw_text, re.DOTALL | re.IGNORECASE):
        add(match.group(1))

    add(raw_text)

    object_start = raw_text.find("{")
    object_end = raw_text.rfind("}")
    if object_start != -1 and object_end != -1 and object_end > object_start:
        add(raw_text[object_start : object_end + 1])

    array_start = raw_text.find("[")
    array_end = raw_text.rfind("]")
    if array_start != -1 and array_end != -1 and array_end > array_start:
        add(raw_text[array_start : array_end + 1])

    for candidate in _balanced_json_candidates(raw_text):
        add(candidate)

    return candidates


def extract_json_text(text: str) -> str:
    candidates = json_candidate_texts(text)
    return candidates[0] if candidates else _coerce_text(text)


def parse_json_with_diagnostics(text: Any, default: Any = None) -> tuple[Any, str | None]:
    candidates = json_candidate_texts(text)
    if not candidates:
        return default, "No JSON object/array candidate found in model output."

    errors: List[str] = []
    for index, candidate in enumerate(candidates[:12], start=1):
        try:
            return json.loads(candidate), None
        except Exception as exc:
            errors.append(f"candidate {index}: {type(exc).__name__}: {exc}")
    return default, "; ".join(errors[:3]) if errors else "JSON parse failed."


def safe_json_loads(text: str, default: Any) -> Any:
    parsed, _ = parse_json_with_diagnostics(text, default)
    return parsed


def approximate_token_count(text: str) -> int:
    text = text or ""
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(0, (ascii_chars + 3) // 4 + non_ascii_chars)


def truncate_middle(text: str, max_chars: int, marker: str = "\n...\n") -> str:
    text = text or ""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= len(marker) + 2:
        return text[:max_chars]

    head = (max_chars - len(marker)) // 2
    tail = max_chars - len(marker) - head
    return text[:head] + marker + text[-tail:]


def condense_context(
    query: str,
    text: str,
    max_chars: int = 6000,
    *,
    chunk_size: int = 1200,
    overlap: int = 120,
    max_chunks: int = 8,
) -> str:
    text = (text or "").strip()
    if accuracy_mode():
        max_chars = min(max_chars * 3, 40000)
        max_chunks *= 3
    if not text or max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text

    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        return truncate_middle(text, max_chars)

    scored = [(keyword_score(query, chunk), idx, chunk) for idx, chunk in enumerate(chunks)]
    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)

    selected: list[tuple[int, str]] = []
    selected_indices: set[int] = set()
    used_chars = 0
    separator = "\n\n"

    first_chunk = chunks[0]
    if len(first_chunk) < max_chars // 3:
        selected.append((0, first_chunk))
        selected_indices.add(0)
        used_chars = len(first_chunk)

    for _, idx, chunk in scored:
        if idx in selected_indices:
            continue
        if len(selected) >= max_chunks:
            break

        extra = len(separator) if selected else 0
        projected = used_chars + extra + len(chunk)
        if projected > max_chars and selected:
            continue

        selected.append((idx, chunk))
        selected_indices.add(idx)
        used_chars = projected

    if not selected:
        return truncate_middle(text, max_chars)

    selected.sort(key=lambda item: item[0])
    condensed = separator.join(chunk for _, chunk in selected)
    if len(condensed) > max_chars:
        condensed = truncate_middle(condensed, max_chars)

    if len(condensed) >= len(text):
        return condensed

    note = f"\n\n[context truncated from {len(text)} to {len(condensed)} chars]"
    remaining = max_chars - len(note)
    if remaining > 0 and len(condensed) > remaining:
        condensed = truncate_middle(condensed, remaining)
    return condensed + note


def extract_sql_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    parsed = safe_json_loads(text, None)
    if isinstance(parsed, dict):
        sql_value = parsed.get("sql") or parsed.get("SQL")
        if isinstance(sql_value, str) and sql_value.strip():
            text = sql_value.strip()

    code_match = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if code_match:
        return code_match.group(1).strip()

    generic_code_match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if generic_code_match:
        candidate = generic_code_match.group(1).strip()
        if re.match(r"(?is)^(select|with)\b", candidate):
            return candidate

    sql_match = re.search(r"(?is)\b(with|select)\b.*", text)
    if sql_match:
        candidate = sql_match.group(0).strip()
        last_semicolon = candidate.rfind(";")
        if last_semicolon != -1:
            candidate = candidate[: last_semicolon + 1]
        return candidate.strip()

    return text


def chunk_text(text: str, chunk_size: int = 1800, overlap: int = 200) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []

    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    chunks: List[str] = []
    buffer = ""
    for block in blocks:
        if len(buffer) + len(block) + 2 <= chunk_size:
            buffer = f"{buffer}\n\n{block}".strip()
        else:
            if buffer:
                chunks.append(buffer)
            if len(block) <= chunk_size:
                buffer = block
            else:
                start = 0
                while start < len(block):
                    chunks.append(block[start : start + chunk_size])
                    start += max(1, chunk_size - overlap)
                buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


def keyword_score(query: str, text: str) -> int:
    query = (query or "").strip()
    text = text or ""
    tokens = [tok for tok in re.split(r"[\s,，。；;：:、/\\()（）\[\]{}<>《》\"'`]+", query) if tok]
    score = sum(text.count(tok) for tok in tokens)
    for char in query:
        if "\u4e00" <= char <= "\u9fff":
            score += text.count(char)
    return score


def keyword_top_documents(query: str, docs: List[Document], k: int = 6) -> List[Document]:
    scored = [(keyword_score(query, doc.page_content), idx, doc) for idx, doc in enumerate(docs)]
    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    selected = [doc for score, _, doc in scored[: min(k, len(scored))] if score > 0]
    return selected or docs[: min(k, len(docs))]
