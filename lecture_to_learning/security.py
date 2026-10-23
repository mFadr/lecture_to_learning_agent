"""Bezpečnostní pomůcky: validace cest, názvy souborů, ošetření textu z PDF."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}


class PathSecurityError(ValueError):
    pass


def ensure_within(base: Path, candidate: Path) -> Path:
    """Vrátí resolved cestu, pokud leží uvnitř `base`, jinak vyhodí výjimku."""
    base_r = base.resolve()
    cand_r = (base_r / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if cand_r != base_r and base_r not in cand_r.parents:
        raise PathSecurityError(f"Cesta {candidate} je mimo povolenou složku.")
    return cand_r


def safe_filename(name: str, max_len: int = 80, default: str = "soubor", has_ext: bool = True) -> str:
    """Bezpečný název souboru pro Windows; zachová českou diakritiku.

    `has_ext=False` – název nemá příponu (tečky uvnitř, např. „A vs. B“, se nepovažují za příponu)."""
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = default
    stem, dot, ext = name.rpartition(".")
    if not dot or not has_ext or not re.fullmatch(r"[A-Za-z0-9]{1,10}", ext):
        stem, ext = name, ""
    if stem.upper() in _WIN_RESERVED:
        stem = f"_{stem}"
    ext = ext[:10]
    room = max_len - (len(ext) + 1 if ext else 0)
    stem = stem[:max(1, room)].rstrip(" .") or default
    return f"{stem}.{ext}" if ext else stem


def unique_path(path: Path) -> Path:
    """Vyhne se kolizi názvů přidáním čísla."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 10_000):
        cand = path.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(path)


def is_pdf_file(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        with path.open("rb") as fh:
            head = fh.read(1024)
        return b"%PDF" in head
    except OSError:
        return False


# Značky, kterými v promptu ohraničujeme text dokumentu. Z textu PDF je odstraníme,
# aby dokument nemohl "uzavřít" datový blok a vložit vlastní instrukce.
DOC_OPEN = "<<<DOKUMENT>>>"
DOC_CLOSE = "<<<KONEC_DOKUMENTU>>>"


def sanitize_document_text(text: str) -> str:
    text = text.replace(DOC_OPEN, "").replace(DOC_CLOSE, "")
    text = re.sub(r"<<<[A-Z_ ]{3,40}>>>", "", text)
    # řídicí znaky kromě nového řádku a tabulátoru
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
