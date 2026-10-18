"""Data quality checks on the loaded database. Each check returns a list of problems; any problem
fails the run, so a broken file stops CI instead of quietly loading half its rows.

    rensim quality                  # after rensim run
    rensim quality --backend sqlite

Plain SQL that runs on both DuckDB and SQLite. When real data legitimately breaks a rule, add it
to the known exceptions in your rensim.toml with the reason, rather than loosening the rule:

    [quality]
    known_gaps = [{ account = "ABC", from = "1546-07", to = "1546-12", why = "changed family" }]
    known_gm_messages = ['Your courier was delayed\\.']
"""
from __future__ import annotations

import argparse
import re
import sys

from .settings import QUALITY


def month_index(ym: str) -> int:
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def month_name(i: int) -> str:
    return f"{i // 12}-{i % 12 + 1:02d}"


# ---- known exceptions ----------------------------------------------------------------------

def known_gaps(entries: list[dict]) -> dict[tuple[str, str], str]:
    """(account, YYYY-MM) -> why, for months with no turn report that are expected to be missing.
    Each rensim.toml entry covers `from` to `to` (inclusive) for one account."""
    gaps = {}
    for e in entries:
        for i in range(month_index(e["from"]), month_index(e.get("to", e["from"])) + 1):
            gaps[(e["account"].upper(), month_name(i))] = e.get("why", "known gap")
    return gaps


KNOWN_GAPS = known_gaps(QUALITY.get("known_gaps", []))

# Unparsed lines that are GM messages, not a parser gap. Matched anywhere in the line.
KNOWN_GM_MESSAGES = [
    r"When coding orders, please omit initial zeroes\.",
    *QUALITY.get("known_gm_messages", []),
]

# ---- expected row counts -------------------------------------------------------------------

# Rows each turn report must produce, per table: (min, max). A turn that parses to 0 personae or
# 0 market lines means the format changed or the file is truncated. Real turns so far: 5 personae,
# 10-50 assets, 32-61 skills, 8-105 market lines, 5-41 present, 1-5 locations.
TURN_ROWS = {
    "turn_persona_status": (1, 12),
    "turn_persona_assets": (1, 200),
    "turn_persona_skills": (1, 200),
    "turn_market": (1, 400),
    "turn_presence": (1, 200),
    "turn_location_status": (1, 12),
}
# Per GM newsletter. Real issues so far: 44-131 events, 2-5 notices.
NEWSLETTER_ROWS = {
    "news_events": (20, 400),
    "news_notices": (1, 20),
}

# Tables with a price column.
PRICE_TABLES = ["turn_market", "ref_city_market", "claim_market"]


def columns(con, table: str) -> set[str] | None:
    """The table's columns, or None if it wasn't created (the loader skips tables with no rows)."""
    try:
        return {d[0] for d in con.execute(f"SELECT * FROM {table} LIMIT 0").description}
    except Exception:
        return None


def check_turn_months(con) -> list[str]:
    """One turn report per month per account, from its first turn to its last, with no gaps
    (except known gaps). One account plays one position at a time, so this also covers each
    position (account + family)."""
    rows = con.execute("""SELECT account, SUBSTR(game_date, 1, 7) AS ym, COUNT(*) AS n
                          FROM sources WHERE kind = 'turn_report'
                          GROUP BY account, SUBSTR(game_date, 1, 7)""").fetchall()
    problems, months = [], {}
    for account, ym, n in rows:
        months.setdefault(account, set()).add(month_index(ym))
        if n > 1:
            problems.append(f"{account} has {n} turn reports for {ym}")
    for account, have in sorted(months.items()):
        for i in range(min(have), max(have) + 1):
            if i not in have and (account, month_name(i)) not in KNOWN_GAPS:
                problems.append(f"{account} has no turn report for {month_name(i)}")
    for (account, ym), why in sorted(KNOWN_GAPS.items()):
        if account in months and month_index(ym) in months[account]:
            problems.append(f"{account} {ym} is listed as a known gap ({why}) but now has a turn: "
                            "remove it from known_gaps in rensim.toml")
    return problems


def _rows_per_source(con, kind: str, expected: dict[str, tuple[int, int]]) -> list[str]:
    problems = []
    sources = [r[0] for r in con.execute(
        f"SELECT source_id FROM sources WHERE kind = '{kind}' ORDER BY source_id").fetchall()]
    for table, (lo, hi) in expected.items():
        counts = (dict(con.execute(f"SELECT source_id, COUNT(*) FROM {table} GROUP BY source_id").fetchall())
                  if columns(con, table) else {})
        for s in sources:
            n = counts.get(s, 0)
            if not lo <= n <= hi:
                problems.append(f"{s}: {n} rows in {table}, expected {lo}-{hi}")
    return problems


def check_row_counts(con) -> list[str]:
    """Every turn report and newsletter produced a plausible number of rows in each table."""
    return (_rows_per_source(con, "turn_report", TURN_ROWS)
            + _rows_per_source(con, "gm_newsletter", NEWSLETTER_ROWS))


def check_prices(con) -> list[str]:
    """Every price is > 0. The one exception is a market line with quantity 0: the GM prints
    'weaponry 0.0 0 0' for an item a town neither demands nor supplies, so price 0 means nothing
    there (claim_market has no quantity, so every claimed price must be > 0)."""
    problems = []
    for table in PRICE_TABLES:
        cols = columns(con, table)
        if cols is None:
            continue
        unused = "quantity = 0" if "quantity" in cols else "1 = 0"
        for row in con.execute(f"""SELECT source_id, side, item, price FROM {table}
                                   WHERE (price IS NULL OR price <= 0) AND NOT ({unused})
                                   ORDER BY source_id, side, item""").fetchall():
            problems.append(f"{table}: {row[0]} {row[1]} {row[2]} has price {row[3]}")
    return problems


def check_unparsed(con) -> list[str]:
    """No line the parsers didn't recognise, except known GM messages."""
    if columns(con, "unparsed_lines") is None:
        return []
    known = [re.compile(p) for p in KNOWN_GM_MESSAGES]
    rows = con.execute("SELECT source_id, line_no, text FROM unparsed_lines ORDER BY source_id, line_no").fetchall()
    return [f"{s} line {n} not parsed: {t.strip()}" for s, n, t in rows
            if not any(p.search(t) for p in known)]


CHECKS = [check_turn_months, check_row_counts, check_prices, check_unparsed]


def run_checks(con) -> dict[str, list[str]]:
    """check name -> problems (empty list when it passes)."""
    return {c.__name__: c(con) for c in CHECKS}


def main(argv=None):
    from .load import connect
    from .run import DB

    ap = argparse.ArgumentParser(prog="rensim quality")
    ap.add_argument("--backend", choices=["duckdb", "sqlite"], default="duckdb")
    args = ap.parse_args(argv)
    results = run_checks(connect(args.backend, DB[args.backend]))
    for name, problems in results.items():
        print(f"{'FAIL' if problems else 'ok  '}  {name}")
        for p in problems:
            print(f"        {p}")
    if any(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
