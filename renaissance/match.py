"""Propose names that may be one family, place or character written differently: OCR typos,
regional spellings, a publication's own spelling. Matches are proposals for you to review, never
merged on their own: Habsburg, HABSBOURG and HAPSBOURG hold separate towers in one city.

    rensim match                       score name pairs -> data/aliases/proposed.csv
    rensim match --apply               rows you marked y go to data/aliases/, n to rejected.csv
    rensim match --score labels.csv    precision and recall against pairs you labelled by hand

Mark each row of proposed.csv y or n in its `decision` column, run `rensim match --apply`, then
`rensim run` so the accepted aliases reach the database. Needs `pip install "rensim-tools[match]"`.

How a pair is scored, with Splink (https://moj-analytical-services.github.io/splink/):

- Records. One per family key, one per place key, and for characters, one per character ID
  (`known`) and one per name written without an ID in a newsletter or claim (`mention`).
  Families and places are deduplicated against themselves; mentions are linked to known
  characters.
- Blocking. Comparing every record with every other grows with the square of the count, so
  only pairs that share something cheap to check are compared (BLOCKING). A pair no rule lets
  through is never scored, so the rules have to be loose enough for typos.
- Comparisons. Each pair falls into one level per comparison, the first whose test it passes:
  names identical, one edit apart (Damerau-Levenshtein: a letter added, dropped, changed or two
  swapped, which is what OCR and hurried copying do), similar (Jaro-Winkler >= 0.88), or
  different; how many places both were seen at; how far apart two places' coordinates are.
- m and u. For each level, m is how often a true match lands there and u how often two
  unrelated names do. Their ratio is the level's evidence (its match weight, log2(m/u)): two
  names a letter apart are common among matches (high m) and rare by chance (low u). Splink's
  usual way to learn m is expectation maximisation over thousands of records; a game has
  hundreds of names, so m is set by hand below from what a typo looks like. u is measured from
  your data: Splink compares random pairs, which are nearly all non-matches
  (estimate_u_using_random_sampling). The hand-set u values are only used for a level random
  pairs never reach.
- Prior. Before any evidence, the chance that two random records match: about one family or
  place name in ten has a variant (PRIOR_VARIANTS), and about half the names written without an
  ID are characters you've seen (PRIOR_KNOWN). The weights add to it; the total becomes
  match_probability.

Then one rule the model can't overrule: the GM doesn't misspell. Two different names that both
appear in turn reports or GM newsletters are different things, so they're never proposed.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .claims import ALIASES, load_family_aliases
from .common import place_key
from .normalize import FAMILY_COLUMNS, PERSONA_MENTIONS, PLACE_COLUMNS

DB = ALIASES.parent / "renaissance.duckdb"
PROPOSED = ALIASES / "proposed.csv"
REJECTED = ALIASES / "rejected.csv"
ALIAS_FILES = {"family": ("families.csv", ["alias", "canonical"]),
               "place": ("places.csv", ["alias", "canonical"]),
               "persona": ("personae.csv", ["given", "family", "persona_id"])}
PROPOSAL_COLUMNS = ["kind", "name", "match", "persona_id", "probability", "why", "decision"]

GM_KINDS = ("turn_report", "gm_newsletter")
# Propose pairs at least this likely. Low, because you review every proposal and a variant missed
# stays split; on the labelled sample in tests/test_match.py real pairs score 0.2 and up.
THRESHOLD = 0.1
PRIOR_VARIANTS = 0.1  # share of family and place names that have a variant
PRIOR_KNOWN = 0.5     # share of names written without an ID that are one of the known characters

# Only pairs passing at least one rule are scored: same first letter, or same last three letters
# (so 'Gdinburgh' still meets 'Edinburgh'). Characters: same family, or same first letter of the
# given name (so a misspelt family still meets its character).
BLOCKING = {"family": ["substr(name, 1, 1)", "substr(name, -3)"],
            "place": ["substr(name, 1, 1)", "substr(name, -3)"],
            "persona": ["family", "substr(given, 1, 1)"]}


def spelling(col: str, m: list[float], u: list[float]):
    """Identical, one edit apart, similar, or different: a custom comparison, since Splink's
    ready-made ones use one measure each. Jaro-Winkler alone scores one-letter slips anywhere from
    0.84 ('Tirion'/'Tyrion') to 0.97, while look-alikes two edits apart ('MEDINA'/'MEDICI') can
    score higher."""
    import splink.comparison_level_library as cll
    import splink.comparison_library as cl

    return cl.CustomComparison(
        [cll.NullLevel(col), cll.ExactMatchLevel(col), cll.DamerauLevenshteinLevel(col, 1),
         cll.JaroWinklerLevel(col, 0.88), cll.ElseLevel()],
        output_column_name=col,
    ).configure(m_probabilities=m, u_probabilities=u)


def comparisons(kind: str) -> list:
    """Splink comparisons for one kind of record, with hand-set m (and fallback u) per level.
    The lists run from the closest level to 'all other comparisons'; a NULL level has none."""
    import splink.comparison_library as cl

    # Seen at the same places: some evidence, but a variant is often from a publication that
    # reports the family somewhere else.
    places = cl.ArrayIntersectAtSizes("places", [2, 1]).configure(
        m_probabilities=[0.3, 0.4, 0.3], u_probabilities=[0.01, 0.1, 0.89])
    # Exact never happens between two family or place records (each name is one record), so its
    # m barely matters; one letter off is the usual slip.
    name = spelling("name", m=[0.01, 0.7, 0.2, 0.09], u=[0.001, 0.005, 0.03, 0.964])
    if kind == "family":
        return [name, places]
    if kind == "place":
        # A variant spelling of a place is the same spot on the map.
        return [name, cl.DistanceInKMAtThresholds("lat", "lon", [25, 100]).configure(
            m_probabilities=[0.9, 0.07, 0.03], u_probabilities=[0.01, 0.05, 0.94])]
    return [spelling("given", m=[0.7, 0.2, 0.05, 0.05], u=[0.01, 0.005, 0.03, 0.955]),
            spelling("family", m=[0.85, 0.1, 0.03, 0.02], u=[0.1, 0.005, 0.02, 0.875]),
            places]


# ---- records ------------------------------------------------------------------------------

def group(kind: str, sightings: list[dict]) -> list[dict]:
    """Sightings -> one record per name, with where it was seen.

    A sighting is one row of a source naming something: {'name', 'gm', 'place'} for a family
    (the place it was seen at), {'name', 'spelling', 'gm', 'lat', 'lon'} for a place, and
    {'given', 'family', 'gm', 'place'} for a character, plus 'persona_id' for a known one."""
    by: dict[tuple, list[dict]] = defaultdict(list)
    for s in sightings:
        key = (s["persona_id"],) if s.get("persona_id") is not None else \
              (s["given"].lower(), s["family"]) if kind == "persona" else (s["name"],)
        by[key].append(s)
    records = []
    for i, (key, rows) in enumerate(sorted(by.items(), key=lambda kv: repr(kv[0]))):
        places = sorted({r["place"] for r in rows if r.get("place")})
        rec = {"unique_id": i, "gm": any(r["gm"] for r in rows), "rows": len(rows), "places": places or None}
        if kind == "persona":
            last = rows[-1]
            rec.update(given=last["given"], family=last["family"], persona_id=last.get("persona_id"),
                       display=f"{last['family']}, {last['given']}")
        else:
            rec.update(name=key[0], display=Counter(r.get("spelling") or r["name"] for r in rows).most_common(1)[0][0])
        if kind == "place":
            rec.update(lat=next((r["lat"] for r in rows if r.get("lat") is not None), None),
                       lon=next((r["lon"] for r in rows if r.get("lon") is not None), None))
        records.append(rec)
    return records


def read_sightings(con) -> dict[str, list[dict]]:
    """Every family, place and character name in the loaded database, as sightings for group()."""
    cols: dict[str, set] = defaultdict(set)
    for t, c in con.execute("SELECT table_name, column_name FROM information_schema.columns "
                            "WHERE table_schema = 'game'").fetchall():
        cols[t].add(c)
    place_of = {}   # table -> its first place key column, for where a family or character was seen
    for t, c in PLACE_COLUMNS:
        place_of.setdefault(t, f"{c}_key")

    def rows(table: str, *exprs: str, where: str = "TRUE") -> list[tuple]:
        gm = f"s.kind IN {GM_KINDS}"
        return con.execute(f'SELECT {", ".join(exprs)}, {gm} FROM "{table}" t '
                           f"LEFT JOIN sources s USING (source_id) WHERE {where}").fetchall()

    out = {"family": [], "place": [], "persona": []}
    for table, col, _ in FAMILY_COLUMNS:
        if f"{col}_key" in cols[table]:
            place = f"t.{place_of[table]}" if place_of.get(table) in cols[table] else "NULL"
            out["family"] += [{"name": n, "place": p, "gm": gm}
                              for n, p, gm in rows(table, f"t.{col}_key", place, where=f"t.{col}_key IS NOT NULL")]

    coords = {k: (lat, lon) for k, lat, lon in con.execute("SELECT place_key, lat, lon FROM dim_places").fetchall()}
    for table, col in PLACE_COLUMNS:
        if f"{col}_key" in cols[table]:
            out["place"] += [{"name": k, "spelling": sp, "gm": gm, **dict(zip(("lat", "lon"), coords.get(k, (None, None))))}
                             for k, sp, gm in rows(table, f"t.{col}_key", f"t.{col}", where=f"t.{col}_key IS NOT NULL")]

    # Known characters: every sighting by ID, under the name dim_personae settled on.
    families = load_family_aliases()
    names = {pid: (given, families.get(fam, fam))
             for pid, given, fam in con.execute("SELECT persona_id, given_name, UPPER(family) FROM dim_personae").fetchall()}
    for table in ("turn_persona_status", "turn_presence", "ref_personae"):
        if "persona_id" in cols[table]:
            out["persona"] += [{"persona_id": pid, "given": names[pid][0], "family": names[pid][1], "place": p, "gm": gm}
                               for pid, p, gm in rows(table, "t.persona_id", "t.location_key")
                               if names.get(pid, (None,))[0]]
    # Characters named without an ID that haven't been matched yet.
    for table, given, family, pid in PERSONA_MENTIONS:
        if given in cols[table]:
            place = "COALESCE(t.subject_place_key, t.place_key)" if table == "claims" else "t.place_key"
            matched = f"t.{pid} IS NULL" if pid in cols[table] else "TRUE"
            out["persona"] += [{"given": g, "family": f, "place": p, "gm": gm}
                               for g, f, p, gm in rows(table, f"t.{given}", f"t.{family}_key", place,
                                                       where=f"t.{given} IS NOT NULL AND t.{family}_key IS NOT NULL AND {matched}")]
    return out


# ---- scoring ------------------------------------------------------------------------------

def score(kind: str, records: list[dict]) -> list[dict]:
    """Every pair the blocking rules let through, with Splink's match_probability and the level
    each comparison fell into. Characters: known x mention pairs only."""
    import pyarrow as pa
    import duckdb
    from splink import DuckDBAPI, Linker, SettingsCreator, block_on

    known = [r for r in records if kind != "persona" or r["persona_id"] is not None]
    mentions = [r for r in records if kind == "persona" and r["persona_id"] is None]
    if (not mentions) if kind == "persona" else len(known) < 2:
        return []
    db = DuckDBAPI(connection=duckdb.connect())
    fields = ["unique_id", *(["given", "family"] if kind == "persona" else ["name"]),
              *(["lat", "lon"] if kind == "place" else ["places"])]
    # Given names are compared ignoring case ('Sansa' / 'SANSA'); family and place names are keys already.
    tables = [db.register(pa.Table.from_pylist([{f: r[f].lower() if f == "given" else r[f] for f in fields} for r in rs]),
                          dataset_display_name=n)
              for n, rs in (("known", known), ("mentions", mentions)) if rs]
    settings = SettingsCreator(
        link_type="link_only" if kind == "persona" else "dedupe_only",
        comparisons=comparisons(kind),
        blocking_rules_to_generate_predictions=[block_on(rule) for rule in BLOCKING[kind]],
        # Pairs that match / pairs compared: one per variant among n(n-1)/2 pairs, or one known
        # character per mention among known x mentions.
        probability_two_random_records_match=min(PRIOR_KNOWN / len(known) if kind == "persona"
                                                 else 2 * PRIOR_VARIANTS / (len(known) - 1), 0.5),
    )
    linker = Linker(tables, settings, log_level=logging.WARNING)
    # A game's names give far fewer than max_pairs random pairs, so this compares all of them.
    linker.training.estimate_u_using_random_sampling(max_pairs=1e7, seed=1)
    labels = {c["output_column_name"]: [lv["label_for_charts"].replace("All other comparisons", f"{c['output_column_name']} differs")
                                        for lv in c["comparison_levels"] if not lv.get("is_null_level")]
              for c in linker.misc.save_model_to_json()["comparisons"]}
    by_id = {r["unique_id"]: r for r in records}   # unique across known and mentions (group())
    predictions = linker.inference.predict(warning_mode="never").as_duckdbpyrelation()
    pairs = []
    for row in (dict(zip(predictions.columns, r)) for r in predictions.fetchall()):
        left, right = by_id[row["unique_id_l"]], by_id[row["unique_id_r"]]
        # A level's label, from its gamma: non-NULL levels count down from the closest to 0.
        levels = [lv[len(lv) - 1 - row[f"gamma_{c}"]] for c, lv in labels.items() if row[f"gamma_{c}"] >= 0]
        pairs.append({"left": left, "right": right, "probability": row["match_probability"], "levels": levels})
    return pairs


def same_name(kind: str, a: dict, b: dict) -> bool:
    if kind == "persona":
        return (a["given"].lower(), a["family"]) == (b["given"].lower(), b["family"])
    return a["name"] == b["name"]


def seen(r: dict) -> str:
    where = f" at {', '.join(r['places'][:3])}{'...' if len(r['places']) > 3 else ''}" if r.get("places") else ""
    return f"{'GM' if r['gm'] else 'not GM'}, {r['rows']} row{'s' * (r['rows'] != 1)}{where}"


def propose(kind: str, sightings: list[dict], threshold: float = THRESHOLD, rejected: set = frozenset()) -> list[dict]:
    """Proposal rows for one kind: likely pairs, minus two different GM names and pairs you rejected."""
    out = []
    for p in score(kind, group(kind, sightings)):
        a, b = p["left"], p["right"]
        if p["probability"] < threshold or (a["gm"] and b["gm"] and not same_name(kind, a, b)):
            continue
        if kind == "persona":
            variant, canon = (b, a) if a["persona_id"] is not None else (a, b)
        else:   # the GM's spelling is the canonical one, else the more common
            variant, canon = sorted((a, b), key=lambda r: (r["gm"], r["rows"], r["name"]))
        row = {"kind": kind, "name": variant["display"], "match": canon["display"],
               "persona_id": canon.get("persona_id") or "", "probability": round(p["probability"], 3),
               "why": "; ".join([*p["levels"], f"{variant['display']}: {seen(variant)}", f"{canon['display']}: {seen(canon)}"]),
               "decision": ""}
        if pair_key(row) not in rejected:
            out.append(row)
    return sorted(out, key=lambda r: (-r["probability"], r["kind"], r["name"]))


def pair_key(row: dict) -> tuple:
    """A proposal or label, compared regardless of spelling case or which name is first."""
    kind = row["kind"]
    if kind == "persona":
        return kind, row["name"].lower(), str(row.get("persona_id") or "")
    norm = place_key if kind == "place" else str.upper
    return (kind, *sorted([norm(row["name"]), norm(row["match"])]))


# ---- files --------------------------------------------------------------------------------

def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, columns: list[str], rows: list[dict], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not append or not path.exists() or path.stat().st_size == 0
    with path.open("w" if not append else "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, columns, extrasaction="ignore", lineterminator="\n")
        if new:
            w.writeheader()
        w.writerows(rows)


def apply(folder: Path = ALIASES) -> tuple[int, int, int]:
    """proposed.csv rows marked y -> the alias files, n -> rejected.csv; undecided rows stay.
    Returns (accepted, rejected, left)."""
    rows = read_csv(folder / "proposed.csv")
    decide = lambda r: (r.get("decision") or "").strip().lower()[:1]
    yes = [r for r in rows if decide(r) == "y"]
    no = [r for r in rows if decide(r) == "n"]
    for kind, (file, columns) in ALIAS_FILES.items():
        new = [r for r in yes if r["kind"] == kind]
        if kind == "persona":
            new = [{"family": r["name"].split(", ", 1)[0], "given": r["name"].split(", ", 1)[1],
                    "persona_id": r["persona_id"]} for r in new]
        else:
            new = [{"alias": r["name"], "canonical": r["match"]} for r in new]
        if new:
            write_csv(folder / file, columns, new, append=True)
    if no:
        write_csv(folder / "rejected.csv", PROPOSAL_COLUMNS[:4], no, append=True)
    left = [r for r in rows if r not in yes and r not in no]
    write_csv(folder / "proposed.csv", PROPOSAL_COLUMNS, left)
    return len(yes), len(no), len(left)


def precision_recall(proposals: list[dict], labels: list[dict]) -> dict:
    """Against hand-labelled pairs (kind, name, match, persona_id, is_match = 1/0): of the labelled
    pairs proposed, the share that are real (precision); of the real ones, the share proposed (recall).
    Unlabelled proposals don't count either way."""
    proposed = {pair_key(p) for p in proposals}
    truth = {pair_key(l): l["is_match"].strip() in ("1", "y", "yes", "true") for l in labels}
    tp = [k for k, real in truth.items() if real and k in proposed]
    fp = [k for k, real in truth.items() if not real and k in proposed]
    fn = [k for k, real in truth.items() if real and k not in proposed]
    return {"precision": len(tp) / (len(tp) + len(fp)) if tp or fp else None,
            "recall": len(tp) / (len(tp) + len(fn)) if tp or fn else None,
            "true_positives": tp, "false_positives": fp, "false_negatives": fn}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rensim match", description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="move decided rows of proposed.csv into data/aliases/")
    ap.add_argument("--score", metavar="LABELS.csv", type=Path, help="precision and recall against hand-labelled pairs")
    ap.add_argument("--threshold", type=float, default=THRESHOLD, help=f"lowest match probability to propose (default {THRESHOLD})")
    args = ap.parse_args(argv)

    if args.apply:
        yes, no, left = apply()
        print(f"{yes} accepted into data/aliases/, {no} rejected, {left} left to decide.")
        if yes:
            print("Run `rensim run` so the database uses them.")
        return 0
    try:
        import splink  # noqa: F401
    except ImportError:
        print('rensim match needs Splink: pip install "rensim-tools[match]"', file=sys.stderr)
        return 1
    if not DB.exists():
        print("No database yet: run `rensim run` first.", file=sys.stderr)
        return 1

    from .load import connect
    con = connect(DB)
    try:
        sightings = read_sightings(con)
    finally:
        con.close()
    rejected = {pair_key(r) for r in read_csv(REJECTED)}
    # Aliases you accepted since the last `rensim run` aren't in the database yet: don't re-propose them.
    for kind, (file, _) in ALIAS_FILES.items():
        for r in read_csv(ALIASES / file):
            rejected.add(pair_key({"kind": kind, "name": f"{r['family']}, {r['given']}", "persona_id": r["persona_id"]}
                                  if kind == "persona" else {"kind": kind, "name": r["alias"], "match": r["canonical"]}))
    proposals = [p for kind in ("family", "place", "persona") for p in propose(kind, sightings[kind], args.threshold, rejected)]

    if args.score:
        result = precision_recall(proposals, read_csv(args.score))
        fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
        print(f"precision {fmt(result['precision'])}  recall {fmt(result['recall'])}  "
              f"(threshold {args.threshold}, {len(read_csv(args.score))} labelled pairs)")
        for name in ("false_positives", "false_negatives"):
            for k in result[name]:
                print(f"  {name.replace('_', ' ')[:-1]}: {' / '.join(k)}")
        return 0

    # Keep decisions already written in proposed.csv for pairs proposed again.
    decided = {pair_key(r): r["decision"] for r in read_csv(PROPOSED) if (r.get("decision") or "").strip()}
    for p in proposals:
        p["decision"] = decided.get(pair_key(p), "")
    write_csv(PROPOSED, PROPOSAL_COLUMNS, proposals)
    counts = Counter(p["kind"] for p in proposals)
    print(f"{len(proposals)} proposed match(es) ({', '.join(f'{n} {k}' for k, n in sorted(counts.items())) or 'none'}) "
          f"in {PROPOSED}.")
    if proposals:
        print("Mark each y or n in its decision column, then run `rensim match --apply`.")
    return 0
