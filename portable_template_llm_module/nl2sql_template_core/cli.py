from __future__ import annotations

import os
import sys
from typing import Sequence

from .profiles import apply_profile_env, default_profile_name, list_profiles


def _split_profile_args(argv: list[str]) -> tuple[list[str], str | None, bool]:
    cleaned: list[str] = []
    profile_name: str | None = None
    list_only = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--list-profiles":
            list_only = True
            index += 1
            continue
        if token == "--profile":
            if index + 1 >= len(argv):
                raise SystemExit("Missing profile name after --profile")
            profile_name = argv[index + 1]
            index += 2
            continue
        if token.startswith("--profile="):
            profile_name = token.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(token)
        index += 1
    return cleaned, profile_name, list_only


def _print_profiles() -> None:
    active_default = default_profile_name()
    print("Available NL2SQL model profiles:")
    for item in list_profiles():
        aliases = f" | aliases: {', '.join(item['aliases'])}" if item["aliases"] else ""
        default_mark = " [default]" if item["name"] == active_default else ""
        print(f"- {item['name']}{default_mark}: {item['description']}{aliases}")


def main(argv: Sequence[str] | None = None, *, default_profile: str | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    cleaned_argv, explicit_profile, list_only = _split_profile_args(raw_argv)

    if list_only:
        _print_profiles()
        return 0

    env_profile = os.getenv("NL2SQL_PROFILE")
    chosen_profile = explicit_profile or env_profile or default_profile or default_profile_name()
    force_profile = explicit_profile is not None or env_profile is not None or default_profile is not None
    apply_profile_env(chosen_profile, force=force_profile)

    from .main import main as core_main

    return core_main(cleaned_argv)


if __name__ == "__main__":
    raise SystemExit(main())
