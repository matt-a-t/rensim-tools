"""Load the claims files (data/claims/*.json) written from player publications.

See docs/claims_extraction.md for the format. Every claim keeps its verbatim quote, and
check_quotes() verifies each quote really appears in the source text, so a claim can't say
more than the page does.

    rensim claims        # check every claims file; exits 1 if any is malformed or misquotes
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .common import place_key
from .settings import DATA

CLAIMS = DATA / "claims"
EXTRACTED = DATA / "extracted"
TRANSCRIBED = DATA / "transcribed"
ALIASES = DATA / "aliases"
FAMILY_ALIASES = ALIASES / "families.csv"
PLACE_ALIASES = ALIASES / "places.csv"
PERSONA_ALIASES = ALIASES / "personae.csv"

CLAIM_TYPES = {"office", "title_dispute", "action", "history", "family", "heraldry", "location_report"}
HEDGES = {"asserted", "reported", "rumored", "denied"}
REQUIRED = ("id", "type", "subject", "predicate", "object", "hedge", "quote")


def source_text(file: str) -> str:
    """The text claims are checked against: the hand transcription if there is one, else the machine text."""
    for base in (TRANSCRIBED, EXTRACTED):
        path = base / (file + ".txt")
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No extracted or transcribed text for {file}; run rensim extract")


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read_claim_files() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) | {"_path": p.name} for p in sorted(CLAIMS.glob("*.json"))]


def validate(doc: dict) -> list[str]:
    """Problems with one claims file (empty list = fine)."""
    problems, seen = [], set()
    for key in ("source_id", "file", "kind", "game_date"):
        if not doc.get("source", {}).get(key):
            problems.append(f"source.{key} is missing")
    for c in doc.get("claims", []):
        cid = c.get("id", "?")
        problems += [f"{cid}: {k} is missing" for k in REQUIRED if not c.get(k)]
        if c.get("type") not in CLAIM_TYPES:
            problems.append(f"{cid}: unknown type {c.get('type')!r}")
        if c.get("hedge") not in HEDGES:
            problems.append(f"{cid}: unknown hedge {c.get('hedge')!r}")
        if cid in seen:
            problems.append(f"{cid}: duplicate id")
        seen.add(cid)
    return problems


def check_quotes(doc: dict) -> list[str]:
    """Claims whose quote isn't found word-for-word (ignoring whitespace) in the source text."""
    text = squash(source_text(doc["source"]["file"]))
    return [f"{c['id']}: quote not in source: {c['quote']!r}"
            for c in doc["claims"] if squash(c["quote"]) not in text]


def read_aliases(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_family_aliases() -> dict[str, str]:
    """ALIAS -> CANONICAL, from data/aliases/families.csv (variants you accepted in `rensim match`)."""
    return {r["alias"].strip().upper(): r["canonical"].strip().upper() for r in read_aliases(FAMILY_ALIASES)}


def load_place_aliases() -> dict[str, str]:
    """alias key -> canonical key, from data/aliases/places.csv ('Edinburg' -> 'edinburgh')."""
    return {place_key(r["alias"]): place_key(r["canonical"]) for r in read_aliases(PLACE_ALIASES)}


def load_persona_aliases() -> dict[tuple[str, str], int]:
    """(given name lowercased, family key) -> persona ID, from data/aliases/personae.csv: names
    written without an ID (newsletters, claims) that you matched to a character."""
    families = load_family_aliases()
    return {(r["given"].strip().lower(), families.get(f := r["family"].strip().upper(), f)): int(r["persona_id"])
            for r in read_aliases(PERSONA_ALIASES)}


def load_claims() -> dict[str, list[dict]]:
    """Claims files -> rows for the sources, claims and claim_market tables."""
    out = {"sources": [], "claims": [], "claim_market": []}
    for doc in read_claim_files():
        problems = validate(doc)
        if problems:
            raise ValueError(f"{doc['_path']}: " + "; ".join(problems))
        s = doc["source"]
        gd = s["game_date"]
        out["sources"].append({
            "source_id": s["source_id"], "kind": s["kind"], "reliability": "player_claim",
            "file_name": Path(s["file"]).name, "title": s.get("title"), "publisher": s.get("publisher"),
            "player_family": (s.get("publisher_family") or None),
            "game_date": f"{gd}-01" if len(gd) == 7 else gd, "real_date": s.get("real_date"),
            "account": None, "actions_used": None, "issue": None,
        })
        for c in doc["claims"]:
            subj = c["subject"]
            data = c.get("data")
            out["claims"].append({
                "source_id": s["source_id"], "claim_id": c["id"], "claim_type": c["type"],
                "subject_name": subj.get("name"), "subject_given": subj.get("given"),
                "subject_family": (subj.get("family") or "").upper() or None, "subject_place": subj.get("place"),
                "predicate": c["predicate"], "object": c["object"], "place": c.get("place"),
                "game_date": c.get("game_date") or gd, "hedge": c["hedge"], "claimed_by": c.get("claimed_by") or s.get("publisher"),
                "quote": squash(c["quote"]), "refs": "; ".join(c.get("refs", [])) or None,
                "lat_text": (data or {}).get("lat"), "lon_text": (data or {}).get("lon"),
                "data_json": json.dumps(data, ensure_ascii=False) if data else None,
            })
            for side, key in (("supply", "supplies"), ("demand", "demands")):
                for m in (data or {}).get(key, []):
                    out["claim_market"].append({
                        "source_id": s["source_id"], "claim_id": c["id"], "game_date": c.get("game_date") or gd,
                        "location": c.get("place"), "side": side, "item": m["item"].lower(),
                        "bq": float(m["bq"]), "price": int(m["price"]),
                    })
    return out


def main(argv=None):
    """Check every claims file: well-formed, and every quote really in its source."""
    import sys
    docs, bad = read_claim_files(), 0
    for doc in docs:
        try:
            problems = validate(doc) or check_quotes(doc)
        except FileNotFoundError as e:
            problems = [str(e)]
        bad += bool(problems)
        print(f"{'FAIL' if problems else 'ok  '}  {doc['_path']}")
        for p in problems:
            print(f"        {p}")
    print(f"\n{len(docs) - bad} of {len(docs)} claims file(s) OK.")
    if bad:
        sys.exit(1)
