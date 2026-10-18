"""Parse a Renaissance turn report (e.g. abc.txt) into rows for each table.

The report layout, top to bottom:
  1. Header: account code, game date / real date, actions used
  2. Billing + mailing address          -> deliberately DROPPED (private)
  3. One block per persona (character): identity and titles, office, morale/popularity/piety,
     location, skills, allies, assets
  4. One block per location the family is reported on, each starting after a '*****' line:
     defenses, tariffs, family towers, ingress, officials, roads, market, personae present
  5. A final block of answers to lookup orders ("X is at N55.57, W3.10") and GM messages

Small families get one location block; larger ones get one per location held.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import coord, parse_date, read_text, to_int

DATE = r"[A-Za-z]+ \d+\s*, \d+"
HEADER_RE = re.compile(rf"ACCOUNT CODE:\s*(?P<account>\S+)\s+(?P<game>{DATE})\s*/\s*(?P<real>{DATE})")
ACTIONS_RE = re.compile(r"ACTIONS=\s*(?P<family>\S+):\s*(?P<n>\d+)")
PERSONA_RE = re.compile(r"^\s*(?P<given>[^(]+?)\(\s*(?P<id>\d+)\)(?P<family>[A-Za-z'\-]+)\s*,(?P<rest>.*)$")
SKILL_RE = re.compile(r"([a-z]+):\s*(\d+)")
LOCATION_RE = re.compile(
    r"^(?P<name>\S.*?)\s*\((?P<lat>[NS][\d.]+),\s*(?P<lon>[EW][\d.]+)\)\s+FAS:\s*(?P<fas>\d+)\s+FAR:\s*(?P<far>\d+)"
    r"\s+GARRISON:\s*(?P<garrison>\d+)\s+RECRUITS:\s*(?P<recruits>\d+)")
TARIFF_RE = re.compile(r"IMPORT TARIFF:\s*(?P<imp>\d+)%\s+EXPORT TARIFF:\s*(?P<exp>\d+)%\s*(?P<note>.*)")
POP_RE = re.compile(
    r"POPULATION:\s*(?P<pop>\d+)\s+PORT DEPTH:\s*(?P<port>\d+)\s+WEALTH:\s*(?P<wealth>\d+)\s+LOYALTY:\s*(?P<loyalty>\d+)")
OFFICIAL_RE = re.compile(r"^(?P<role>[A-Z]+)(?:\((?P<code>[A-Za-z])\))?\s+(?P<holder>.*?)\s+TOWER:\s*(?P<tower>\d+)\s*$")
TOWER_RE = re.compile(r"([A-Za-z'\-]+)\s+(\d+)")
ROAD_RE = re.compile(r"([A-Za-z][\w' \-]*?)\s+(\d+)\s+miles")
MARKET_RE = re.compile(r"^\s*(?P<bq>[\d.]+)\s+(?P<item>.+?)\s*(?P<price>\d+)\s+(?P<qty>\d+)\s*$")
PERSONA_REF_RE = re.compile(r"^\s*(?P<given>[^(]+?)\(\s*(?P<id>\d+)\)\s*(?P<family>[A-Za-z'\-]+)(?P<rest>.*)$")
LOOKUP_AT_RE = re.compile(r"^(?P<family>\S+)\s+(?P<place>.+?)\s+is at\s+(?P<lat>[NS][\d.]+)\s*,\s*(?P<lon>[EW][\d.]+)")
LOOKUP_NOT_RE = re.compile(r"^(?P<family>\S+)\s+(?P<place>.+?)\s+is not a set up location")

# How a location's magistrate is chosen (rules: "Magistrates may be selected by election (e), or
# through appointment by the sovereign (s), feudatory (f), tenant (t), bishop (b), or archbishop (a)").
MAGISTRATE_SELECTION = {"e": "election", "s": "sovereign", "f": "feudatory", "t": "tenant",
                        "b": "bishop", "a": "archbishop"}

TABLES = ("sources", "persona_status", "persona_skills", "persona_assets", "persona_allies",
          "location_status", "location_ingress", "location_egress_denied", "location_officials", "location_towers", "location_roads",
          "market", "presence", "lookups", "unparsed_lines")


def split_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    """Split the report at '*****' separator lines -> [(first line number, lines)]."""
    blocks, start, current = [], 0, []
    for n, line in enumerate(lines):
        if line.startswith("*****"):
            blocks.append((start, current))
            start, current = n + 1, []
        else:
            current.append(line)
    blocks.append((start, current))
    return blocks


def parse_turn(path: str | Path) -> dict[str, list[dict]]:
    path = Path(path)
    lines = read_text(path).split("\n")
    out: dict[str, list[dict]] = {t: [] for t in TABLES}

    # ---- 1. header -------------------------------------------------------
    head = HEADER_RE.search(lines[0])
    if not head:
        raise ValueError(f"{path.name}: not a turn report (no ACCOUNT CODE header)")
    game_date, real_date = parse_date(head["game"]), parse_date(head["real"])
    actions = ACTIONS_RE.search(lines[1])
    source_id = f"turn:{head['account']}:{game_date.isoformat()}"
    player_family = actions["family"].upper() if actions else None
    # account + family on every row: one account can play different families over time
    # (a new family after the old one falls), and allies' shared reports can be loaded too.
    base = {"source_id": source_id, "game_date": game_date.isoformat(),
            "account": head["account"], "player_family": player_family}
    out["sources"].append({
        **base, "kind": "turn_report", "reliability": "official",
        "file_name": path.name,
        "real_date": real_date.isoformat(),
        "actions_used": int(actions["n"]) if actions else None, "issue": None, "publisher": None,
    })

    def unparsed(section: str, n: int, line: str):
        out["unparsed_lines"].append({**base, "section": section, "line_no": n + 1, "text": line})

    # ---- 2. skip billing / address: everything up to the Copyright line --
    first = next((n for n, l in enumerate(lines) if l.strip().startswith("Copyright")), 1) + 1
    blocks = split_blocks(lines)
    # The persona section runs from after the Copyright line to the first separator.
    persona_end = blocks[0][0] + len(blocks[0][1])
    parse_personae(lines[first:persona_end], first, base, out, unparsed)

    # ---- 4/5. location blocks, then results --------------------------------
    for start, block in blocks[1:]:
        content = [(start + i, l) for i, l in enumerate(block) if l.strip()]
        if not content:
            continue
        if LOCATION_RE.match(content[0][1]):
            parse_location(content, base, out, unparsed)
        else:
            parse_results(content, base, out, unparsed)
    return out


# ---- personae -------------------------------------------------------------------
def parse_personae(lines, offset, base, out, unparsed):
    """Each persona: an identity line, 1-2 lines of office/stats, then SKILLS and ASSETS lines."""
    current, pending = None, []
    for i, line in enumerate(lines):
        n = offset + i
        if (m := PERSONA_RE.match(line)):
            # "COUNT of ALAHEJOS, PRINCE of COCA, Catholic, Citizen of Salamanca": read from the right.
            parts = [p.strip() for p in m["rest"].split(",")]
            current = {**base, "persona_id": int(m["id"]), "given_name": m["given"].strip(),
                       "family": m["family"].upper(), "title": ", ".join(p for p in parts[:-2] if p) or None,
                       "religion": parts[-2] or None if len(parts) >= 2 else None,
                       "status": parts[-1] or None}
            out["persona_status"].append(current)
            pending = []
        elif current and line.startswith("SKILLS"):
            finish_stats(current, pending, n, unparsed)
            pending = None
            skills_part, _, allies_part = line.partition(". ALLIES -")
            for skill, level in SKILL_RE.findall(skills_part):
                out["persona_skills"].append({**base, "persona_id": current["persona_id"],
                                              "skill": skill, "level": int(level)})
            for ally in allies_part.split():
                out["persona_allies"].append({**base, "persona_id": current["persona_id"],
                                              "ally_family": ally.upper()})
        elif current and line.startswith("ASSETS"):
            for item in line.split("-", 1)[1].split(";"):
                name, _, qty = item.partition(":")
                if name.strip():
                    out["persona_assets"].append({**base, "persona_id": current["persona_id"],
                                                  "asset": name.strip().lower(), "quantity": to_int(qty)})
        elif current is not None and pending is not None and line.strip():
            pending.append(line.strip())
        elif line.strip():
            unparsed("personae", n, line)


def finish_stats(persona: dict, pending: list[str], n: int, unparsed) -> None:
    """The lines between identity and SKILLS: '[office] [SEE:x] [notes] MORALE:.. POPULARITY:.. [PIETY:..] LOCATION:..'"""
    text = " ".join(pending)
    stats = re.search(r"MORALE:\s*(\d+)\s+POPULARITY:\s*(\d+)(?:\s+PIETY:\s*(\d+))?\s+LOCATION:\s*(.+?)\s*$", text)
    if not stats:
        unparsed("personae", n, text)
        return
    head = text[:stats.start()].strip()
    see = re.search(r"\bSEE:\s*(\S+)", head)
    if see:
        head = (head[:see.start()] + head[see.end():]).strip()
    office, _, notes = head.partition(". ")
    persona.update(office=office.rstrip(".") or None, office_notes=notes.strip() or None,
                   see=see.group(1) if see else None, morale=int(stats[1]), popularity=int(stats[2]),
                   piety=int(stats[3]) if stats[3] else None, location=stats[4].strip())


# ---- locations ------------------------------------------------------------------
def parse_location(content, base, out, unparsed):
    n0, first = content[0]
    m = LOCATION_RE.match(first)
    loc = {**base, "location": m["name"].strip(), "lat": coord(m["lat"]), "lon": coord(m["lon"]),
           "fas": int(m["fas"]), "far": int(m["far"]), "garrison": int(m["garrison"]),
           "recruits": int(m["recruits"])}
    out["location_status"].append(loc)
    at = {**base, "location": loc["location"]}
    market_side = None
    for n, line in content[1:]:
        s = line.strip()
        towers = None
        if (t := TARIFF_RE.search(line)):
            note, _, towers = t["note"].partition("FAMILY TOWERS:")
            loc.update(import_tariff_pct=int(t["imp"]), export_tariff_pct=int(t["exp"]),
                       market_note=" ".join(note.split()) or None)
        elif s.startswith("FAMILY TOWERS:"):
            towers = s.split(":", 1)[1]
        elif s.startswith("INGRESS IS HELD ONLY BY:"):
            for fam in s.split(":", 1)[1].split():
                out["location_ingress"].append({**at, "family": fam.upper()})
        elif s.startswith("EGRESS DENIED TO:"):
            for fam in s.split(":", 1)[1].split():
                out["location_egress_denied"].append({**at, "family": fam.upper()})
        elif (p := POP_RE.search(line)):
            loc.update(population=int(p["pop"]), port_depth=int(p["port"]),
                       wealth=int(p["wealth"]), loyalty=int(p["loyalty"]))
        elif (o := OFFICIAL_RE.match(line)):
            code = o["code"]
            out["location_officials"].append({
                **at, "role": o["role"], "holder": " ".join(o["holder"].split()), "tower": int(o["tower"]),
                "selection_code": code, "selected_by": MAGISTRATE_SELECTION.get(code.lower()) if code else None})
        elif s.startswith("ROADS:"):
            loc["roads"] = s.split(":", 1)[1].strip()
            for dest, miles in ROAD_RE.findall(loc["roads"]):
                out["location_roads"].append({**at, "destination": dest.strip(), "miles": int(miles)})
        elif "ITEMS DEMANDED" in line:
            market_side = "demand"
        elif "ITEMS SUPPLIED" in line:
            market_side = "supply"
        elif market_side and (mk := MARKET_RE.match(line)):
            out["market"].append({**at, "side": market_side, "item": mk["item"].strip().lower(),
                                  "bq": float(mk["bq"]), "price": int(mk["price"]), "quantity": int(mk["qty"])})
        elif s.startswith("*** PERSONAE"):
            where = "inside" if "INSIDE" in s else "outside"
            for entry in s.split(":", 1)[1].split(";"):
                if (pr := PERSONA_REF_RE.match(entry)):
                    troops = re.search(r"([\d,]+)\s*T\.", pr["rest"])
                    ships = re.search(r"([\d,]+)\s*S\.", pr["rest"])
                    out["presence"].append({**at, "inside_or_outside": where, "persona_id": int(pr["id"]),
                                            "given_name": pr["given"].strip(), "family": pr["family"].upper(),
                                            "troops": to_int(troops[1]) if troops else None,
                                            "ships": to_int(ships[1]) if ships else None})
        elif not set(s) <= set("- ") and "BQ" not in s:
            unparsed("location", n, line)
        if towers:
            for fam, fas in TOWER_RE.findall(towers):
                out["location_towers"].append({**at, "family": fam.upper(), "tower_fas": int(fas)})


# ---- lookup answers and messages -----------------------------------------------------
def parse_results(content, base, out, unparsed):
    for n, line in content:
        if (m := LOOKUP_AT_RE.match(line)):
            out["lookups"].append({**base, "family": m["family"].upper(), "place": m["place"].strip(),
                                   "is_set_up": True, "lat": coord(m["lat"]), "lon": coord(m["lon"])})
        elif (m := LOOKUP_NOT_RE.match(line)):
            out["lookups"].append({**base, "family": m["family"].upper(), "place": m["place"].strip(),
                                   "is_set_up": False, "lat": None, "lon": None})
        else:
            unparsed("results", n, line)
