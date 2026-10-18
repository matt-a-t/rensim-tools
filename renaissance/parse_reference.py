"""Parse an ally's master city reference (a *_Master_City_Data_*.docx) into ref_* tables.

Some players consolidate the location and character data from many months of turn reports into
one Word document: a characters section, then one section per city with the latest data they
had for it. It is GM-reported data transcribed by an ally, so it
gets its own tables and reliability ('ally_compiled'): more trustworthy than player claims,
but second-hand and each city only as fresh as its 'Latest data' date.

Document layout per city:
  Heading 1  "Agadir  (N30.30,W9.40)"
  "Latest data: <Family> January 31, 1547"
  table      FAS | FAR | Garrison | Recruits | Population | Port Depth | Wealth | Loyalty
  "Import / Export Tariff: 5% / 5%"
  "Market: EXTERNAL MARKET IS CLOSED ... DE FACTO CONTROLLER: Vasco CORTEREAL"
  "Family Towers: SADIK 1, Beniwadi 25, ..."     "Ingress held only by: ..."
  ["Egress denied to: ..."]                        "Roads: none" / "Roads: X  199 miles. ..."
  Heading 3 "Governance"     table Role | Holder | Tower
  Heading 3 "Items Demanded" table Item | BQ | Price | Quantity Demanded
  Heading 3 "Items Supplied" table Item | BQ | Price | Quantity Available
  [Heading 3 "Notes" + paragraphs]
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import coord, parse_date, to_int
from .parse_turn import MAGISTRATE_SELECTION, ROAD_RE, TOWER_RE
from .settings import ALLY

CITY_RE = re.compile(r"^(?P<name>.*?)\s+\((?P<lat>[NS][\d.]+),\s*(?P<lon>[EW][\d.]+)\)$")
LATEST_RE = re.compile(r"^Latest data:\s*(?P<who>.+?)\s+(?P<date>[A-Z][a-z]+ \d+\s*, \d{4})")
CHAR_RE = re.compile(r"^(?P<given>.+?)\s*\((?P<id>\d+)\)\s*(?P<family>[A-Za-z'\-]+)$")
SKILL_RE = re.compile(r"([a-z]+):\s*(\d+)")
ROLE_RE = re.compile(r"^(?P<role>[A-Z]+)(?:\((?P<code>[A-Za-z])\))?$")
RELIGIONS = ("Catholic", "Orthodox", "Monophysite", "Shia", "Sunni", "Jewish", "Hindu", "Buddhist", "Animist", "Protestant")


def split_title_line(line: str):
    """'DUKE of BEARN, BARON of ALBRET, VISCOUNT of LIMOGES, Catholi' -> (title, religion, status).
    The reference's table cuts this column at 60 characters, so the religion may be truncated and the
    status missing; the religion is found by name (or a cut-off prefix of one), not by position."""
    parts = [p.strip() for p in line.split(",")]
    for i, part in enumerate(parts):
        hit = next((r for r in RELIGIONS if part and len(part) >= 3 and r.startswith(part)), None)
        if hit:
            status = parts[i + 1] if i + 1 < len(parts) else None
            return ", ".join(parts[:i]) or None, hit, status
    return line or None, None, None

TABLES = ("sources", "ref_cities", "ref_city_towers", "ref_city_ingress", "ref_city_egress_denied",
          "ref_city_roads", "ref_city_officials", "ref_city_market", "ref_personae",
          "ref_persona_skills", "ref_persona_assets")


def body_items(path: Path):
    """The document body in order: ('p', style, text) and ('t', rows-of-cell-text)."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(str(path))
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            if p.text.strip():
                yield "p", p.style.name, p.text.strip()
        elif tag == "tbl":
            yield "t", None, [[c.text.strip() for c in r.cells] for r in Table(child, d).rows]


def parse_reference(path: str | Path) -> dict[str, list[dict]]:
    path = Path(path)
    out: dict[str, list[dict]] = {t: [] for t in TABLES}
    # Who compiled it comes from [ally] in rensim.toml; the date defaults to its freshest city.
    account = (ALLY.get("account") or "").upper() or None
    source_id = f"ref:{(account or 'ally').lower()}:{path.stem.lower()}"
    source = {
        "source_id": source_id, "kind": "ally_reference", "reliability": "ally_compiled",
        "file_name": path.name, "title": ALLY.get("title") or f"Ally reference ({path.stem})",
        "publisher": ALLY.get("publisher"), "account": account,
        "player_family": (ALLY.get("family") or "").upper() or None,
        "game_date": ALLY.get("game_date"), "real_date": None, "actions_used": None, "issue": None,
    }
    out["sources"].append(source)
    base = {"source_id": source_id}
    city = None
    section = None            # which Heading 3 we're under, or the character tables
    for kind, style, content in body_items(path):
        if kind == "p" and style == "Heading 1":
            m = CITY_RE.match(content)
            section = content if not m else None
            if m:
                city = {**base, "city": m["name"].strip(), "lat": coord(m["lat"]), "lon": coord(m["lon"]),
                        "as_of": None, "as_of_from": None, "notes": None}
                out["ref_cities"].append(city)
            continue
        if kind == "p" and style == "Heading 3":
            section = content
            continue
        if kind == "t":
            parse_table(content, section, city, base, out)
            continue
        if city is None:
            continue
        text = content
        at = {**base, "city": city["city"], "as_of": city["as_of"]}
        if (m := LATEST_RE.match(text)):
            city["as_of"], city["as_of_from"] = parse_date(m["date"]).isoformat(), m["who"].strip()
        elif text.startswith("Import / Export Tariff:"):
            imp, exp = re.findall(r"(\d+)%", text)[:2]
            city.update(import_tariff_pct=int(imp), export_tariff_pct=int(exp))
        elif text.startswith("Market:"):
            note, _, controller = text.split(":", 1)[1].partition("DE FACTO CONTROLLER:")
            city.update(market_note=" ".join(note.split()) or None, de_facto_controller=controller.strip() or None)
        elif text.startswith("DE FACTO CONTROLLER:"):
            city["de_facto_controller"] = text.split(":", 1)[1].strip()
        elif text.startswith("Family Towers:"):
            for fam, fas in TOWER_RE.findall(text.split(":", 1)[1]):
                out["ref_city_towers"].append({**at, "family": fam.upper(), "tower_fas": int(fas)})
        elif text.startswith("Ingress held only by:"):
            for fam in text.split(":", 1)[1].split():
                out["ref_city_ingress"].append({**at, "family": fam.upper()})
        elif text.startswith("Egress denied to:"):
            for fam in text.split(":", 1)[1].split():
                out["ref_city_egress_denied"].append({**at, "family": fam.upper()})
        elif text.startswith("Roads:"):
            city["roads"] = text.split(":", 1)[1].strip()
            for dest, miles in ROAD_RE.findall(city["roads"]):
                out["ref_city_roads"].append({**at, "destination": dest.strip(), "miles": int(miles)})
        else:
            # Notes, colonization remarks and stray lines are kept with the city, not dropped.
            city["notes"] = (city["notes"] + " | " if city["notes"] else "") + " ".join(text.split())
    if not source["game_date"]:
        source["game_date"] = max((c["as_of"] for c in out["ref_cities"] if c.get("as_of")), default=None)
    return out


def parse_table(rows, section, city, base, out):
    header, data = rows[0], rows[1:]
    if header[:2] == ["FAS", "FAR"] and city is not None:
        v = dict(zip(header, data[0]))
        city.update(fas=to_int(v["FAS"]), far=to_int(v["FAR"]), garrison=to_int(v["Garrison"]),
                    recruits=to_int(v["Recruits"]), population=to_int(v["Population"]),
                    port_depth=to_int(v["Port Depth"]), wealth=to_int(v["Wealth"]), loyalty=to_int(v["Loyalty"]))
    elif header == ["Role", "Holder", "Tower"] and city is not None:
        for role, holder, tower in data:
            m = ROLE_RE.match(role.strip())
            code = m["code"] if m else None
            out["ref_city_officials"].append({
                **base, "city": city["city"], "as_of": city["as_of"], "role": m["role"] if m else role,
                "holder": " ".join(holder.split()), "tower": to_int(tower), "selection_code": code,
                "selected_by": MAGISTRATE_SELECTION.get(code.lower()) if code else None})
    elif header[:3] == ["Item", "BQ", "Price"] and city is not None:
        side = "demand" if "Demanded" in header[3] else "supply"
        for item, bq, price, qty in data:
            if not item.strip():
                continue
            out["ref_city_market"].append({
                **base, "city": city["city"], "as_of": city["as_of"], "side": side, "item": item.strip().lower(),
                "bq": float(bq) if bq.strip() else None, "price": to_int(price), "quantity": to_int(qty)})
    elif header and header[0] == "Character":
        parse_character_table(header, data, base, out)


def parse_character_table(header, data, base, out):
    for row in data:
        m = CHAR_RE.match(row[0])
        if not m:
            continue
        key = {**base, "persona_id": int(m["id"])}
        if header[1] == "Title":
            title, religion, status = split_title_line(row[1])
            out["ref_personae"].append({
                **key, "given_name": m["given"].strip(), "family": m["family"].upper(),
                "title_line": row[1], "title": title, "title_truncated": len(row[1]) >= 60,
                "religion": religion, "status": status, "location": row[2] or None,
                "morale": to_int(row[3]), "popularity": to_int(row[4]),
                "piety": to_int(row[5]) if row[5].strip() not in ("", "—", "-") else None,
                "ducats": to_int(row[6]) if row[6].strip() not in ("", "—", "-") else None})
        elif header[1] == "Skills":
            for skill, level in SKILL_RE.findall(row[1]):
                out["ref_persona_skills"].append({**key, "skill": skill, "level": int(level)})
        elif header[1] == "Assets":
            for item in row[1].split(";"):
                name, _, qty = item.partition(":")
                if name.strip():
                    out["ref_persona_assets"].append({**key, "asset": name.strip().lower(), "quantity": to_int(qty)})
