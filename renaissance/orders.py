"""Check a Renaissance orders file before sending it to the GM, and explain it in plain English.

    rensim orders my_orders.txt          # check + explain (or "-" to read stdin)
    rensim orders my_orders.txt --send   # print the text to send (no # notes)

Orders are drafted with Claude in chat and emailed to the GM. The final version sent each turn is
saved as orders/<account>_YYYY-MM.txt (with # notes explaining the plan) and loaded by `rensim run` into the
orders_sent table, so past moves can be looked up ("what did I do in January?").

Grammar from the GM's Action Codes sheet:

    /ABC *SMITH                                  player code, then family
    *Carlisle .120 pffar mlKelso*                per persona: starting location, .number, codes

Codes are separated by spaces and are not case-sensitive. Within a persona, orders go in this
order: transfers, sells, buys, other orders, then movement/battle. Lines starting with # are
notes for you and are stripped by --send.

What it checks against your latest turn report:
  - every code is a real code with the right shape
  - the persona belongs to you and starts where the order says
  - transfers and sells don't exceed what the persona holds; buys are affordable
  - groups are in the required order
  - movement destinations are places the database knows (with distance)
and it estimates the action fee from your last bill.
"""
from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .settings import ALLY_REFERENCE, MY_ACCOUNT, ORDERS_DIR, RAW

TURNS = RAW / "turns"

GROUPS = {1: "transfer", 2: "sell", 3: "buy", 4: "other", 5: "movement/battle"}
# Instruct skill letters: special cases from the code sheet, otherwise the skill's initial letter.
SKILL_CODES = {"q": "barquebuilding", "v": "caravelbuilding", "o": "galliotbuilding", "y": "galleybuilding",
               "x": "architecture", "t": "art", "z": "alchemy", "k": "assassination", "u": "gunsmithing",
               "l": "letters", "g": "gunnery", "m": "merchantry", "n": "navigation", "s": "sermonizing",
               "f": "fortification", "c": "cannonmaking", "a": "armory", "b": "bowmaking", "d": "dueling",
               "r": "roundshipbuilding"}
CATEGORIES = {"foo": "food", "alc": "alcohol", "clo": "clothing", "met": "metals", "con": "construction materials",
              "man": "manufactures", "spi": "spices", "dru": "drugs", "art": "art objects", "pre": "precious objects",
              "dye": "dyestuffs", "fib": "fibers", "nav": "naval materials", "raw": "raw materials", "wea": "weaponry"}
TROOPS = {"x": ("auxiliaries", 5, "smallarms"), "q": ("arquebusiers", 10, "arquebuses"), "b": ("archers", 20, "bows"),
          "p": ("pikemen", 15, "pikes"), "h": ("heavy cavalry", 50, "armor"), "c": ("crew", 0, None),
          "g": ("garrison troops", 0, "smallarms")}

# Freight (Movement section): horses carry 2 cargo each overland and slaves 1/4; troops, horses, slaves,
# cattle and sheep move themselves; ducats weigh nothing; cannons weigh 40; empty ships carried overland
# weigh barque 201, caravel 403, roundship 1,203, galliot 400, galley 4,000; everything else 1 per unit.
# Over capacity, a persona trying to move ends the turn just outside his starting location.
SELF_MOVING = {"horse", "slave", "cattle", "sheep", "ducat", "auxiliarie", "arquebusier", "archer", "pikemen",
               "heavy cavalry", "heavycavalry", "crew", "garrison troop", "garrison", "troop"}
CARGO_WEIGHT = {"cannon": 40, "barque": 201, "caravel": 403, "roundship": 1203, "galliot": 400, "galley": 4000}
CARRY = {"horse": 2, "slave": 0.25}


def norm_item(name: str) -> str:
    """'horses' and the order code's 'horse' (or 'Horse') are the same holding."""
    return name.lower().strip().removesuffix("s")


def find_holding(held: dict, item: str):
    """The holding an order's item refers to (codes cut names to 8 letters)."""
    item = norm_item(item)
    if item in held:
        return item
    return next((k for k in held if k.startswith(item[:8])), None)


def cargo(held: dict) -> tuple[float, float]:
    """(cargo to carry overland, carrying capacity) for one persona's holdings."""
    load = sum(q * CARGO_WEIGHT.get(k, 1) for k, q in held.items() if k not in SELF_MOVING and q > 0)
    capacity = sum(q * CARRY[k] for k, q in held.items() if k in CARRY and q > 0)
    return load, capacity


# (regex, group, explanation template). Checked in order; the first full match wins.
CODES: list[tuple[re.Pattern, int, str]] = [(re.compile(rx, re.I), g, text) for rx, g, text in [
    (r"t(?P<to>\d+)(?P<item>[a-z]+)(?P<qty>\d+)", 1, "Transfer {qty} {item} to persona {to}"),
    (r"s(?P<m>\d)(?P<cat>[a-z]{3})(?P<item>[a-z]+)(?P<qty>\d+)", 2, "Sell {qty} {item} (category {cat})"),
    (r"b(?P<item>[a-z]+)(?P<qty>\d+)(?P<soldout>y)?", 3, "Buy {qty} {item}"),
    (r"i(?P<who>\d+)(?P<skill>[a-z])", 4, "Instruct persona {who} in {skill}"),
    (r"pf(?P<target>[a-z]+)", 4, "Fortify: improve {target}"),
    (r"ph", 4, "Hold court (sovereigns only)"),
    (r"pi(?P<who>\d+)", 4, "Grant an indulgence to persona {who}"),
    (r"ps(?P<who>\d+)", 4, "Deliver a sermon to persona {who}'s troops"),
    (r"ps", 4, "Deliver a sermon to the city"),
    (r"p-(?P<kind>[la])(?P<who>\d+)", 4, "Produce a negative work of {kind} against persona {who}"),
    (r"p(?P<kind>[lax])(?P<who>\d+)", 4, "Produce a work of {kind} for persona {who}"),
    (r"pw(?P<arm>[sbqapcf])(?P<qty>\d+)", 4, "Produce {qty} {arm}"),
    (r"pb(?P<ship>[yorvq])(?P<qty>\d+)", 4, "Build {qty} {ship}"),
    (r"pm(?P<with>[dwbas])(?P<qty>\d+)", 4, "Raise morale, spending {qty} {with}"),
    (r"pt(?P<troop>[xqbphcg])(?P<qty>\d+)", 4, "Recruit {qty} {troop}"),
    (r"ptf", 4, "Raise a feudal levy (feudatory only; -10 loyalty)"),
    (r"pe\+(?P<dest>[a-z]+)", 4, "Lengthen the road to {dest}"),
    (r"pe#", 4, "Deepen the harbor"),
    (r"pe(?P<dest>[a-z]+)", 4, "Shorten the road to {dest}"),
    (r"pg", 4, "Grandmaster claims free heavy cavalry"),
    (r"pp(?P<qty>\d+)", 4, "Give {qty} ducats to charity (popularity)"),
    (r"pr(?P<qty>\d+)", 4, "Give {qty} ducats to charity (Moslem piety)"),
    (r"pverm(?P<qty>\d+)", 4, "Produce {qty} vermilion"),
    (r"pverd(?P<qty>\d+)", 4, "Produce {qty} verdigris"),
    (r"pc(?P<troop>[scxqpbh])(?P<qty>\d+)", 4, "Plant {qty} {troop} as colonists"),
    (r"a-(?P<family>[a-z]+)", 4, "End alliance with {family}"),
    (r"a(?P<family>[a-z]+)", 4, "Ally with {family}"),
    (r"n@(?P<family>[a-z]+)", 4, "Ask for the player name and address of {family}"),
    (r"n%(?P<place>[a-z]+)", 4, "Ask for the co-ordinates of {place}"),
    (r"n#(?P<place>[a-z]+)", 4, "Ask for the port depth of {place}"),
    (r"ll(?P<place>[a-z]+)", 4, "Location orders that follow apply at {place}"),
    (r"g(?P<act>om|cm)", 4, "{act} the external market"),
    (r"g(?P<act>ge|de|gi|di|gt|dt)(?P<family>[a-z]+)", 4, "{act} for {family}"),
    (r"lt(?P<imp>[+-]\d+)(?P<exp>[+-]\d+)", 4, "Change tariffs: import {imp}%, export {exp}%"),
    (r"lx", 4, "Collect taxes"), (r"lh", 4, "Collect tithes"),
    (r"la(?P<office>[xhbm])(?P<who>\d+)", 4, "Appoint persona {who} as {office}"),
    (r"lrm", 4, "Resign the magistracy"),
    (r"lcd(?P<who>\d+)", 4, "Transfer de facto control (0 = back to the tenant's office)"),
    (r"lcm(?P<how>[esftab])", 4, "Change magistrate selection to ({how})"),
    (r"tt(?P<tower>[osftmba])(?P<to>[a-z]+)", 4, "Transfer a tower to {to}"),
    (r"md(?:boats)?", 5, "Abandon excess cargo, including boats"),
    (r"mi", 5, "Move inside the starting location"),
    (r"m(?P<how>[slf])(?P<dest>[a-z]+)(?P<stay>\*)?", 5, "Move by {how} to {dest}"),
    (r"mr(?P<dest>[a-z]+)", 5, "Move by road to {dest}"),
    (r"mg(?P<who>\d+)", 5, "Let persona {who} board my ships"),
    (r"ma", 5, "Board the ship of a persona who gave permission"),
    (r"mo", 5, "Disembark"),
    (r"mto", 5, "Keep out of all towers"), (r"mti", 5, "Stop keeping out of towers"),
    (r"d(?P<who>\d+)", 5, "Challenge persona {who} to a duel"),
]]
WORDS = {"how": {"s": "ship", "l": "land", "f": "ferry"}, "kind": {"l": "letters", "a": "art", "x": "architecture"},
         "arm": {"s": "small arms", "b": "bows", "q": "arquebuses", "a": "armor", "p": "pikes", "c": "cannon", "f": "falconets"},
         "ship": {"y": "galleys", "o": "galliots", "r": "roundships", "v": "caravels", "q": "barques"},
         "with": {"d": "ducats", "w": "wine", "b": "beer", "a": "ale", "s": "spirits"},
         "office": {"x": "tax collector", "h": "tithe collector", "b": "bishop", "m": "magistrate"},
         "act": {"om": "Open", "cm": "Close", "ge": "Grant egress", "de": "Deny egress", "gi": "Grant ingress",
                 "di": "Deny ingress", "gt": "Grant external trade", "dt": "Deny external trade"}}


@dataclass
class Order:
    line: int
    code: str
    group: int | None = None
    fields: dict = field(default_factory=dict)
    explanation: str = ""


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)       # the plain-English walk-through
    training_pairs: set = field(default_factory=set)


def match_code(token: str) -> Order | None:
    for rx, group, text in CODES:
        if (m := rx.fullmatch(token)):
            f = {k: v for k, v in m.groupdict().items() if v is not None}
            shown = {k: WORDS.get(k, {}).get(v.lower(), v) for k, v in f.items()}
            if "skill" in f:
                shown["skill"] = SKILL_CODES.get(f["skill"].lower(), f"the skill starting with '{f['skill']}'")
            if "troop" in f:
                shown["troop"] = TROOPS.get(f["troop"].lower(), (f["troop"],))[0]
            if "cat" in f:
                shown["cat"] = CATEGORIES.get(f["cat"].lower(), f["cat"])
            try:
                text_out = text.format(**{k: shown.get(k, "") for k in re.findall(r"{(\w+)}", text)})
            except (KeyError, IndexError):
                text_out = text
            return Order(0, token, group, f, text_out)
    return None


# ---- the state the checks run against ------------------------------------------------
def latest_position():
    """My latest turn report, parsed: personae, assets, skills, location stats and market."""
    from .parse_turn import parse_turn
    mine = []
    for path in TURNS.glob("*.txt"):
        t = parse_turn(path)
        if t["sources"][0]["account"] == MY_ACCOUNT:
            mine.append((t["sources"][0]["game_date"], path, t))
    mine.sort()
    return mine[-1] if mine else None


def last_fee_per_action() -> float | None:
    """Observed from the bills in my turn reports: TOTAL ACTION FEES / actions used."""
    rates = []
    for path in TURNS.glob("*.txt"):
        text = path.read_bytes().decode("cp1252", "replace")
        if not re.match(rf"ACCOUNT CODE:\s*{re.escape(MY_ACCOUNT)}\s", text):
            continue
        a = re.search(r"ACTIONS=\s*\S+:\s*(\d+)", text)
        f = re.search(r"TOTAL ACTION FEES:\s*([\d.]+)", text)
        if a and f and int(a[1]) > 0:
            rates.append(float(f[1]) / int(a[1]))
    return round(sorted(rates)[len(rates) // 2], 3) if rates else None


def known_places() -> dict:
    """place_key -> (name, lat, lon) from every source the pipeline knows (no database needed)."""
    from .common import place_key
    from .parse_turn import parse_turn
    places = {}
    for path in TURNS.glob("*.txt"):
        t = parse_turn(path)
        for r in t["location_status"]:
            places.setdefault(place_key(r["location"]), (r["location"], r["lat"], r["lon"]))
        for r in t["lookups"]:
            places.setdefault(place_key(r["place"]), (r["place"], r["lat"], r["lon"]))
    ref = sorted(RAW.glob(ALLY_REFERENCE))
    if ref:
        try:
            from .parse_reference import parse_reference
            for r in parse_reference(ref[-1])["ref_cities"]:
                places.setdefault(place_key(r["city"]), (r["city"], r["lat"], r["lon"]))
        except ImportError:
            pass
    return places


# ---- checking ---------------------------------------------------------------------------
def check(text: str) -> Report:
    from .common import miles_between, place_key
    rep = Report()
    pos = latest_position()
    if not pos:
        rep.errors.append(f"No turn report for account {MY_ACCOUNT or '(not set)'} found in {TURNS}; "
                          "check account in rensim.toml.")
        return rep
    game_date, _, turn = pos
    personae = {p["persona_id"]: p for p in turn["persona_status"]}
    assets = {}
    for a in turn["persona_assets"]:
        assets.setdefault(a["persona_id"], {})[norm_item(a["asset"])] = a["quantity"] or 0
    skills = {}
    for s in turn["persona_skills"]:
        skills.setdefault(s["persona_id"], {})[s["skill"]] = s["level"]
    locs = {place_key(l["location"]): l for l in turn["location_status"]}
    supply = {(place_key(m["location"]), m["item"][:8]): m for m in turn["market"] if m["side"] == "supply"}
    places = known_places()
    family = turn["sources"][0]["player_family"]
    rep.notes.append(f"Checked against your {game_date} turn report ({family}).")

    header_seen, current, last_group, action_count = False, None, 0, 0
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("#") else ""
        if not line:
            continue
        if "\t" in raw:
            rep.errors.append(f"line {n}: contains a tab; the GM asks for spaces only.")
        tokens = line.split()
        if tokens[0].startswith("/"):
            header_seen = True
            if tokens[0][1:].upper() != MY_ACCOUNT:
                rep.errors.append(f"line {n}: player code is {tokens[0][1:]!r}, your account is {MY_ACCOUNT}.")
            fam = next((t[1:] for t in tokens[1:] if t.startswith("*")), None)
            if not fam:
                rep.errors.append(f"line {n}: the family name (*{family}) should follow the player code.")
            elif fam.upper() != family:
                rep.errors.append(f"line {n}: family is {fam!r}, your current family is {family}.")
            rep.lines.append(f"Orders for account {MY_ACCOUNT}, family {family}")
            continue
        if tokens[0].startswith("*"):
            if not header_seen:
                rep.errors.append(f"line {n}: persona orders before the /{MY_ACCOUNT} *{family} header line.")
            if len(tokens) < 2 or not re.fullmatch(r"\.\d+", tokens[1]):
                rep.errors.append(f"line {n}: expected '*Location .number' to start the persona's orders.")
                continue
            pid, start = int(tokens[1][1:]), tokens[0][1:]
            p = personae.get(pid)
            current, last_group = pid, 0
            if not p:
                rep.errors.append(f"line {n}: persona {pid} isn't one of yours on the {game_date} report "
                                  f"(yours: {', '.join(f'{k} {v['given_name']}' for k, v in personae.items())}).")
            elif place_key(start) != place_key(p.get("location")):
                rep.errors.append(f"line {n}: {p['given_name']} ({pid}) starts in {p.get('location')}, not {start}.")
            rep.lines.append(f"{(p or {}).get('given_name', '?')} ({pid}), starting in {start}:")
            tokens = tokens[2:]
        elif current is None:
            rep.errors.append(f"line {n}: orders before any '*Location .number' persona line.")
            continue
        for tok in tokens:
            o = match_code(tok)
            if not o:
                rep.errors.append(f"line {n}: '{tok}' isn't a valid order code.")
                continue
            action_count += 1
            o.line = n
            rep.lines.append(f"  {tok:<18} {o.explanation}")
            if o.group < last_group:
                rep.warnings.append(f"line {n}: '{tok}' ({GROUPS[o.group]}) comes after a {GROUPS[last_group]} order; "
                                    f"the GM asks for transfers, sells, buys, other, then movement.")
            last_group = max(last_group, o.group)
            check_order(o, current, personae, assets, skills, locs, supply, places, rep, miles_between, place_key)

    fee = last_fee_per_action()
    rep.notes.append(f"{action_count} order code(s)." + (f" At your last billed rate (${fee:.2f}/action) that's about "
                                                          f"${action_count * fee:.2f} in action fees." if fee else ""))
    return rep


def check_order(o, pid, personae, assets, skills, locs, supply, places, rep, miles_between, place_key):
    p = personae.get(pid) or {}
    held = assets.get(pid, {})
    f, code = o.fields, o.code.lower()
    loc = locs.get(place_key(p.get("location")))
    where = f"line {o.line}: '{o.code}'"
    item = f.get("item", "").lower()
    if item and len(item) > 8:
        rep.errors.append(f"{where}: item names are cut to 8 letters ('{item[:8]}').")
    # Holdings are updated as each transfer, sale and purchase is read (personae in file order),
    # so the cargo check on a move sees what the persona will actually carry.
    if code.startswith("t") and f.get("qty"):
        key = find_holding(held, item)
        have, qty = held.get(key, 0), int(f["qty"])
        if qty > have:
            rep.errors.append(f"{where}: {p.get('given_name')} holds {have:,} {item}, can't transfer {qty:,}.")
        elif key:
            held[key] = have - qty
            if int(f["to"]) in assets:
                assets[int(f["to"])][key] = assets[int(f["to"])].get(key, 0) + qty
        if int(f["to"]) != 9999 and int(f["to"]) not in personae:
            rep.warnings.append(f"{where}: persona {f['to']} isn't one of yours; check the number.")
    if o.group == 2:
        key = find_holding(held, item)
        have, qty = held.get(key, 0), int(f["qty"])
        if qty > have:
            rep.errors.append(f"{where}: {p.get('given_name')} holds {have:,} {item}, can't sell {qty:,}.")
        elif key:
            held[key] = have - qty
        if f["cat"].lower() not in CATEGORIES:
            rep.warnings.append(f"{where}: '{f['cat']}' isn't a known market category ({', '.join(CATEGORIES)}).")
    if o.group == 3:
        offer = supply.get((place_key(p.get("location")), item[:8]))
        if not offer:
            rep.warnings.append(f"{where}: {item} isn't listed as supplied at {p.get('location')} on your last report.")
        else:
            cost = offer["price"] * int(f["qty"])
            if cost > held.get("ducat", 0):
                rep.errors.append(f"{where}: costs about {cost:,} ducats; {p.get('given_name')} has {held.get('ducats', 0):,}.")
            else:
                rep.notes.append(f"{where}: about {cost:,} ducats at last turn's price of {offer['price']:,}.")
                held["ducat"] = held.get("ducat", 0) - cost
            key = norm_item(item)
            held[key] = held.get(key, 0) + int(f["qty"])
    if code.startswith("pf"):
        target = f["target"].lower()
        if target in ("fas", "far") and loc:
            cost = max(1000, loc.get("population") or 0)
            gain = math.sqrt(skills.get(pid, {}).get("fortification", 0))
            rep.notes.append(f"{where}: {loc['location']} {target.upper()} {loc[target]} -> about {loc[target] + gain:.0f} "
                             f"(+{gain:.0f}, from fortification {skills.get(pid, {}).get('fortification', 0)}), costs {cost:,} ducats.")
            if not skills.get(pid, {}).get("fortification"):
                rep.warnings.append(f"{where}: {p.get('given_name')} has no fortification skill, so this adds nothing.")
            if cost > held.get("ducat", 0):
                rep.errors.append(f"{where}: needs {cost:,} ducats; {p.get('given_name')} has {held.get('ducats', 0):,}.")
    if code.startswith("pt") and f.get("troop"):
        name, bonus, weapon = TROOPS[f["troop"].lower()]
        qty = int(f["qty"])
        if bonus:
            rep.notes.append(f"{where}: enlistment bonuses about {qty * bonus:,} ducats (x3 if recruiting outside the walls).")
        if weapon and held.get(weapon, 0) < qty:
            rep.errors.append(f"{where}: each recruit needs one {weapon}; {p.get('given_name')} has {held.get(weapon, 0):,}.")
        if loc and qty > (loc.get("recruits") or 0):
            rep.warnings.append(f"{where}: {loc['location']} had {loc.get('recruits')} recruits available last turn.")
    if o.group == 5 and (code.startswith("ml") or code.startswith("mr")):
        load, capacity = cargo(held)
        if load > capacity:
            goods = ", ".join(f"{q:,} {k}" for k, q in held.items() if k not in SELF_MOVING and q > 0)
            rep.errors.append(f"{where}: {p.get('given_name')} would carry {load:,.0f} cargo ({goods}) but his horses and "
                              f"slaves carry {capacity:,.0f}; he'd end the turn just outside {p.get('location')}. "
                              f"Transfer the goods to someone staying, or buy {math.ceil((load - capacity) / 2):,} horses.")
        else:
            rep.notes.append(f"{where}: cargo {load:,.0f} of {capacity:,.0f} capacity.")
    if o.group == 5 and f.get("dest"):
        dest = places.get(place_key(f["dest"]))
        if not dest:
            rep.warnings.append(f"{where}: '{f['dest']}' isn't a place the database knows; check the spelling.")
        elif loc and dest[1] is not None:
            miles = miles_between(loc["lat"], loc["lon"], dest[1], dest[2])
            limit = 200 if (p.get("popularity") or 0) >= 200 else (p.get("popularity") or 0)
            rep.notes.append(f"{where}: {dest[0]} is about {miles:.0f} miles straight-line"
                             + (f"; more than {limit} miles by land risks attrition." if miles and miles > limit else "."))
    if o.fields.get("skill") and code.startswith("i"):
        trainee = int(f["who"])
        skill = SKILL_CODES.get(f["skill"].lower())
        pair = (pid, trainee)
        if pair in rep.training_pairs:
            rep.errors.append(f"{where}: {p.get('given_name')} already trains persona {trainee} this turn (once per pair).")
        rep.training_pairs.add(pair)
        other = personae.get(trainee)
        if not other:
            rep.warnings.append(f"{where}: persona {trainee} isn't one of yours; training needs both at the same location.")
        elif place_key(other.get("location")) != place_key(p.get("location")):
            rep.errors.append(f"{where}: {other['given_name']} doesn't start in {p.get('location')}; they must start together.")
        elif skill:
            mine, theirs = skills.get(pid, {}).get(skill, 0), skills.get(trainee, {}).get(skill, 0)
            if mine <= theirs:
                rep.warnings.append(f"{where}: {p.get('given_name')}'s {skill} ({mine}) isn't higher than "
                                    f"{other['given_name']}'s ({theirs}), so there's no gain.")
            else:
                rep.notes.append(f"{where}: {other['given_name']} {skill} {theirs} -> about {theirs + math.sqrt(mine - theirs):.0f}.")
    if code.startswith("n%") or code.startswith("n#"):
        if place_key(f.get("place")) in places and code.startswith("n%"):
            rep.warnings.append(f"{where}: the database already has co-ordinates for {places[place_key(f['place'])][0]}.")


ORDERS_NAME = re.compile(r"(?P<account>[a-z]+)_(?P<year>\d{4})-(?P<month>\d{2})$", re.I)


def parse_orders_file(path: str | Path) -> dict[str, list[dict]]:
    """A saved orders file -> rows for orders_sent (one per code) plus the plan notes as a source.
    Parsed on its own terms (no check against the current turn), so old orders always load."""
    path = Path(path)
    m = ORDERS_NAME.match(path.stem)
    if not m:
        raise ValueError(f"{path.name}: orders files are named like <account>_1547-04.txt")
    source_id = f"orders:{path.stem.lower()}"
    game_month = f"{m['year']}-{m['month']}"
    text = path.read_text(encoding="utf-8")
    notes = " ".join(l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#"))
    rows, family, persona, start = [], None, None, None
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        if tokens[0].startswith("/"):
            family = next((t.lstrip("*") for t in tokens[1:] if t.startswith("*")), None)
            continue
        if tokens[0].startswith("*") and len(tokens) > 1 and tokens[1].startswith("."):
            start, persona, tokens = tokens[0].lstrip("*"), int(tokens[1][1:]), tokens[2:]
        for seq, tok in enumerate(tokens, 1):
            o = match_code(tok)
            rows.append({"source_id": source_id, "game_month": game_month, "account": m["account"].upper(),
                         "family": family, "persona_id": persona, "start_location": start, "line_no": n,
                         "seq": seq, "code": tok, "order_group": GROUPS[o.group] if o else None,
                         "explanation": o.explanation if o else "unrecognized code"})
    source = {"source_id": source_id, "kind": "orders", "reliability": "my_orders", "file_name": path.name,
              "title": f"Orders sent for {game_month}", "publisher": m["account"].upper(),
              "account": m["account"].upper(), "player_family": family, "game_date": None,
              "real_date": None, "actions_used": len(rows), "issue": None, "notes": notes or None}
    return {"sources": [source], "orders_sent": rows}


def send_text(text: str) -> str:
    """The orders exactly as they should be transmitted: notes and blank lines removed."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if line.strip():
            out.append(" ".join(line.split()))
    return "\n".join(out) + "\n"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="rensim orders")
    ap.add_argument("file", help='orders file, or "-" for stdin')
    ap.add_argument("--send", action="store_true", help="print the text to transmit")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    if args.send:
        print(send_text(text), end="")
        return 0
    rep = check(text)
    print("\n".join(rep.lines))
    for title, items in (("ERRORS", rep.errors), ("WARNINGS", rep.warnings), ("NOTES", rep.notes)):
        if items:
            print(f"\n{title}")
            print("\n".join(f"  - {i}" for i in items))
    print("\n" + ("Not ready: fix the errors above." if rep.errors else "No errors found."))
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
