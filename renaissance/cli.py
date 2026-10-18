"""The `rensim` command: one entry point for every tool. Run it from your data folder (the one
with rensim.toml), or anywhere below it.

    rensim run [--backend sqlite]       parse data/raw and orders/, load the database
    rensim ask [name] [--sql "..."]     saved questions, or your own SQL
    rensim quality                      data quality checks on the loaded database
    rensim compare                      DuckDB and SQLite give the same answers
    rensim orders FILE [--send]         check orders in plain English / print the text to email
    rensim training [--skills a,b]      best training orders for personae starting together
    rensim extract                      text from new player publications (needs OCR tools)
    rensim claims                       claims files are valid and every quote is in its source
    rensim config                       show the settings in use

`python -m renaissance ...` does the same.
"""
from __future__ import annotations

import importlib
import sys

COMMANDS = {"run": "run", "ask": "ask", "quality": "quality", "compare": "compare",
            "orders": "orders", "training": "training", "extract": "extract_text",
            "claims": "claims"}


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
    module = importlib.import_module(f".{COMMANDS[argv[0]]}", __package__)
    return module.main(argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
