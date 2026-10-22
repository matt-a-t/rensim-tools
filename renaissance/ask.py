"""Run the saved questions in sql/questions.sql and print the answers.

    rensim ask                       # all questions
    rensim ask what_changed          # one question by name
    rensim ask --sql "SELECT * FROM dim_places LIMIT 5"

The questions read the dbt models (renaissance/dbt/) that `rensim run` builds.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .load import connect
from .run import DB

QUESTIONS = Path(__file__).resolve().parent / "sql" / "questions.sql"


def load_questions() -> dict[str, tuple[str, str]]:
    """name -> (description, sql)."""
    out = {}
    for block in re.split(r"^-- name:\s*", QUESTIONS.read_text(encoding="utf-8"), flags=re.M)[1:]:
        name, _, rest = block.partition("\n")
        desc = rest.splitlines()[0].lstrip("- ").strip() if rest.startswith("--") else ""
        out[name.strip()] = (desc, rest.strip().rstrip(";"))
    return out


def show(con, sql: str) -> None:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = [["" if v is None else str(v) for v in r] for r in cur.fetchall()]
    widths = [min(60, max([len(c)] + [len(r[i]) for r in rows])) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + "  ".join(v[:60].ljust(w) for v, w in zip(r, widths)))
    print(f"  ({len(rows)} row{'s' if len(rows) != 1 else ''})\n")


def main(argv=None):
    # Windows consoles default to a legacy code page; print accented names instead of crashing.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="rensim ask")
    ap.add_argument("name", nargs="?")
    ap.add_argument("--sql")
    args = ap.parse_args(argv)
    con = connect(DB)

    if args.sql:
        show(con, args.sql)
        return
    questions = load_questions()
    for name, (desc, sql) in questions.items():
        if args.name and name != args.name:
            continue
        print(f"== {name}: {desc}")
        show(con, sql)


if __name__ == "__main__":
    main()
