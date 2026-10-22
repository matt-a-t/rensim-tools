"""The normalization layer: turn per-file parse output into consistent, joinable tables.

- Every place mention gets a `*_key` ('PEGU', 'Pegu' -> 'pegu'; 'CapeVerde' -> 'capeverde'),
  resolved through data/aliases/places.csv.
- `dim_places` lists each place once, with its best display name, coordinates (when any turn
  report gave them) and every spelling seen.
- `dim_personae` lists each character once by its game ID.
- Road events get a direction-independent route key ('nan|pegu').
- Lookup results get straight-line miles from the location the turn report was about.
- `my_settings` holds your account, family and home from rensim.toml, for the SQL views.
- Family names get a `*_key` resolved through data/aliases/families.csv,
  and `dim_families` lists each family once with where it appears (official sources vs claims).
- Characters named without an ID (newsletter actors, besiegers, claim subjects) get a
  `*_persona_id` from data/aliases/personae.csv.
The alias files hold only matches you accepted (`rensim match`); nothing is merged on looks alone.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .claims import load_family_aliases, load_persona_aliases, load_place_aliases
from .common import coord, display_name, miles_between, place_key
from .settings import HOME, MY_ACCOUNT, MY_FAMILY

# (table, column) pairs that hold a place name.
PLACE_COLUMNS = [
    ("turn_persona_status", "location"), ("turn_location_status", "location"),
    ("turn_location_ingress", "location"), ("turn_location_officials", "location"),
    ("turn_market", "location"), ("turn_presence", "location"), ("turn_lookups", "place"),
    ("news_events", "place"), ("news_events", "place_from"), ("news_events", "place_to"),
    ("news_sieges", "place"), ("news_siege_results", "place"),
    ("claims", "place"), ("claims", "subject_place"), ("claim_market", "location"),
    ("ref_cities", "city"), ("ref_city_towers", "city"), ("ref_city_ingress", "city"),
    ("ref_city_egress_denied", "city"), ("ref_city_roads", "city"), ("ref_city_roads", "destination"),
    ("ref_city_officials", "city"), ("ref_city_market", "city"), ("ref_personae", "location"),
    ("turn_location_roads", "destination"),
]

# (table, given-name column, family column, persona ID column to fill) for characters named without an ID.
PERSONA_MENTIONS = [
    ("news_events", "actor_given", "actor_family", "actor_persona_id"),
    ("news_sieges", "besieger_given", "besieger_family", "besieger_persona_id"),
    ("claims", "subject_given", "subject_family", "subject_persona_id"),
]

# (table, column, kind of source) for every column holding a family name.
FAMILY_COLUMNS = [
    ("turn_persona_status", "family", "official"), ("turn_persona_allies", "ally_family", "official"),
    ("turn_location_ingress", "family", "official"), ("turn_presence", "family", "official"),
    ("turn_lookups", "family", "official"), ("news_events", "actor_family", "official"),
    ("news_events", "target_family", "official"), ("news_sieges", "besieger_family", "official"),
    ("claims", "subject_family", "claim"), ("sources", "player_family", "claim"),
    # An ally's transcription of GM reports counts with the official record for family presence.
    ("ref_city_towers", "family", "official"), ("ref_city_ingress", "family", "official"),
    ("ref_city_egress_denied", "family", "official"), ("ref_personae", "family", "official"),
]

TURN_TABLE_NAMES = {
    "persona_status": "turn_persona_status", "persona_skills": "turn_persona_skills",
    "persona_assets": "turn_persona_assets", "persona_allies": "turn_persona_allies",
    "location_status": "turn_location_status", "location_ingress": "turn_location_ingress",
    "location_officials": "turn_location_officials", "market": "turn_market",
    "presence": "turn_presence", "lookups": "turn_lookups",
}


def combine(parsed_files: list[dict[str, list[dict]]]) -> dict[str, list[dict]]:
    """Merge many files' parse output into one dict of table -> rows, with final table names."""
    tables: dict[str, list[dict]] = defaultdict(list)
    for parsed in parsed_files:
        for name, rows in parsed.items():
            tables[TURN_TABLE_NAMES.get(name, name)].extend(rows)
    return dict(tables)


def normalize(tables: dict[str, list[dict]]) -> dict[str, list[dict]]:
    # ---- place keys on every place column ---------------------------------
    spellings: dict[str, Counter] = defaultdict(Counter)
    place_aliases = load_place_aliases()
    for table, col in PLACE_COLUMNS:
        for row in tables.get(table, []):
            key = place_key(row.get(col))
            key = place_aliases.get(key, key)
            row[f"{col}_key"] = key
            if key:
                spellings[key][row[col].strip()] += 1

    # ---- coordinates: from location reports and lookup answers -------------
    coords: dict[str, tuple[float, float]] = {}
    for row in tables.get("turn_location_status", []) + tables.get("turn_lookups", []):
        key = row.get("location_key") or row.get("place_key")
        if row.get("lat") is not None:
            coords[key] = (row["lat"], row["lon"], "turn report")
    # Then an ally's compiled reference, then player publications: never override a fresher source.
    for row in tables.get("ref_cities", []):
        if row.get("city_key") and row["city_key"] not in coords:
            coords[row["city_key"]] = (row["lat"], row["lon"], "ally reference")
    for row in tables.get("claims", []):
        if row.get("lat_text") and row.get("place_key") and row["place_key"] not in coords:
            coords[row["place_key"]] = (coord(row["lat_text"].replace(" ", "")),
                                        coord(row["lon_text"].replace(" ", "")), "player claim")

    # ---- dim_places ---------------------------------------------------------
    def best_name(counter: Counter) -> str:
        # Prefer a normally capitalised spelling ('Pegu') over 'PEGU' / 'pNompenh'.
        nice = [s for s, _ in counter.most_common() if s[:1].isupper() and not s.isupper()]
        return nice[0] if nice else display_name(counter.most_common(1)[0][0])

    # "Home" = where my account's latest turn report is from.
    my_turns = sorted((r for r in tables.get("turn_location_status", []) if r.get("account") == MY_ACCOUNT),
                      key=lambda r: r["game_date"])
    home = (my_turns[-1]["lat"], my_turns[-1]["lon"]) if my_turns else (None, None)

    def place_row(key, c):
        lat, lon, src = coords.get(key, (None, None, None))
        return {"place_key": key, "name": best_name(c), "lat": lat, "lon": lon, "coords_from": src,
                "miles_from_home": miles_between(home[0], home[1], lat, lon),
                "mentions": sum(c.values()), "spellings": " | ".join(sorted(c)), "spelling_count": len(c)}
    tables["dim_places"] = [place_row(key, c) for key, c in sorted(spellings.items())]

    # ---- family keys + dim_families ---------------------------------------------
    aliases = load_family_aliases()
    fam: dict[str, dict] = {}
    for table, col, kind in FAMILY_COLUMNS:
        for row in tables.get(table, []):
            if table == "sources":   # a source row is official or a claim depending on what it is
                kind = "claim" if row.get("reliability") == "player_claim" else "official"
                if row.get("reliability") == "ally_compiled":
                    continue
            raw = (row.get(col) or "").strip().upper() or None
            key = aliases.get(raw, raw) if raw else None
            row[f"{col}_key"] = key
            if key:
                f = fam.setdefault(key, {"family_key": key, "spellings": set(), "official_rows": 0, "claim_rows": 0})
                f["spellings"].add(raw)
                f[f"{kind}_rows"] += 1
    tables["dim_families"] = [{**f, "spellings": " | ".join(sorted(f["spellings"])),
                               "in_official": f["official_rows"] > 0, "in_claims": f["claim_rows"] > 0}
                              for f in sorted(fam.values(), key=lambda f: f["family_key"])]

    # ---- persona IDs for characters named without one ---------------------------
    persona_aliases = load_persona_aliases()
    for table, given, family, out in PERSONA_MENTIONS:
        for row in tables.get(table, []):
            row[out] = persona_aliases.get(((row.get(given) or "").strip().lower(), row.get(f"{family}_key")))

    # ---- road routes ----------------------------------------------------------
    for row in tables.get("news_events", []):
        if row["event_type"] == "road_shortened":
            a, b = sorted([row["place_from_key"], row["place_to_key"]])
            row["route_key"] = f"{a}|{b}"
        else:
            row["route_key"] = None

    # ---- order start locations: keyed to join dim_places, but not counted as mentions, since an
    # orders file is my own writing rather than a sighting in a source -------------------------
    for row in tables.get("orders_sent", []):
        key = place_key(row.get("start_location"))
        row["start_location_key"] = place_aliases.get(key, key)

    # ---- lookup distances from the turn's home location ----------------------
    home = {r["source_id"]: (r["lat"], r["lon"]) for r in tables.get("turn_location_status", [])}
    for row in tables.get("turn_lookups", []):
        h = home.get(row["source_id"], (None, None))
        row["miles_from_home"] = miles_between(h[0], h[1], row["lat"], row["lon"])

    # ---- dim_personae: one row per character ID, latest sighting wins --------
    personae: dict[int, dict] = {}
    # An ally reference's characters are dated by the reference itself.
    ref_dates = {s["source_id"]: s.get("game_date") or "" for s in tables.get("sources", [])
                 if s.get("kind") == "ally_reference"}
    # Personae with a full status block are the player's own; presence lists show everyone nearby.
    sightings = [(r, True) for r in tables.get("turn_persona_status", [])] + \
                [(r, False) for r in tables.get("turn_presence", [])] + \
                [({**r, "game_date": ref_dates.get(r["source_id"])}, False) for r in tables.get("ref_personae", [])]
    for row, own in sorted(sightings, key=lambda s: s[0]["game_date"]):
        p = personae.setdefault(row["persona_id"], {"persona_id": row["persona_id"],
                                                    "first_seen": row["game_date"], "is_mine": False})
        p.update(given_name=row["given_name"], family=row["family"], last_seen=row["game_date"],
                 last_location=row.get("location"),
                 is_mine=p["is_mine"] or (own and row.get("account") == MY_ACCOUNT))
    tables["dim_personae"] = list(personae.values())

    # ---- my_settings: who "my" is in the views (rensim.toml) ----------------------
    tables["my_settings"] = [{"account": MY_ACCOUNT, "family": MY_FAMILY, "home": HOME}]
    return tables
