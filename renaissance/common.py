"""Shared helpers: reading the old DOS-style files, dates, numbers, coordinates."""
from __future__ import annotations

import math
import re
from datetime import date, datetime

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}


def read_text(path) -> str:
    """Read a game file: Windows line endings, cp1252 text, Ctrl-Z (0x1A) padding at the end."""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("cp1252", errors="replace")
    text = text.replace("\x1a", "").replace("\r\n", "\n").replace("\r", "\n")
    return text


def parse_date(text: str) -> date:
    """'February 28, 1547' -> date(1547, 2, 28). Tolerates 'October 31 , 1545'."""
    return datetime.strptime(re.sub(r"\s*,\s*", ", ", " ".join(text.split())), "%B %d, %Y").date()


def to_int(text: str | None) -> int | None:
    """Game numbers come padded and oddly comma'd: '   ,349,402' or '  , 9,203'."""
    if text is None:
        return None
    digits = re.sub(r"[^\d-]", "", text)
    return int(digits) if digits else None


def coord(text: str) -> float:
    """Game coordinates are degrees.minutes: 'N55.57' = 55°57' = 55.95. West/South are negative."""
    text = text.strip()
    hemi, value = text[0].upper(), text[1:]
    deg_str, _, min_str = value.partition(".")
    degrees = int(deg_str) + (int(min_str.ljust(2, "0")[:2]) / 60 if min_str else 0)
    return round(-degrees if hemi in "SW" else degrees, 4)


def miles_between(lat1, lon1, lat2, lon2) -> float | None:
    """Straight-line (great-circle) distance in miles."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


def place_key(name: str | None) -> str | None:
    """Normalize a place name to a join key: 'pNompenh', 'Pnompenh', 'PNOMPENH' -> 'pnompenh';
    'Cape Verde' and 'CapeVerde' -> 'capeverde'."""
    if not name:
        return None
    return re.sub(r"[^a-z]", "", name.lower()) or None


def display_name(name: str) -> str:
    """Readable form of a place name: 'PEGU' -> 'Pegu', 'pNompenh' -> 'Pnompenh'."""
    name = name.strip()
    if name.isupper() or name.islower() or (len(name) > 1 and name[0].islower()):
        return name[:1].upper() + name[1:].lower()
    return name
