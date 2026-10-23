"""Draft claims files from player publications with the Claude API, and score drafts against
claims files written by hand.

    rensim extract-claims player_publications/foo.pdf     draft -> data/claims/drafts/foo.json
    rensim eval-claims data/claims/foo.json --draft data/claims/drafts/foo.json
    rensim eval-claims data/claims/foo.json               extract foo's publication again and score that

Claude reads the publication's text (the transcription if there is one, else the machine text) and
follows claims_extraction.md, the same guide used by hand. Every claim it returns goes through the
checks `rensim claims` makes: one with a missing field, an unknown type or hedge, or a quote that
isn't word for word in the source is dropped and listed, never written. The draft goes to
data/claims/drafts/, which `rensim run` doesn't load: review it, fix it, and move it up to
data/claims/ to use it. Needs `pip install "rensim-tools[llm]"` and an API key (ANTHROPIC_API_KEY).

Scoring: the hand-written file is the answer key. A draft claim matches a hand-written one when
they have the same type and their quotes overlap: one contains the other, or they share at least
half their words. Pairs are matched one to one, closest first. Precision is the share of draft
claims that match (claims made up, or given the wrong type, cost precision); recall is the share
of hand-written claims matched (claims missed cost recall). Hedges are compared on matched pairs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .claims import CLAIM_TYPES, CLAIMS, HEDGES, misquote, source_text, squash, validate, validate_claim
from .settings import RAW

DRAFTS = CLAIMS / "drafts"
GUIDE = Path(__file__).with_name("claims_extraction.md")
MODEL = "claude-opus-5-5"
OVERLAP = 0.5          # share of words two quotes must have in common to match (Jaccard)
KINDS = ["player_newsletter", "proclamation", "player_letter"]
SOURCE_FIELDS = ("source_id", "file", "kind", "title", "publisher", "publisher_family", "game_date", "real_date", "notes")

PROMPT = """Extract every claim from the player publication below, following the guide.
Its file is {file}. Fill `source` from what the text says (title, publisher, the publisher's family,
the issue's game month as YYYY-MM); use null for anything it doesn't say. Put a claim's structured
detail in `data_json` as a JSON object written out as a string, or null. Every quote must be copied
word for word from the text.

<publication>
{text}
</publication>"""


def _null(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


TEXT = {"type": "string"}
# The claims file format as a JSON schema, so the reply is always parseable. `data` is free-form
# in the format, which a schema can't say, so it comes back as a JSON string (data_json).
SCHEMA = _object(
    source=_object(kind={"type": "string", "enum": KINDS}, title=_null(TEXT), publisher=_null(TEXT),
                   publisher_family=_null(TEXT), game_date=_null(TEXT), notes=_null(TEXT)),
    claims={"type": "array", "items": _object(
        id=TEXT, type={"type": "string", "enum": sorted(CLAIM_TYPES)},
        subject=_object(name=_null(TEXT), given=_null(TEXT), family=_null(TEXT), place=_null(TEXT)),
        predicate=TEXT, object=TEXT, place=_null(TEXT), game_date=_null(TEXT),
        hedge={"type": "string", "enum": sorted(HEDGES)}, claimed_by=_null(TEXT), quote=TEXT,
        refs={"type": "array", "items": TEXT}, data_json=_null(TEXT))},
)


def publication_file(arg: str) -> str:
    """The publication's path as claims files give it, relative to data/raw/."""
    path = Path(arg)
    try:
        return path.resolve().relative_to(RAW).as_posix()
    except ValueError:
        return path.as_posix()


def ask_claude(text: str, file: str, client, model: str = MODEL) -> dict:
    """Claude's reply for one publication: {"source": {...}, "claims": [...]} in SCHEMA's shape."""
    # Streamed, since a long publication makes a long reply; fallbacks="default" re-runs a request
    # a safety classifier declines on a fallback model instead of failing it.
    with client.beta.messages.stream(
        model=model, max_tokens=64000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=GUIDE.read_text(encoding="utf-8"),
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(file=file, text=text)}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude stopped early ({message.stop_reason}); no draft written")
    return json.loads([b.text for b in message.content if b.type == "text"][-1])


def tidy(claim: dict) -> dict:
    """A claim as a hand-written file would have it: no nulls or empty lists, data parsed."""
    out = {k: v for k, v in claim.items() if v not in (None, [], "") and k != "data_json"}
    out["subject"] = {k: v for k, v in claim["subject"].items() if v}
    if claim.get("data_json"):
        out["data"] = json.loads(claim["data_json"])
    return out


def extract(file: str, client, overrides: dict | None = None, model: str = MODEL) -> tuple[dict, list[str]]:
    """Draft claims file for a publication, and the claims dropped by the checks (with why)."""
    text = source_text(file)
    reply = ask_claude(text, file, client, model)
    found = {k: v for k, v in reply["source"].items() if v}
    source = {"source_id": f"pub:{Path(file).stem}", "file": file, **found, **(overrides or {})}
    claims, rejected, seen, squashed = [], [], set(), squash(text)
    for c in reply["claims"]:
        try:
            c = tidy(c)
        except json.JSONDecodeError:
            rejected.append(f"{c.get('id', '?')}: data_json is not valid JSON: {c['data_json']!r}")
            continue
        problems = validate_claim(c) + [m for m in [misquote(c, squashed)] if m]
        if c.get("id") in seen:
            problems.append(f"{c['id']}: duplicate id")
        if problems:
            rejected += problems
            continue
        seen.add(c["id"])
        claims.append(c)
    return {"source": {k: source[k] for k in SOURCE_FIELDS if source.get(k)}, "claims": claims}, rejected


def make_client():
    try:
        import anthropic
    except ImportError:
        print('This needs the Anthropic SDK: pip install "rensim-tools[llm]"', file=sys.stderr)
        return None
    return anthropic.Anthropic()


def run_extract(file: str, client, overrides: dict | None, model: str) -> tuple[dict, list[str]] | None:
    """extract(), with the reasons it can fail printed instead of raised."""
    try:
        return extract(file, client, overrides, model)
    except (FileNotFoundError, RuntimeError) as e:
        print(e, file=sys.stderr)
    except TypeError as e:   # what the SDK raises when it finds no credentials
        if "authentication" not in str(e):
            raise
        print("No API key: set ANTHROPIC_API_KEY (or log in with `ant auth login`).", file=sys.stderr)
    except Exception as e:
        if type(e).__module__.startswith("anthropic"):
            print(f"Claude API error: {e}", file=sys.stderr)
        else:
            raise
    return None


def print_rejected(rejected: list[str]) -> None:
    for r in rejected:
        print(f"  rejected  {r}")


def main(argv=None):
    """rensim extract-claims: draft a claims file for one publication."""
    ap = argparse.ArgumentParser(prog="rensim extract-claims", description="Draft a claims file with the Claude API.")
    ap.add_argument("publication", help="its path under data/raw/, e.g. player_publications/foo.pdf")
    ap.add_argument("--force", action="store_true", help="overwrite an existing draft")
    ap.add_argument("--model", default=MODEL, help=f"Claude model (default {MODEL})")
    for flag, key in (("--source-id", "source_id"), ("--kind", "kind"), ("--title", "title"),
                      ("--publisher", "publisher"), ("--family", "publisher_family"),
                      ("--game-date", "game_date"), ("--real-date", "real_date")):
        ap.add_argument(flag, dest=key, help=f"set source.{key} instead of taking it from the text")
    args = ap.parse_args(argv)

    file = publication_file(args.publication)
    out = DRAFTS / f"{Path(file).stem}.json"
    if out.exists() and not args.force:
        print(f"{out} already exists; --force to overwrite it.", file=sys.stderr)
        return 1
    client = make_client()
    overrides = {k: v for k in ("source_id", "kind", "title", "publisher", "publisher_family", "game_date", "real_date")
                 if (v := getattr(args, k))}
    if client is None or (result := run_extract(file, client, overrides, args.model)) is None:
        return 1
    doc, rejected = result
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(doc['claims'])} claim(s) written to {out}, {len(rejected)} rejected.")
    print_rejected(rejected)
    for p in validate({"source": doc["source"], "claims": []}):
        print(f"  check     {p}")
    print("Review it, then move it to data/claims/ and run `rensim claims`.")
    return 0


# ---- scoring ------------------------------------------------------------------
def overlap(a: str, b: str) -> float:
    """1 if one quote contains the other, else the share of words they have in common."""
    a, b = squash(a).lower(), squash(b).lower()
    if a in b or b in a:
        return 1.0
    wa, wb = set(re.findall(r"\w+", a)), set(re.findall(r"\w+", b))
    return len(wa & wb) / len(wa | wb) if wa | wb else 0.0


def score_claims(draft: dict, gold: dict, threshold: float = OVERLAP) -> dict:
    """Precision and recall of a draft against a hand-written claims file (see the module docstring)."""
    dc, gc = draft["claims"], gold["claims"]
    candidates = sorted(((overlap(d["quote"], g["quote"]), i, j) for i, d in enumerate(dc) for j, g in enumerate(gc)
                         if d["type"] == g["type"]),
                        key=lambda t: (-t[0], dc[t[1]]["hedge"] != gc[t[2]]["hedge"]))   # ties: same hedge first
    pairs, used_d, used_g = [], set(), set()
    for ov, i, j in candidates:
        if ov < threshold:
            break
        if i not in used_d and j not in used_g:
            pairs.append((dc[i], gc[j]))
            used_d.add(i)
            used_g.add(j)
    return {"precision": len(pairs) / len(dc) if dc else None,
            "recall": len(pairs) / len(gc) if gc else None,
            "hedges_agree": sum(d["hedge"] == g["hedge"] for d, g in pairs), "matched": pairs,
            "false_positives": [d for i, d in enumerate(dc) if i not in used_d],
            "false_negatives": [g for j, g in enumerate(gc) if j not in used_g]}


def evaluate(argv=None):
    """rensim eval-claims: score a draft (or a fresh extraction) against a hand-written claims file."""
    ap = argparse.ArgumentParser(prog="rensim eval-claims", description="Precision and recall of drafted claims.")
    ap.add_argument("gold", type=Path, help="a claims file written by hand")
    ap.add_argument("--draft", type=Path, help="the draft to score; without it, the publication is extracted again")
    ap.add_argument("--model", default=MODEL, help=f"Claude model when extracting (default {MODEL})")
    args = ap.parse_args(argv)

    gold = json.loads(args.gold.read_text(encoding="utf-8"))
    if problems := validate(gold):
        print(f"{args.gold}: " + "; ".join(problems), file=sys.stderr)
        return 1
    if args.draft:
        draft = json.loads(args.draft.read_text(encoding="utf-8"))
    else:
        client = make_client()
        if client is None or (result := run_extract(gold["source"]["file"], client, None, args.model)) is None:
            return 1
        draft, rejected = result
        print(f"Extracted {len(draft['claims'])} claim(s), {len(rejected)} rejected.")
        print_rejected(rejected)

    r = score_claims(draft, gold)
    fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
    print(f"precision {fmt(r['precision'])}  recall {fmt(r['recall'])}  hedges agree {r['hedges_agree']} of "
          f"{len(r['matched'])}  ({len(draft['claims'])} drafted, {len(gold['claims'])} by hand)")
    for d, g in r["matched"]:
        if d["hedge"] != g["hedge"]:
            print(f"  hedge differs: {d['id']} {d['hedge']} (by hand {g['id']} {g['hedge']}): {d['quote']!r}")
    for name, label in (("false_positives", "only in draft"), ("false_negatives", "missed")):
        for c in r[name]:
            print(f"  {label}: {c['id']} {c['type']}: {c['quote']!r}")
    return 0
