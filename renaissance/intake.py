"""Take in whatever a player has for the game and file it in the game folder.

A GM turn result or newsletter is checked and saved by `add_file` (address removed), the same as
`rensim add`. An ally's compiled city data (.docx named like ALLY_REFERENCE) goes where the loader
looks for it. Anything else (a player newsletter, a proclamation, a letter or message from
another player) is saved as a player publication: the original under
data/raw/player_publications/, and its text under data/extracted/, where the claims checks look
for it, ready for Claude to draw claims from.

Text pasted into a chat comes in through `intake_text`; files the player drops into the game
folder's inbox/ through `intake_inbox` (text, Word .docx, PDFs with a text layer). A file that
was filed is moved to inbox/done/; one that wasn't stays, with the reason in the result.
"""
from __future__ import annotations

import contextlib
import fnmatch
import io
import re
import shutil
import tempfile
from pathlib import Path

from .add import Refused, add_file, shown
from .claims import EXTRACTED
from .common import detect_kind
from .settings import ALLY_REFERENCE, BASE, RAW

INBOX = BASE / "inbox"
DONE = INBOX / "done"
PUBLICATIONS = RAW / "player_publications"
TEXT_SUFFIXES = {"", ".txt", ".text", ".md"}
MIN_PDF_CHARS = 200   # less than this from a whole PDF is a scan with no text layer


def slug(name: str) -> str:
    """A file name safe on every system, from whatever the player called it."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:60] or "pasted"


def game_bytes(text: str) -> bytes:
    """Pasted text as the game's own files are stored (cp1252), starting at ACCOUNT CODE: when a
    turn result was pasted with an email's text above it."""
    text = text.lstrip("﻿")
    m = re.search(r"^ACCOUNT CODE:", text, re.M)
    if m:
        text = text[m.start():]
    return text.encode("cp1252", errors="replace")


def add_gm_file(path: Path, kind: str) -> dict:
    """add_file, with its warnings returned instead of printed."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            dest, written = add_file(path, RAW)
    except Refused as e:
        return {"kind": kind, "status": "refused", "why": str(e)}
    warnings = [l.strip().removeprefix("warning: ") for l in out.getvalue().splitlines() if l.strip()]
    return {"kind": kind, "status": "saved" if written else "already saved", "file": shown(dest), "warnings": warnings}


def save_publication(text: str, name: str, original: Path | None = None) -> dict:
    """The original (or the text) under data/raw/player_publications/ and its text under
    data/extracted/. Returns the `file` claims refer to it by."""
    suffix = original.suffix.lower() if original else ".txt"
    stem = slug(Path(name).stem)
    for n in range(1, 1000):   # one stem per publication, since its claims file is named after it
        name = stem if n == 1 else f"{stem}_{n}"
        taken = sorted(PUBLICATIONS.glob(f"{name}.*"))
        file = f"player_publications/{taken[0].name if taken else name + suffix}"
        extracted = EXTRACTED / (file + ".txt")
        if not taken:
            break
        if extracted.exists() and extracted.read_text(encoding="utf-8") == text:
            return {"kind": "publication", "status": "already saved", "file": file}
    (RAW / file).parent.mkdir(parents=True, exist_ok=True)
    if original:
        shutil.copyfile(original, RAW / file)
    else:
        (RAW / file).write_text(text, encoding="utf-8")
    extracted.parent.mkdir(parents=True, exist_ok=True)
    extracted.write_text(text, encoding="utf-8")
    return {"kind": "publication", "status": "saved", "file": file}


def intake_text(text: str, name: str | None = None) -> dict:
    """Pasted text: a GM file if it is one, else a player publication."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pasted.txt"
        path.write_bytes(game_bytes(text))
        kind = detect_kind(path)
        if kind:
            return add_gm_file(path, kind)
    first_line = next((l for l in text.splitlines() if l.strip()), "")
    return save_publication(text, name or first_line or "pasted")


def docx_text(path: Path) -> str:
    """A Word document's paragraphs and table cells, in order."""
    from docx import Document
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(parts).strip() + "\n"


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    return "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages).strip() + "\n"


def intake_file(path: Path) -> dict:
    """One file from the inbox, filed (or why not)."""
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES or path.name.upper().startswith("NEWSL"):
        kind = detect_kind(path)
        if kind:
            return add_gm_file(path, kind)
        return save_publication(path.read_bytes().decode("utf-8", errors="replace"), path.name, path)
    if suffix == ".docx" and fnmatch.fnmatch(f"player_publications/{path.name}", ALLY_REFERENCE):
        from .parse_reference import parse_reference
        try:
            parse_reference(path)
        except Exception as e:
            return {"kind": "ally_reference", "status": "refused", "why": f"couldn't read it as city data: {e}"}
        PUBLICATIONS.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, PUBLICATIONS / path.name)
        return {"kind": "ally_reference", "status": "saved", "file": f"player_publications/{path.name}"}
    if suffix == ".docx":
        return save_publication(docx_text(path), path.name, path)
    if suffix == ".pdf":
        text = pdf_text(path)
        if len(text.strip()) < MIN_PDF_CHARS:
            return {"kind": "publication", "status": "refused",
                    "why": "it's a scan with no text in it: attach it to the chat so Claude can read it"}
        return save_publication(text, path.name, path)
    return {"kind": None, "status": "refused",
            "why": f"{suffix or 'this'} files can't be read here: attach it to the chat instead"}


def intake_inbox() -> list[dict]:
    """Every file in inbox/ (not in done/), filed; filed ones are moved to inbox/done/."""
    results = []
    for path in sorted(p for p in INBOX.glob("*") if p.is_file() and not p.name.startswith(".")):
        try:
            result = intake_file(path)
        except Exception as e:   # a damaged file shouldn't stop the rest
            result = {"kind": None, "status": "refused", "why": f"couldn't read it: {e}"}
        result = {"inbox_file": path.name, **result}
        if result["status"] != "refused":
            DONE.mkdir(parents=True, exist_ok=True)
            dest = DONE / path.name
            n = 1
            while dest.exists():
                n += 1
                dest = DONE / f"{path.stem}_{n}{path.suffix}"
            shutil.move(path, dest)
        results.append(result)
    return results

