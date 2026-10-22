from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


MODEL_PROFILES_PATH = str(
    (Path(__file__).resolve().parent.parent / "data" / "config" / "model_profiles.json").resolve()
)


def _normalize_profile_key(name: str) -> str:
    return name.strip().lower().replace("-", "_")


@lru_cache(maxsize=1)
def load_profile_catalog() -> dict[str, Any]:
    if not os.path.exists(MODEL_PROFILES_PATH):
        return {"default_profile": "minimax", "profiles": {}}
    with open(MODEL_PROFILES_PATH, "r", encoding="utf-8") as file:
        payload = json.load(file)
    return payload if isinstance(payload, dict) else {"default_profile": "minimax", "profiles": {}}


def default_profile_name() -> str:
    catalog = load_profile_catalog()
    raw_name = str(catalog.get("default_profile", "minimax"))
    return _normalize_profile_key(raw_name) or "minimax"


def list_profiles() -> list[dict[str, Any]]:
    catalog = load_profile_catalog()
    results: list[dict[str, Any]] = []
    for raw_name, payload in (catalog.get("profiles") or {}).items():
        profile = payload if isinstance(payload, dict) else {}
        results.append(
            {
                "name": _normalize_profile_key(str(raw_name)),
                "description": str(profile.get("description", "")).strip(),
                "aliases": [str(alias).strip() for alias in profile.get("aliases", []) if str(alias).strip()],
            }
        )
    results.sort(key=lambda item: item["name"])
    return results


def resolve_profile(name: str) -> tuple[str, dict[str, Any]]:
    normalized = _normalize_profile_key(name)
    catalog = load_profile_catalog()
    profiles = catalog.get("profiles") or {}

    for raw_name, payload in profiles.items():
        profile_name = _normalize_profile_key(str(raw_name))
        profile = payload if isinstance(payload, dict) else {}
        aliases = {_normalize_profile_key(profile_name)}
        aliases.update(_normalize_profile_key(str(alias)) for alias in profile.get("aliases", []))
        if normalized in aliases:
            return profile_name, profile

    available = ", ".join(item["name"] for item in list_profiles()) or "(none)"
    raise KeyError(f"Unknown profile '{name}'. Available profiles: {available}")


def batch_defaults_for_profile(name: str) -> dict[str, Any]:
    _, profile = resolve_profile(name)
    batch = profile.get("batch") or {}
    return batch if isinstance(batch, dict) else {}


def apply_profile_env(name: str, *, force: bool) -> str:
    resolved_name, profile = resolve_profile(name)
    env_map = profile.get("env") or {}
    if not isinstance(env_map, dict):
        env_map = {}

    for key, value in env_map.items():
        if force or key not in os.environ:
            os.environ[str(key)] = str(value)

    if force or "NL2SQL_PROFILE" not in os.environ:
        os.environ["NL2SQL_PROFILE"] = resolved_name
    return resolved_name
