"""The saved questions in sql/questions.sql, which the MCP server's `ask` tool and named tools run.
They read the dbt models (renaissance/dbt/) the load builds."""
from __future__ import annotations

import re
from pathlib import Path

QUESTIONS = Path(__file__).resolve().parent / "sql" / "questions.sql"


def load_questions() -> dict[str, tuple[str, str]]:
    """name -> (description, sql)."""
    out = {}
    for block in re.split(r"^-- name:\s*", QUESTIONS.read_text(encoding="utf-8"), flags=re.M)[1:]:
        name, _, rest = block.partition("\n")
        desc = rest.splitlines()[0].lstrip("- ").strip() if rest.startswith("--") else ""
        out[name.strip()] = (desc, rest.strip().rstrip(";"))
    return out

