"""Parse every file in data/raw, normalize, load into DuckDB, then build the views with dbt.

    rensim run

Drop new turn results into data/raw/turns/ and new GM newsletters into data/raw/newsletters/,
then rerun. File names don't matter; the parser reads the dates from inside each file.
"""
from __future__ import annotations

import argparse

from .claims import load_claims
from .common import detect_kind
from .dbt_runner import dbt
from .load import load_duckdb
from .normalize import combine, normalize
from .orders import parse_orders_file
from .parse_news import parse_newsletter
from .parse_reference import parse_reference
from .parse_turn import parse_turn
from .settings import ALLY_REFERENCE, BASE, DATA, MY_ACCOUNT, ORDERS_DIR, RAW

DB = DATA / "renaissance.duckdb"


def parse_all() -> dict[str, list[dict]]:
    parsed = []
    # Turn reports and GM newsletters are parsed here; player publications come in through
    # data/claims/ (see renaissance/claims_extraction.md) and reference documents aren't loaded.
    for path in sorted(p for p in RAW.rglob("*") if p.is_file()
                       and not {"player_publications", "reference"} & set(p.parts)):
        kind = detect_kind(path)
        if kind == "turn":
            parsed.append(parse_turn(path))
        elif kind == "newsletter":
            parsed.append(parse_newsletter(path))
        else:
            print(f"  skipped (unrecognized format): {path.relative_to(BASE)}")
            continue
        print(f"  parsed {path.relative_to(BASE)}")
    # Allies' compiled references: GM data transcribed by another player (see parse_reference.py).
    for path in sorted(RAW.glob(ALLY_REFERENCE)):
        parsed.append(parse_reference(path))
        print(f"  parsed {path.relative_to(BASE)} (ally reference)")
    # The orders you sent each turn (orders/<account>_YYYY-MM.txt): what you did, for looking back.
    for path in sorted(ORDERS_DIR.glob("*.txt")):
        parsed.append(parse_orders_file(path))
        print(f"  parsed {path.relative_to(BASE)} (orders sent)")
    claims = load_claims()
    print(f"  loaded {len(claims['claims'])} claims from {len(claims['sources'])} player publication(s)")
    parsed.append(claims)
    return normalize(combine(parsed))


def main(argv=None):
    argparse.ArgumentParser(prog="rensim run").parse_args(argv)

    if not MY_ACCOUNT:
        print("No account set: add rensim.toml (see the README) so your own turns are recognised.")
    print("Parsing raw files...")
    tables = parse_all()
    unparsed = tables.get("unparsed_lines", [])
    if unparsed:
        print(f"\n{len(unparsed)} line(s) didn't match any known pattern (kept in table 'unparsed_lines'):")
        for row in unparsed:
            print(f"  [{row['source_id']} line {row['line_no']}] {row['text'].strip()}")

    print(f"\nLoading into {DB.relative_to(BASE)}")
    load_duckdb(tables, DB)

    # Models first, then tests, rather than `dbt build`: build skips everything downstream of a
    # failing test, and a bad row shouldn't cost you the views. A failing test is reported instead.
    print("\nBuilding the views (dbt run)...")
    if not dbt(["run"], DB):
        return 1
    print("\nTesting them (dbt test)...")
    if not dbt(["test"], DB):
        print("\nSome dbt tests failed (above). The views are built; check the data before trusting them.")
        return 1
    print("\nDone. Try:  rensim ask")


if __name__ == "__main__":
    main()
