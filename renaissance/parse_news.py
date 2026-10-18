"""Parse the GM's official Renaissance Newsletter (e.g. NEWSL651).

Layout: a header (issue, real date, GM), then sections that start with an ALL-CAPS heading
followed by ' - ' ("APRIL ORDERS - ...", "SIEGES - ...", "MARCH EVENTS - ...").
Event lines follow a handful of fixed sentence templates, so they parse with regexes.
Anything that doesn't match a template is still kept (event_type='other' / notices table),
so nothing in the file is silently lost.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import MONTHS, read_text

ISSUE_RE = re.compile(r"Issue Number\s+(?P<issue>\d+)\s+(?P<date>[A-Za-z]+ \d+, \d+)")
# A section starts at the left margin with an ALL-CAPS heading, then ' - ' or plain text:
#   "SIEGES - Basel remains..."   /   "MARCH MARKET CHANGES are reflected..."
SECTION_RE = re.compile(r"^(?P<head>[A-Z][A-Z]+(?: [A-Z]+)*)(?: - | (?=[a-z]))(?P<rest>.*)$")
ORDERS_RE = re.compile(r"Orders for (?P<month>[A-Za-z]+) (?P<year>\d{4})")
SIEGE_RE = re.compile(r"^(?P<place>.+?) remains under siege by (?P<by>[^,]+?)(?:,\s*(?P<by_title>.+?))?\.?$")
SIEGE_RESULT_RE = re.compile(
    r"(?:(?P<place>[A-Z][A-Za-z ]+): )?The besiegers (?P<outcome>succeeded|failed)(?P<detail>.*?)\."
    r"\s*The defenders suffered (?P<attrition>\d+)% attrition")

# Each event template: (event_type, regex). Named groups map onto event columns.
# The GM writes routine events as one-line templates and military news as prose; both are covered,
# and anything unmatched is kept as event_type 'other' with its full text.
WHO = r"(?P<actor>[A-Z][\w'\-]*(?: [\w'.\-]+)*?)"
PERSON = r"(?P<actor>[A-Z][\w']+(?: [A-Z][\w']+)*?)"          # 'Enrique Talavera', 'Count Sigismund Jagiello'
TITLE = r"(?:,\s*(?P<actor_title>[^,()]+?)\)?\s*,)?"           # optional ', Bishop of Agde,'
AUX = r"(?:.*?(?P<quantity>[\d,]+) auxiliaries)?"
EVENT_TEMPLATES = [
    ("road_shortened", re.compile(
        WHO + r" shortened the road from (?P<place_from>.+?) to (?P<place_to>.+?) to\s+(?P<miles>\d+)\s*mi(?:les?|l)?\.?$")),
    ("magistracy_appointed", re.compile(WHO + r" was appointed to the magistracy of (?P<place>.+?)\.$")),
    ("magistracy_reelected", re.compile(PERSON + r" was reelected to the Magistracy of (?P<place>.+?) with the votes? of")),
    ("market_opened", re.compile(WHO + r" opened the market at (?P<place>.+?)\.$")),
    ("colonists_planted", re.compile(WHO + r" planted (?P<quantity>\d+) colonists at (?P<place>.+?)\.$")),
    ("indulgence_granted", re.compile(WHO + r" was granted an indulgence at (?P<place>.+?)\.$")),
    ("harbor_deepened", re.compile(WHO + r" deepened the harbor at (?P<place>.+?) to\s+(?P<quantity>\d+) feet\.?$")),
    ("pilgrimage_completed", re.compile(
        r"^(?:Pilgrimages|A pilgrimage) to (?P<place>.+?) (?:were|was) completed by (?P<actors>.+?)(?:, (?P<actor_title>[A-Z][^,]*? of [^,]+))?\.?$")),
    ("tower_captured", re.compile(
        r"^The (?:undefended )?(?:(?P<target_family>[A-Z]\w+) family |\"[^\"]+\" |FAR\d+ )?tower(?: \(FAS=\d+\))? at (?P<place>\w+)"
        r".*? was captured by (?P<method>\w+) by " + PERSON + TITLE + r"\s*with " + AUX)),
    ("location_captured", re.compile(
        r"^The (?:undefended )?(?:town|colonial site|city) of (?P<place>[\w' ]+?) \(.*? was captured by (?P<method>\w+) by "
        + PERSON + TITLE + r"\s*with " + AUX)),
    ("location_captured", re.compile(
        r"^" + PERSON + r", (?P<actor_title>[^,]+), captured (?P<place>\w+) \(.*?by (?P<method>assault)" + AUX)),
    ("de_facto_control", re.compile(PERSON + r"(?: and his .*?)? took de facto control of (?P<place>\w+) by defeating")),
    ("de_facto_control", re.compile(
        r"^The town of (?P<place>\w+), .*?had its de facto control taken through urban battle orders issued by "
        + PERSON + r", (?P<actor_title>[^,]+?), to " + AUX)),
]
# Paragraphs are split into sentences and each is matched on its own
# ("Pilgrimages ... were completed by A, B and C. The undefended town of Palencia ... was captured ...").
SENTENCE_SPLIT = re.compile(r"(?<=\.)\s+(?=(?:The |A |Pilgrimages |[A-Z][a-z]+ [A-Z][a-z]+ (?:was|took)))")


def split_actor(actor: str | None):
    """'Zana MARTABAN' -> ('Zana', 'MARTABAN'); the family is the last word."""
    if not actor:
        return None, None
    parts = actor.split()
    return " ".join(parts[:-1]) or None, parts[-1].upper()


def parse_newsletter(path: str | Path) -> dict[str, list[dict]]:
    path = Path(path)
    text = read_text(path)
    lines = text.split("\n")
    out = {t: [] for t in ("sources", "news_events", "news_sieges", "news_siege_results", "news_notices")}

    head = ISSUE_RE.search(text)
    if not head:
        raise ValueError(f"{path.name}: no 'Issue Number' header found")
    issue = int(head["issue"])
    from_name = re.search(r"(\d{3,})", path.name)
    if from_name and from_name[1].startswith(head["issue"]) and len(from_name[1]) > len(head["issue"]):
        issue = int(from_name[1])        # header was cut short ("Issue Number 63" in NEWSL639)
    real_date = re.sub(r"\s+", " ", head["date"])
    from .common import parse_date
    real_date = parse_date(real_date)

    # The issue reports results for the month BEFORE the orders it asks for.
    orders = ORDERS_RE.search(text)
    game_month = None
    if orders:
        # The section heading ("DECEMBER ORDERS - Orders for November 1545") wins over the sentence:
        # #635 has exactly that typo, and the heading matches the issue sequence.
        heading = re.search(r"^([A-Z]+) ORDERS -", text, re.M)
        month = heading[1] if heading and heading[1].lower() in MONTHS else orders["month"]
        m, y = MONTHS[month.lower()], int(orders["year"])
        m, y = (m - 1, y) if m > 1 else (12, y - 1)
        game_month = f"{y:04d}-{m:02d}"

    source_id = f"news:renaissance:{issue}"
    base = {"source_id": source_id, "issue": issue, "game_month": game_month}
    out["sources"].append({
        "source_id": source_id, "kind": "gm_newsletter", "reliability": "official",
        "file_name": path.name, "account": None, "player_family": None,
        "game_date": f"{game_month}-01" if game_month else None, "real_date": real_date.isoformat(),
        "actions_used": None, "issue": issue, "publisher": "GM",
    })

    # ---- group lines into sections -------------------------------------------
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if (m := SECTION_RE.match(line)):
            sections.append((m["head"].strip(), [m["rest"]]))
        elif sections and not set(line.strip()) <= set("_"):
            sections[-1][1].append(line)

    for heading, body in sections:
        if heading.endswith("EVENTS"):
            _parse_events(body, base, out)
        elif heading in ("SIEGE", "SIEGES"):
            for raw in (l.strip() for l in body if l.strip()):
                if (m := SIEGE_RE.match(raw)):
                    g, f = split_actor(m["by"])
                    out["news_sieges"].append({**base, "place": m["place"].strip(), "besieger": m["by"].strip(),
                                               "besieger_given": g, "besieger_family": f,
                                               "besieger_title": (m["by_title"] or "").strip() or None,
                                               "raw_text": raw})
        elif heading.startswith("RESULT OF") and "SIEGE" in heading:
            joined = " ".join(l.strip() for l in body)
            sieged = [x["place"] for x in out["news_sieges"]]
            for m in SIEGE_RESULT_RE.finditer(joined):
                # With a single siege the GM omits the town ("RESULT OF DECEMBER SIEGE - The besiegers ...").
                place = m["place"] or (sieged[0] if len(sieged) == 1 else None)
                out["news_siege_results"].append({**base, "place": place.strip() if place else None, "outcome": m["outcome"],
                                                  "attrition_pct": int(m["attrition"]),
                                                  "detail": m["detail"].strip() or None})
        else:
            out["news_notices"].append({**base, "heading": heading,
                                        "text": " ".join(" ".join(l.split()) for l in body if l.strip())})
    return out


def _parse_events(body: list[str], base: dict, out: dict) -> None:
    """Events: the first one follows the heading; each later one starts on a line indented by
    two spaces. Lines without that indent continue the previous event (multi-line events)."""
    events: list[str] = []
    for n, line in enumerate(body):
        if n == 0 or re.match(r"^ {1,3}\S", line):
            events.append(line.strip())
        elif line.strip() and events:
            events[-1] += " " + line.strip()

    seq = 0
    for raw in events:
        pieces = SENTENCE_SPLIT.split(raw) or [raw]
        for piece in pieces:
            for row in event_rows(piece.strip(), base):
                seq += 1
                out["news_events"].append({**row, "seq": seq})


def match_event(text: str):
    for event_type, rx in EVENT_TEMPLATES:
        if (m := rx.search(text)):
            return event_type, m.groupdict()
    return None


def event_rows(raw: str, base: dict) -> list[dict]:
    """One template match -> one row, or one row per person for group events (pilgrimages)."""
    row = {**base, "event_type": "other", "actor": None, "actor_given": None,
           "actor_family": None, "actor_title": None, "place": None, "place_from": None,
           "place_to": None, "miles": None, "quantity": None, "target_family": None,
           "truncated": not raw.endswith("."), "raw_text": raw}   # a line cut off mid-word is flagged
    hit = match_event(raw)
    if not hit:
        return [row]
    event_type, g = hit
    row["event_type"] = event_type
    for k in ("actor", "actor_title", "place", "place_from", "place_to", "target_family"):
        if g.get(k):
            row[k] = g[k].strip()
    if g.get("miles"):
        row["miles"] = int(g["miles"])
    if g.get("quantity"):
        row["quantity"] = int(g["quantity"].replace(",", ""))
    if row["target_family"]:
        row["target_family"] = row["target_family"].upper()
    actors = [row["actor"]]
    if g.get("actors"):   # "Juan Borgia, Tomasso Euffreducci and Giambattista Zeno"
        actors = [a.strip() for a in re.split(r",\s*|\s+and\s+", g["actors"]) if a.strip()]
    rows = []
    for actor in actors:
        given, family = split_actor(actor)
        rows.append({**row, "actor": actor, "actor_given": given, "actor_family": family})
    return rows
