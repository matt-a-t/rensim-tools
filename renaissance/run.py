"""Parse every file in data/raw, normalize, and load into a database.

    rensim run                  # DuckDB via dlt
    rensim run --backend sqlite # Python's built-in sqlite3

Drop new turn results into data/raw/turns/ and new GM newsletters into data/raw/newsletters/,
then rerun. File names don't matter; the parser reads the dates from inside each file.
"""
from __future__ import annotations

import argparse

from .claims import load_claims
from .load import load_duckdb, load_sqlite
from .normalize import combine, normalize
from .orders import parse_orders_file
from .parse_news import parse_newsletter
from .parse_reference import parse_reference
from .parse_turn import parse_turn
from .settings import ALLY_REFERENCE, BASE, DATA, MY_ACCOUNT, ORDERS_DIR, RAW

DB = {"duckdb": DATA / "renaissance.duckdb", "sqlite": DATA / "renaissance.sqlite"}


def parse_all() -> dict[str, list[dict]]:
    parsed = []
    # Turn reports and GM newsletters are parsed here; player publications come in through
    # data/claims/ (see docs/claims_extraction.md) and reference documents aren't loaded.
    for path in sorted(p for p in RAW.rglob("*") if p.is_file()
                       and not {"player_publications", "reference"} & set(p.parts)):
        head = path.read_bytes()[:400].decode("cp1252", errors="replace")
        if head.startswith("ACCOUNT CODE:"):
            parsed.append(parse_turn(path))
        elif "N E W S L E T T E R" in head:
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
    ap = argparse.ArgumentParser(prog="rensim run")
    ap.add_argument("--backend", choices=["duckdb", "sqlite"], default="duckdb")
    args = ap.parse_args(argv)

    if not MY_ACCOUNT:
        print("No account set: add rensim.toml (see the README) so your own turns are recognised.")
    print("Parsing raw files...")
    tables = parse_all()
    unparsed = tables.get("unparsed_lines", [])
    if unparsed:
        print(f"\n{len(unparsed)} line(s) didn't match any known pattern (kept in table 'unparsed_lines'):")
        for row in unparsed:
            print(f"  [{row['source_id']} line {row['line_no']}] {row['text'].strip()}")

    print(f"\nLoading into {args.backend}: {DB[args.backend].relative_to(BASE)}")
    if args.backend == "duckdb":
        load_duckdb(tables, DB["duckdb"])
    else:
        load_sqlite(tables, DB["sqlite"])
    print("Done. Try:  rensim ask" + (" --backend sqlite" if args.backend == "sqlite" else ""))


if __name__ == "__main__":
    main()
