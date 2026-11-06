"""Save a turn result or GM newsletter into data/raw, named the usual way (`intake` uses it).

Each file is parsed straight away, so one the parser can't handle is refused now rather than
failing the next load. Turns are saved as data/raw/turns/<account>_<YYYY-MM>.txt and newsletters
as data/raw/newsletters/NEWSL<issue>.

A turn's billing block and mailing address (between the ACTIONS= line and the Copyright line) are
replaced with one line saying so. The fee lines in it stay: `check_orders` reads TOTAL ACTION
FEES to estimate what your orders will cost.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import detect_kind
from .parse_news import parse_newsletter
from .parse_turn import address_block, parse_turn
from .settings import BASE, MY_ACCOUNT

PARSERS = {"turn": parse_turn, "newsletter": parse_newsletter}
ACCOUNT_RE = re.compile(r"[A-Za-z0-9]+")   # turn files are named from it, so nothing else gets in
FEE_RE = re.compile(r"^\s*[A-Z][A-Z ]*:\s*-?[\d,.]+\s*$")   # "  TOTAL ACTION FEES:    5.000"
ADDRESS_REMOVED = "[billing and mailing address removed before committing]"


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


def remove_address(data: bytes) -> bytes | None:
    """The turn file with its address block replaced by ADDRESS_REMOVED, keeping the fee lines;
    None if there's no Copyright line to find the block by. Running it twice changes nothing."""
    # surrogateescape: bytes cp1252 has no character for come back out unchanged.
    lines = data.decode("cp1252", errors="surrogateescape").split("\n")
    block = address_block(lines)
    if block is None:
        return None
    start, end = block
    cr = "\r" if lines[0].endswith("\r") else ""
    lines[start:end] = [l for l in lines[start:end] if FEE_RE.match(l)] + [ADDRESS_REMOVED + cr]
    return "\n".join(lines).encode("cp1252", errors="surrogateescape")


def has_address(data: bytes) -> bool:
    """Whether a turn file still has lines in its address block that remove_address takes out."""
    lines = data.decode("cp1252", errors="surrogateescape").split("\n")
    block = address_block(lines)
    return bool(block) and any(l.strip() and l.strip() != ADDRESS_REMOVED and not FEE_RE.match(l)
                               for l in lines[block[0]:block[1]])


def add_file(path: Path, raw: Path, force: bool = False, keep_address: bool = False,
             require_address: bool = False, write: bool = True) -> tuple[Path, bool]:
    """Check one file and save it under raw. Returns where it went and whether it was written
    (False: an identical copy was already there). Raises Refused if it can't be added.
    require_address: refuse a turn whose address can't be found, rather than warn and keep it.
    write=False checks everything and says where it would go, without saving."""
    kind = detect_kind(path)
    if kind is None:
        raise Refused("not a turn result (starting ACCOUNT CODE:) or a GM newsletter")
    try:
        tables = PARSERS[kind](path)
    except Exception as e:
        raise Refused(f"the {kind} parser couldn't read it: {e}") from e
    source = tables["sources"][0]
    if kind == "turn" and not ACCOUNT_RE.fullmatch(source["account"]):
        raise Refused(f"account code {source['account']!r} isn't letters and digits")
    data = path.read_bytes()
    if kind == "turn" and not keep_address:
        removed = remove_address(data)
        if removed is None and require_address:
            raise Refused("no Copyright line, so the address couldn't be found to remove")
        if removed is None:
            print("  warning: no Copyright line, so the address wasn't found and is still in the saved file")
        data = removed or data
    dest = target(kind, source, raw)
    if not dest.resolve().is_relative_to(raw.resolve()):
        raise Refused(f"it would be saved outside {shown(raw)}, as {dest}")
    if dest.exists():
        if dest.read_bytes() == data:
            return dest, False
        if not force:
            raise Refused(f"{shown(dest)} already exists with different content")

    if kind == "turn" and MY_ACCOUNT and source["account"].upper() != MY_ACCOUNT:
        print(f"  warning: account {source['account']} isn't yours ({MY_ACCOUNT}); "
              "fine for an ally's shared report")
    unparsed = len(tables.get("unparsed_lines", []))
    if unparsed:
        print(f"  warning: {unparsed} line(s) didn't match any known pattern (kept in table unparsed_lines)")
    if write:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return dest, True
