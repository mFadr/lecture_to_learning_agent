"""PDF Processing Engine – extrakce textu a struktury po stránkách (PyMuPDF)."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pymupdf

from ..models import Block, DocumentExtraction, Page, PageQuality

log = logging.getLogger(__name__)

EXTRACTOR_VERSION = "2"
MIN_CHARS_OK = 40  # méně znaků na stránce = podezřele málo textu

_BULLET_RE = re.compile(r"^\s*([•▪◦‣\-–*·●○■□]|\d+[.)]|[a-z][.)])\s+")
_MATH_CHARS = set("=∑∫√≤≥≠≈±×÷∞∂∆ΔαβγδεθλμπσφωΩ^")


class PdfError(Exception):
    """Srozumitelná chyba při práci s PDF."""


@dataclass
class OcrConfig:
    enabled: bool = False
    language: str = "ces+eng"
    tesseract_path: str = ""


def file_hash(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while data := fh.read(chunk):
            h.update(data)
    return h.hexdigest()


def find_tessdata(cfg: OcrConfig) -> str | None:
    """Vrátí cestu k tessdata, pokud je Tesseract nainstalován."""
    candidates: list[Path] = []
    if cfg.tesseract_path:
        p = Path(cfg.tesseract_path)
        candidates += [p / "tessdata", p.parent / "tessdata", p]
    exe = shutil.which("tesseract")
    if exe:
        candidates.append(Path(exe).parent / "tessdata")
    candidates += [Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
                   Path(r"C:\Program Files (x86)\Tesseract-OCR\tessdata")]
    for c in candidates:
        if c.is_dir() and any(c.glob("*.traineddata")):
            return str(c)
    return None


def open_pdf(path: Path) -> pymupdf.Document:
    if not path.exists():
        raise PdfError(f"Soubor {path.name} neexistuje.")
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise PdfError(f"Soubor {path.name} nelze otevřít jako PDF – může být poškozený ({exc}).") from exc
    if not doc.is_pdf:
        doc.close()
        raise PdfError(f"Soubor {path.name} není PDF.")
    if doc.needs_pass:
        doc.close()
        raise PdfError(f"Soubor {path.name} je chráněn heslem.")
    if doc.page_count == 0:
        doc.close()
        raise PdfError(f"Soubor {path.name} neobsahuje žádné stránky.")
    return doc


def page_count(path: Path) -> int:
    with open_pdf(path) as doc:
        return doc.page_count


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u00ad", "")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _looks_like_formula(text: str) -> bool:
    if len(text) > 160:
        return False
    hits = sum(1 for ch in text if ch in _MATH_CHARS)
    return hits >= 2 or bool(re.search(r"\w+\s*=\s*[\w(]", text) and len(text) < 80)


def _blocks_from_dict(d: dict) -> list[Block]:
    """Rozpozná nadpisy (dle velikosti/tučnosti písma), odrážky a vzorce."""
    lines: list[tuple[str, float, bool]] = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            spans = [s for s in ln.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = _clean("".join(s["text"] for s in spans))
            if not text:
                continue
            size = max(s["size"] for s in spans)
            bold = all((s.get("flags", 0) & 16) or "bold" in s.get("font", "").lower() for s in spans)
            lines.append((text, size, bold))
    if not lines:
        return []
    body = statistics.median(sz for _, sz, _ in lines)
    blocks: list[Block] = []
    for text, size, bold in lines:
        if size >= body * 1.25 and len(text) < 140:
            level = 1 if size >= body * 1.6 else 2
            prev = blocks[-1] if blocks else None
            if prev and prev.kind == "heading" and prev.level == level and len(prev.text) + len(text) < 200:
                prev.text += " " + text  # nadpis zalomený na více řádků
            else:
                blocks.append(Block(kind="heading", text=text, level=level))
        elif bold and len(text) < 90 and not text.endswith((".", ",")):
            blocks.append(Block(kind="heading", text=text, level=3))
        elif _BULLET_RE.match(text):
            blocks.append(Block(kind="bullet", text=text))
        elif _looks_like_formula(text):
            blocks.append(Block(kind="formula", text=text))
        elif blocks and blocks[-1].kind == "paragraph" and not blocks[-1].text.endswith((".", ":", "?", "!")):
            blocks[-1].text += " " + text
        else:
            blocks.append(Block(kind="paragraph", text=text))
    return blocks


def _extract_tables(page: pymupdf.Page) -> list[Block]:
    try:
        tabs = page.find_tables()
    except Exception as exc:  # detekce tabulek je best-effort
        log.debug("find_tables selhalo: %s", exc)
        return []
    out: list[Block] = []
    for t in getattr(tabs, "tables", []):
        try:
            rows = t.extract()
        except Exception:
            continue
        rows = [[_clean(c or "") for c in r] for r in rows if any(c for c in r)]
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
            out.append(Block(kind="table", text="\n".join(" | ".join(r) for r in rows)))
    return out


def extract_pdf(path: Path, doc_id: str, *, ocr: OcrConfig | None = None,
                progress: Callable[[int, int], None] | None = None,
                cancelled: Callable[[], bool] | None = None) -> DocumentExtraction:
    """Extrahuje celé PDF po stránkách. Nikdy nevymýšlí text nečitelných stránek."""
    ocr = ocr or OcrConfig()
    try:
        content_hash = file_hash(path)
    except OSError as exc:
        raise PdfError(f"Soubor {path.name} nelze přečíst ({exc.strerror or exc}).") from exc
    tessdata = find_tessdata(ocr) if ocr.enabled else None
    warnings: list[str] = []
    if ocr.enabled and not tessdata:
        warnings.append("OCR je zapnuté, ale Tesseract OCR nebyl nalezen – skenované stránky zůstanou bez textu.")

    pages: list[Page] = []
    with open_pdf(path) as doc:
        title = _clean((doc.metadata or {}).get("title") or "")
        n = doc.page_count
        for i in range(n):
            if cancelled and cancelled():
                raise InterruptedError("Extrakce byla zrušena.")
            pg = doc[i]
            page_warn: list[str] = []
            try:
                raw = pg.get_text("text", sort=True)
                d = pg.get_text("dict", sort=True)
            except Exception as exc:
                raw, d = "", {}
                page_warn.append(f"Text stránky nelze přečíst: {exc}")
            text = _clean(raw)
            text = re.sub(r"\n{3,}", "\n\n", text)
            blocks = _blocks_from_dict(d) if d else []
            has_images = bool(pg.get_images())
            quality = PageQuality.OK
            if len(text) == 0:
                quality = PageQuality.NO_TEXT
            elif len(text) < MIN_CHARS_OK:
                quality = PageQuality.LOW_TEXT
            elif _garbage_ratio(text) > 0.3:
                quality = PageQuality.LOW_TEXT
                page_warn.append("Text obsahuje mnoho nečitelných znaků (možná chybné kódování písma).")

            if quality != PageQuality.OK and tessdata:
                ocr_text = _ocr_page(pg, ocr.language, tessdata)
                if ocr_text and len(ocr_text) > len(text):
                    text, quality = ocr_text, PageQuality.OCR
                    blocks = [Block(kind="paragraph", text=p) for p in ocr_text.split("\n\n") if p.strip()]
                    page_warn.append("Text získán pomocí OCR – může obsahovat chyby rozpoznání.")
            if quality == PageQuality.NO_TEXT:
                page_warn.append("Stránka nemá textovou vrstvu" + (" (obsahuje obrázky – pravděpodobně sken)."
                                                                 if has_images else " (prázdná stránka)."))
            elif quality == PageQuality.LOW_TEXT and not page_warn:
                page_warn.append("Stránka obsahuje velmi málo textu.")

            tables = _extract_tables(pg) if quality in (PageQuality.OK,) else []
            blocks.extend(tables)
            pages.append(Page(number=i + 1, text=text, blocks=blocks, quality=quality,
                              has_images=has_images, table_count=len(tables), warnings=page_warn))
            if progress:
                progress(i + 1, n)

    empty = [p.number for p in pages if p.quality == PageQuality.NO_TEXT]
    if empty and len(empty) == len(pages):
        warnings.append("Dokument nemá žádnou textovou vrstvu. Jde pravděpodobně o sken – zapněte OCR "
                        "(Tesseract) v Nastavení, jinak nelze obsah analyzovat.")
    elif empty:
        warnings.append(f"{len(empty)} stránek bez textu: {', '.join(map(str, empty[:20]))}"
                        + ("…" if len(empty) > 20 else ""))
    if not title:
        title = next((b.text for p in pages for b in p.blocks if b.kind == "heading"), path.stem)
    return DocumentExtraction(doc_id=doc_id, filename=path.name, content_hash=content_hash,
                              page_count=len(pages), title=title[:200], pages=pages,
                              warnings=warnings, extractor_version=EXTRACTOR_VERSION)


def _garbage_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(1 for ch in text if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\n\t"))
    return bad / len(text)


def _ocr_page(pg: pymupdf.Page, language: str, tessdata: str) -> str:
    try:
        tp = pg.get_textpage_ocr(language=language, dpi=300, full=True, tessdata=tessdata)
        return _clean(pg.get_text("text", textpage=tp, sort=True))
    except Exception as exc:
        log.warning("OCR stránky %s selhalo: %s", pg.number + 1, exc)
        return ""


def render_page_png(path: Path, page_number: int, zoom: float = 1.5) -> bytes:
    """Vykreslí stránku jako PNG (pro náhled zdrojové stránky)."""
    with open_pdf(path) as doc:
        if not 1 <= page_number <= doc.page_count:
            raise PdfError(f"Stránka {page_number} v dokumentu neexistuje.")
        pix = doc[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png")
