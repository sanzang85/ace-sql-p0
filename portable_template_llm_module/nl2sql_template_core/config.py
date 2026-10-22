from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import urllib.request
from urllib.error import URLError


@dataclass(frozen=True)
class ProjectPaths:
    repo_root: Path
    legacy_script: Path
    outputs_dir: Path
    runtime_dir: Path
    reports_dir: Path
    traces_dir: Path
    evaluation_dir: Path
    default_gt_file: Path
    default_input_file: Path
    config_dir: Path
    model_profiles: Path
    knowledge_qe: Path
    knowledge_qs: Path
    knowledge_schema: Path
    knowledge_dictionary: Path
    structured_rules: Path
    template_knowledge_cut_batch_dir: Path
    template_knowledge_packaging_dir: Path
    template_knowledge_material_dir: Path
    template_knowledge_quality_dir: Path
    template_vector_index_dir: Path


def get_project_paths() -> ProjectPaths:
    repo_root = Path(__file__).resolve().parent.parent
    outputs_dir = repo_root / "outputs"
    config_dir = repo_root / "data" / "config"
    return ProjectPaths(
        repo_root=repo_root,
        legacy_script=repo_root / "legacy" / "ace_sql0626_trace_monolith.py",
        outputs_dir=outputs_dir,
        runtime_dir=outputs_dir / "runtime",
        reports_dir=outputs_dir / "reports",
        traces_dir=outputs_dir / "traces",
        evaluation_dir=outputs_dir / "evaluation",
        default_gt_file=repo_root / "data" / "ground_truth" / "a_0623.xlsx",
        default_input_file=repo_root / "data" / "inputs" / "q_0623.csv",
        config_dir=config_dir,
        model_profiles=config_dir / "model_profiles.json",
        knowledge_qe=repo_root / "data" / "knowledge" / "qe.json",
        knowledge_qs=repo_root / "data" / "knowledge" / "qs.json",
        knowledge_schema=repo_root / "data" / "knowledge" / "siames信息.md",
        knowledge_dictionary=repo_root / "data" / "knowledge" / "数据表信息.md",
        structured_rules=repo_root / "data" / "knowledge" / "structured_rule_catalog.json",
        template_knowledge_cut_batch_dir=repo_root / "data" / "knowledge" / "templates" / "cut_batch",
        template_knowledge_packaging_dir=repo_root / "data" / "knowledge" / "templates" / "packaging",
        template_knowledge_material_dir=repo_root / "data" / "knowledge" / "templates" / "material_consumption",
        template_knowledge_quality_dir=repo_root / "data" / "knowledge" / "templates" / "quality_check",
        template_vector_index_dir=repo_root / "data" / "knowledge" / "vector_index",
    )


LEGACY_INPUT_FILES = {
    "qe.json": "knowledge_qe",
    "qs.json": "knowledge_qs",
    "siames信息.md": "knowledge_schema",
    "数据表信息.md": "knowledge_dictionary",
}


def _openai_base_url(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    return base_url if base_url.endswith("/v1") else f"{base_url}/v1"


def _detect_chat_model(base_url: str) -> str | None:
    models = _list_remote_model_ids(base_url)
    return models[0] if models else None


def _list_remote_model_ids(base_url: str) -> list[str]:
    model_base_url = base_url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{model_base_url}/v1/models", timeout=5) as response:
            payload = json.load(response)
    except (URLError, TimeoutError, OSError, ValueError):
        return []
    except Exception:
        return []

    results: list[str] = []
    for item in payload.get("data") or []:
        model_id = str((item or {}).get("id", "")).strip()
        if model_id:
            results.append(model_id)
    return results


def _path_tail(text: str) -> str:
    normalized = str(text).strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return ""
    return normalized.rsplit("/", 1)[-1]


def _resolve_remote_model(base_url: str, requested_model: str | None) -> str | None:
    model_ids = _list_remote_model_ids(base_url)
    if not model_ids:
        return requested_model.strip() if isinstance(requested_model, str) and requested_model.strip() else None

    requested = requested_model.strip() if isinstance(requested_model, str) else ""
    if not requested:
        return model_ids[0]

    requested_tail = _path_tail(requested).lower()

    for candidate in model_ids:
        if candidate == requested:
            return candidate

    for candidate in model_ids:
        if candidate.lower() == requested.lower():
            return candidate

    for candidate in model_ids:
        if _path_tail(candidate).lower() == requested_tail:
            return candidate

    return model_ids[0]


BASE_VLLM_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8002")
VLLM_OPENAI_BASE_URL = _openai_base_url(BASE_VLLM_URL)
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "EMPTY")
_USER_VLLM_CHAT_MODEL = os.getenv("VLLM_CHAT_MODEL")
_AUTO_VLLM_CHAT_MODEL = _resolve_remote_model(BASE_VLLM_URL, _USER_VLLM_CHAT_MODEL) or _detect_chat_model(BASE_VLLM_URL)
VLLM_CHAT_MODEL = _AUTO_VLLM_CHAT_MODEL or _USER_VLLM_CHAT_MODEL or "minimax-m27-awq"
VLLM_RERANK_MODEL = os.getenv("VLLM_RERANK_MODEL", VLLM_CHAT_MODEL)
VLLM_MAX_TOKENS = int(os.getenv("VLLM_MAX_TOKENS", "1024"))
SQL_PRECHECK_ENABLED = os.getenv("NL2SQL_SQL_PRECHECK_ENABLED", "1").lower() not in {"0", "false", "no"}
SQL_PRECHECK_MAX_RETRIES = int(os.getenv("NL2SQL_SQL_PRECHECK_MAX_RETRIES", "3"))
PLAN_VALIDATION_MAX_RETRIES = int(os.getenv("NL2SQL_PLAN_VALIDATION_MAX_RETRIES", "2"))
SCHEMA_RETRY_MAX_RETRIES = int(os.getenv("NL2SQL_SCHEMA_RETRY_MAX_RETRIES", "1"))
BASE_VLLM_EMBEDDING_URL = os.getenv("VLLM_EMBEDDING_BASE_URL", "http://localhost:8000")
VLLM_EMBEDDING_OPENAI_BASE_URL = _openai_base_url(BASE_VLLM_EMBEDDING_URL)
_USER_VLLM_EMBEDDING_MODEL = os.getenv("VLLM_EMBEDDING_MODEL", "Qwen3-Embedding-8B")
VLLM_EMBEDDING_MODEL = _resolve_remote_model(BASE_VLLM_EMBEDDING_URL, _USER_VLLM_EMBEDDING_MODEL) or _USER_VLLM_EMBEDDING_MODEL
USE_VLLM_EMBEDDINGS = os.getenv("USE_VLLM_EMBEDDINGS", "1").lower() not in {"0", "false", "no"}

_PATHS = get_project_paths()
DATA_DICTIONARY_PATH = str(_PATHS.knowledge_dictionary)
DB_SCHEMA_PATH = str(_PATHS.knowledge_schema)
QE_RULES_PATH = str(_PATHS.knowledge_qe)
QS_EXAMPLES_PATH = str(_PATHS.knowledge_qs)
MODEL_PROFILES_PATH = str(_PATHS.model_profiles)
STRUCTURED_RULES_PATH = str(_PATHS.structured_rules)
TEMPLATE_KNOWLEDGE_CUT_BATCH_DIR = str(_PATHS.template_knowledge_cut_batch_dir)
TEMPLATE_KNOWLEDGE_PACKAGING_DIR = str(_PATHS.template_knowledge_packaging_dir)
TEMPLATE_KNOWLEDGE_MATERIAL_DIR = str(_PATHS.template_knowledge_material_dir)
TEMPLATE_KNOWLEDGE_QUALITY_DIR = str(_PATHS.template_knowledge_quality_dir)
TEMPLATE_VECTOR_INDEX_DIR = str(_PATHS.template_vector_index_dir)
USE_TEMPLATE_KNOWLEDGE = os.getenv("NL2SQL_USE_TEMPLATE_KNOWLEDGE", "0").lower() not in {"0", "false", "no"}
USE_VECTOR_RETRIEVAL = os.getenv("NL2SQL_USE_VECTOR_RETRIEVAL", "1").lower() not in {"0", "false", "no"}
CUT_BATCH_DETERMINISTIC_FASTPATH = os.getenv("NL2SQL_CUT_BATCH_DETERMINISTIC_FASTPATH", "1").lower() not in {"0", "false", "no"}
TEMPLATE_LLM_ONLY_MODE = os.getenv("NL2SQL_TEMPLATE_LLM_ONLY", "1").lower() not in {"0", "false", "no"}
LOW_RISK_TEMPLATE_MODE = os.getenv("NL2SQL_LOW_RISK_TEMPLATE_MODE", "0").lower() not in {"0", "false", "no"}
VECTOR_RETRIEVAL_SCHEMA_TOPK = int(os.getenv("NL2SQL_VECTOR_SCHEMA_TOPK", "5"))
VECTOR_RETRIEVAL_TEMPLATE_TOPK = int(os.getenv("NL2SQL_VECTOR_TEMPLATE_TOPK", "3"))
VECTOR_RETRIEVAL_ALIAS_TOPK = int(os.getenv("NL2SQL_VECTOR_ALIAS_TOPK", "6"))


def get_db_config() -> dict:
    return {
        "host": os.getenv("NL2SQL_DB_HOST", "localhost"),
        "port": int(os.getenv("NL2SQL_DB_PORT", "3306")),
        "user": os.getenv("NL2SQL_DB_USER", "readonly_user"),
        "password": os.environ["NL2SQL_DB_PASSWORD"],
        "database": os.getenv("NL2SQL_DB_NAME", "siames"),
        "charset": os.getenv("NL2SQL_DB_CHARSET", "utf8mb4"),
        "connect_timeout": int(os.getenv("NL2SQL_DB_CONNECT_TIMEOUT", "5")),
    }
