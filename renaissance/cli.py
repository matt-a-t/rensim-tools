"""The `rensim` command: one entry point for every tool. Run it from your data folder (the one
with rensim.toml), or anywhere below it.

    rensim run                          parse data/raw and orders/, load the database, build the views
    rensim ask [name] [--sql "..."]     saved questions, or your own SQL
    rensim quality                      data quality checks on the loaded database
    rensim dbt COMMAND                  any dbt command on the views: build, test, docs generate...
    rensim orders FILE [--send]         check orders in plain English / print the text to email
    rensim training [--skills a,b]      best training orders for personae starting together
    rensim extract                      text from new player publications (needs OCR tools)
    rensim claims                       claims files are valid and every quote is in its source
    rensim extract-claims FILE          draft a claims file for a publication with the Claude API
    rensim eval-claims GOLD [--draft D] precision and recall of drafted claims against a hand-written file
    rensim match [--apply | --score F]  propose look-alike names to review, then keep the ones you accept
    rensim mcp                          MCP server: Claude answers questions from your database
    rensim config                       show the settings in use

`python -m renaissance ...` does the same.
"""
from __future__ import annotations

import importlib
import sys

COMMANDS = {"run": "run", "ask": "ask", "quality": "quality", "dbt": "dbt_runner",
            "orders": "orders", "training": "training", "extract": "extract_text",
            "claims": "claims", "match": "match",
            "extract-claims": "llm_claims", "eval-claims": "llm_claims:evaluate",
            "mcp": "mcp_server"}   # module[:function], default main


def show_config() -> int:
    from . import settings
    print(f"config file  {settings.CONFIG or '(none found: using defaults in the current folder)'}")
    for name in ("MY_ACCOUNT", "MY_FAMILY", "HOME", "DATA", "ORDERS_DIR", "ALLY_REFERENCE", "ALLY", "QUALITY"):
        print(f"{name.lower():<12} {getattr(settings, name)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] == "config":
        return show_config()
    if argv[0] not in COMMANDS:
        print(f"rensim: unknown command {argv[0]!r}. Commands: {', '.join([*COMMANDS, 'config'])}", file=sys.stderr)
        return 2
    name, _, function = COMMANDS[argv[0]].partition(":")
    module = importlib.import_module(f".{name}", __package__)
    return getattr(module, function or "main")(argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
