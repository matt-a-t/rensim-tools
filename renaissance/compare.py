"""Build the database with both backends and check every question in sql/questions.sql gives the
same answer on each. Exits 1 with a readable diff if any question disagrees.

    rensim compare              # rebuild both databases, then compare
    rensim compare --no-build   # compare the databases already built
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from decimal import Decimal

from .ask import load_questions
from .load import connect
from .run import DB


def normalize_value(v):
    """One value as a comparable string. Only differences that are down to the engine are hidden:
    - booleans: SQLite has no boolean type and stores True/False as 1/0, so both become 1/0.
    - numbers: DuckDB may return Decimal or HUGEINT where SQLite returns float or int, and the two
      engines can differ in the last bits of floating-point sums and averages. Every number is
      compared as a float at 9 significant digits (so 963 == 963.0, and 0.1+0.2 == 0.3).
    - dates: DuckDB returns datetime.date where SQLite returns the ISO string; str() makes them equal.
    Integer division is NOT hidden: 7/2 is 3 in SQLite and 3.5 in DuckDB, and that is a real bug."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, (int, float, Decimal)):
        return f"{float(v):.9g}"
    return str(v)


def normalize_rows(rows) -> list[tuple]:
    """Rows normalized and sorted. Row order is compared as a sorted multiset because ORDER BY
    leaves ties in an engine-defined order (e.g. two places at the same distance); every question
    runs the same SQL on both engines, so what matters is that the same rows come back."""
    return sorted(tuple(normalize_value(v) for v in r) for r in rows)


def answers(backend: str) -> dict[str, list[tuple]]:
    con = connect(backend, DB[backend])
    out = {}
    for name, (_, sql) in load_questions().items():
        try:
            out[name] = normalize_rows(con.execute(sql).fetchall())
        except Exception as e:  # a question that only fails on one engine is a difference too
            out[name] = [("ERROR", f"{type(e).__name__}: {e}")]
    con.close()
    return out


def diff(a: dict[str, list[tuple]], b: dict[str, list[tuple]], names=("duckdb", "sqlite")) -> list[str]:
    """Readable lines for every question whose answers differ; empty if they all agree."""
    lines = []
    for q in a:
        ra, rb = a[q], b.get(q, [])
        if ra == rb:
            continue
        only_a = [r for r in ra if r not in rb]
        only_b = [r for r in rb if r not in ra]
        lines.append(f"== {q}: {len(ra)} row(s) in {names[0]}, {len(rb)} in {names[1]}")
        lines += [f"  only in {names[0]}: {' | '.join(r)}" for r in only_a]
        lines += [f"  only in {names[1]}: {' | '.join(r)}" for r in only_b]
        if not only_a and not only_b:
            lines.append("  same rows, different number of duplicates")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rensim compare")
    ap.add_argument("--no-build", action="store_true", help="compare the databases already built")
    args = ap.parse_args(argv)
    if not args.no_build:
        for backend in DB:
            print(f"Building {backend}...")
            subprocess.run([sys.executable, "-m", "renaissance", "run", "--backend", backend],
                           check=True, stdout=subprocess.DEVNULL)
    a, b = answers("duckdb"), answers("sqlite")
    problems = diff(a, b)
    if problems:
        print("\n".join(problems))
        print(f"\nDuckDB and SQLite disagree on {sum(l.startswith('==') for l in problems)} "
              f"of {len(a)} question(s).")
        sys.exit(1)
    print(f"DuckDB and SQLite agree on all {len(a)} questions.")


if __name__ == "__main__":
    main()
