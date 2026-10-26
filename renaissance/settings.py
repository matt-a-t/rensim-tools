"""Who you are in the game and where your data lives, read from rensim.toml.

The config file is RENSIM_CONFIG if set, otherwise the first rensim.toml in the current folder or
one of its parents (so the commands work from anywhere inside your data folder). Without one, the
current folder is used with the defaults below. Relative folders are taken from the config file's
folder.

Environment variables override the file: RENSIM_ACCOUNT, RENSIM_FAMILY, RENSIM_HOME,
RENSIM_DATA_DIR, RENSIM_ORDERS_DIR, RENSIM_ALLY_REFERENCE, and RENSIM_IMAP_USER for [fetch] user.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

DEFAULTS = {"account": "", "family": "", "home": "", "data_dir": "data", "orders_dir": "orders",
            "ally_reference": "player_publications/*master_city_data*.docx"}
FETCH_DEFAULTS = {"host": "imap.gmail.com", "user": "", "sender": "", "mailbox": "INBOX", "days": 45}


def find_config(start: Path | None = None) -> Path | None:
    """RENSIM_CONFIG, else the nearest rensim.toml at or above `start` (default: the current folder)."""
    if os.environ.get("RENSIM_CONFIG"):
        return Path(os.environ["RENSIM_CONFIG"]).resolve()
    here = (start or Path.cwd()).resolve()
    return next((d / "rensim.toml" for d in (here, *here.parents) if (d / "rensim.toml").is_file()), None)


def load(path: Path | None) -> dict:
    """DEFAULTS, then the config file (if any), then RENSIM_* environment variables."""
    values = dict(DEFAULTS)
    if path and path.exists():
        values.update(tomllib.loads(path.read_text(encoding="utf-8")))
    for key in DEFAULTS:
        values[key] = os.environ.get(f"RENSIM_{key.upper()}", values[key])
    return values


CONFIG = find_config()
BASE = CONFIG.parent if CONFIG else Path.cwd().resolve()
_values = load(CONFIG)
MY_ACCOUNT = _values["account"].upper()   # your GM account code; other accounts are allies' shared reports
MY_FAMILY = _values["family"].upper()
HOME = _values["home"]
DATA = (BASE / _values["data_dir"]).resolve()
RAW = DATA / "raw"
ORDERS_DIR = (BASE / _values["orders_dir"]).resolve()
ALLY_REFERENCE = _values["ally_reference"]   # glob under data/raw: an ally's compiled city data
QUALITY = _values.get("quality", {})         # known exceptions for renaissance.quality
ALLY = _values.get("ally", {})               # who compiled the ally reference: title, publisher, account, family, game_date
FETCH = {**FETCH_DEFAULTS, **_values.get("fetch", {})}   # the mailbox rensim fetch reads; the password is never here
FETCH["user"] = os.environ.get("RENSIM_IMAP_USER", FETCH["user"])
