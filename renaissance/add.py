"""Save a turn result or GM newsletter into data/raw, named the usual way.

    rensim add FILE... [--force] [--run]
    rensim add -                        text pasted on stdin (end with Ctrl-D)

Each file is read straight away, so one the parser can't handle fails now rather than partway
through `rensim run`. Turns are saved as data/raw/turns/<account>_<YYYY-MM>.txt and newsletters
as data/raw/newsletters/NEWSL<issue>.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .common import detect_kind
from .parse_news import parse_newsletter
from .parse_turn import parse_turn
from .settings import BASE, MY_ACCOUNT, RAW

PARSERS = {"turn": parse_turn, "newsletter": parse_newsletter}


class Refused(Exception):
    """A file that wasn't saved, and why."""


def shown(path: Path) -> str:
    """The path as the user would type it: relative to the data folder's base when it's inside."""
    return str(path.relative_to(BASE)) if path.is_relative_to(BASE) else str(path)


def target(kind: str, source: dict, raw: Path) -> Path:
    """Where a parsed file goes: named from what's inside it, not what it was called."""
    if kind == "turn":
        return raw / "turns" / f"{source['account'].lower()}_{source['game_date'][:7]}.txt"
    return raw / "newsletters" / f"NEWSL{source['issue']}"


def add_file(path: Path, raw: Path, force: bool = False) -> tuple[Path, bool]:
    """Check one file and save it under raw. Returns where it went and whether it was written
    (False: an identical copy was already there). Raises Refused if it can't be added."""
    kind = detect_kind(path)
    if kind is None:
        raise Refused("not a turn result (starting ACCOUNT CODE:) or a GM newsletter")
    try:
        tables = PARSERS[kind](path)
    except Exception as e:
        raise Refused(f"the {kind} parser couldn't read it: {e}") from e
    source = tables["sources"][0]
    data = path.read_bytes()
    dest = target(kind, source, raw)
    if dest.exists():
        if dest.read_bytes() == data:
            return dest, False
        if not force:
            raise Refused(f"{shown(dest)} already exists with different content (--force replaces it)")

    if kind == "turn" and MY_ACCOUNT and source["account"].upper() != MY_ACCOUNT:
        print(f"  warning: account {source['account']} isn't yours ({MY_ACCOUNT}); "
              "fine for an ally's shared report")
    unparsed = len(tables.get("unparsed_lines", []))
    if unparsed:
        print(f"  warning: {unparsed} line(s) didn't match any known pattern (`rensim run` lists them)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest, True


def read_stdin(folder: Path) -> Path:
    """Pasted text, saved to a file the parsers can read. Terminals paste UTF-8; the game's files
    are cp1252, so it's stored that way."""
    path = folder / "pasted.txt"
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    path.write_bytes(text.encode("cp1252", errors="replace"))
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rensim add", description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", metavar="FILE", help="turn results or newsletters; - for stdin")
    ap.add_argument("--force", action="store_true", help="replace a saved file with different content")
    ap.add_argument("--run", action="store_true", help="then rensim run, if anything new was saved")
    args = ap.parse_args(argv)

    failed = saved = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name in args.files:
            path = read_stdin(Path(tmp)) if name == "-" else Path(name)
            label = "(stdin)" if name == "-" else name
            if not path.is_file():
                print(f"{label}: no such file", file=sys.stderr)
                failed += 1
                continue
            print(f"{label}:", flush=True)   # before any error on stderr, when piped
            try:
                dest, written = add_file(path, RAW, args.force)
            except Refused as e:
                print(f"  not added: {e}", file=sys.stderr)
                failed += 1
                continue
            saved += written
            print(f"  {'saved as' if written else 'already saved, identical:'} {shown(dest)}")

    if saved and args.run:
        from .run import main as run   # loads dlt and dbt, so only when asked
        print()
        return run([]) or (1 if failed else 0)
    if saved:
        print("\nNext: rensim run")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
