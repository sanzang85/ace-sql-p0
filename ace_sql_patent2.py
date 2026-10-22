"""P0 four-file NL2SQL entrypoint."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "portable_template_llm_module"))


def main():
    args = sys.argv[1:]
    if args and args[0] == "query":
        os.environ["NL2SQL_TEMPLATE_LIFECYCLE_ENABLED"] = "1"
        from nl2sql_template_core.cli import main as query_main
        return query_main(args[1:])
    from nl2sql_template_core.cli import main as query_main
    return query_main(args)

if __name__ == "__main__":
    raise SystemExit(main())
