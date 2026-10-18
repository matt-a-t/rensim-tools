"""Turn player publications (PDFs, scans, phone photos, Word .doc files) into plain text.

The text is cached in data/extracted/<same relative path>.txt and committed, so:
  - OCR runs once, not on every pipeline run (CI doesn't need Tesseract or LibreOffice);
  - you can review exactly what the machine read, as a diff, before claims are drawn from it.

Strategy per file type:
  .pdf        text layer via pdftotext; pages with (almost) no text are scanned images -> OCR
  .jpg/.png   OCR, after detecting and fixing rotation (phone photos are often sideways)
  .doc/.docx  LibreOffice headless conversion to text

    rensim extract [--force]

Needs: poppler-utils (pdftotext, pdftoppm), tesseract-ocr, libreoffice, and
pip install "rensim-tools[extract]" (Pillow).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from .settings import DATA, RAW

EXTRACTED = DATA / "extracted"
SOURCE_DIRS = ["player_publications", "reference"]
MIN_CHARS_PER_PAGE = 80          # below this a PDF page is treated as a scanned image
OCR_DPI = 300


def run(cmd: list[str], **kw) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


# ---- images ------------------------------------------------------------------
def detect_rotation(image_path: Path) -> int:
    """Degrees Tesseract says the page is rotated (0/90/180/270), from its orientation detector."""
    try:
        osd = run(["tesseract", str(image_path), "-", "--psm", "0"])
    except subprocess.CalledProcessError:
        return 0          # too little text to tell; leave as is
    m = re.search(r"Rotate:\s*(\d+)", osd)
    return int(m.group(1)) if m else 0


def ocr_image(image_path: Path) -> str:
    """OCR one image, upright-ing it first. Returns the text plus a header noting what was done."""
    with tempfile.TemporaryDirectory() as tmp:
        img = ImageOps.exif_transpose(Image.open(image_path)).convert("L")
        prepared = Path(tmp) / "page.png"
        img.save(prepared)
        rotate = detect_rotation(prepared)
        if rotate:
            # Tesseract reports the clockwise rotation needed; PIL rotates counter-clockwise.
            img = img.rotate(-rotate, expand=True)
            img.save(prepared)
        text = run(["tesseract", str(prepared), "-", "--psm", "3"])
    return f"[ocr: tesseract, rotated {rotate} degrees]\n{text}"


# ---- PDFs --------------------------------------------------------------------
def pdf_page_count(pdf: Path) -> int:
    return int(re.search(r"Pages:\s+(\d+)", run(["pdfinfo", str(pdf)])).group(1))


def extract_pdf(pdf: Path) -> str:
    pages = []
    for n in range(1, pdf_page_count(pdf) + 1):
        text = run(["pdftotext", "-layout", "-f", str(n), "-l", str(n), str(pdf), "-"])
        if len(text.strip()) >= MIN_CHARS_PER_PAGE:
            pages.append(f"=== page {n} [text layer] ===\n{text.rstrip()}")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            run(["pdftoppm", "-r", str(OCR_DPI), "-f", str(n), "-l", str(n), "-png", str(pdf), f"{tmp}/p"])
            image = next(Path(tmp).glob("p*.png"))
            pages.append(f"=== page {n} {ocr_image(image).rstrip()}")
    return "\n\n".join(pages) + "\n"


# ---- Word documents -------------------------------------------------------------
def extract_word(doc: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        run(["soffice", "--headless", "--convert-to", "txt:Text", "--outdir", tmp, str(doc)])
        text = next(Path(tmp).glob("*.txt")).read_text(encoding="utf-8", errors="replace")
    return "[converted: libreoffice]\n" + text.replace("\r\n", "\n").lstrip("﻿")


EXTRACTORS = {".pdf": extract_pdf, ".jpg": lambda p: ocr_image(p) + "\n", ".jpeg": lambda p: ocr_image(p) + "\n",
              ".png": lambda p: ocr_image(p) + "\n", ".doc": extract_word, ".docx": extract_word}


def extracted_path(raw_file: Path) -> Path:
    return EXTRACTED / raw_file.relative_to(RAW).with_suffix(raw_file.suffix + ".txt")


def extract_all(force: bool = False) -> list[Path]:
    """Extract every source that has no cached text yet (or all of them with force=True)."""
    missing = [t for t in ("pdftotext", "tesseract", "soffice") if not shutil.which(t)]
    done = []
    for folder in SOURCE_DIRS:
        for raw_file in sorted((RAW / folder).glob("*")):
            fn = EXTRACTORS.get(raw_file.suffix.lower())
            out = extracted_path(raw_file)
            if not fn or (out.exists() and not force):
                continue
            if missing:
                raise SystemExit(f"Need {', '.join(missing)} to extract {raw_file.name}")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(fn(raw_file), encoding="utf-8")
            done.append(out)
            print(f"  extracted {raw_file.relative_to(DATA.parent)} -> {out.relative_to(DATA.parent)}")
    return done


def main(argv=None):
    import sys
    extract_all(force="--force" in (sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    main()
