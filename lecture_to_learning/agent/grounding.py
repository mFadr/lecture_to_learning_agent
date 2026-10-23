"""Ověřování návaznosti na zdroj: existence stránek, dohledání citací a čísel."""

from __future__ import annotations

import re
import unicodedata

from ..models import DocumentExtraction


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[‐-―−]", "-", text)
    text = re.sub(r"[^\w\s%.,=+\-*/^()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def key(text: str) -> str:
    return re.sub(r"[^\w]+", " ", normalize(text)).strip()


def find_quote_pages(quote: str, page_texts: dict[int, str], min_len: int = 12) -> list[int]:
    """Vrátí stránky, na kterých se citace (normalizovaně) vyskytuje."""
    q = normalize(quote).strip(" .,\"'„“")
    if len(q) < min_len:
        return []
    hits = [p for p, t in page_texts.items() if q in normalize(t)]
    if hits:
        return hits
    # tolerance pro rozdělení řádků / drobné odchylky: shoda 80 % slov ve správném pořadí
    words = q.split()
    if len(words) < 4:
        return []
    out = []
    for p, t in page_texts.items():
        nt = normalize(t)
        pos, found = 0, 0
        for w in words:
            i = nt.find(w, pos)
            if i != -1:
                found += 1
                pos = i + len(w)
        if found / len(words) >= 0.8:
            out.append(p)
    return out


def find_term_pages(term: str, page_texts: dict[int, str]) -> list[int]:
    k = normalize(term)
    if len(k) < 3:
        return []
    return [p for p, t in page_texts.items() if k in normalize(t)]


def valid_pages(pages: list[int], allowed: set[int]) -> list[int]:
    return sorted({p for p in pages if p in allowed})


_NUM_RE = re.compile(r"(?<![\w.,])[-−]?\d+(?:[  ]\d{3})*(?:[.,]\d+)?")


def numbers_in_text(text: str) -> set[float]:
    out: set[float] = set()
    for m in _NUM_RE.finditer(text):
        s = m.group(0).replace("−", "-").replace(" ", "").replace(" ", "")
        candidates = {s.replace(",", ".")}
        if "," in s and "." not in s and re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):
            candidates.add(s.replace(",", ""))  # anglický oddělovač tisíců
        for c in candidates:
            try:
                out.add(float(c))
            except ValueError:
                pass
    return out


def number_in_source(value: float, source_numbers: set[float]) -> bool:
    for n in source_numbers:
        if abs(n - value) <= 1e-9 * max(1.0, abs(n)):
            return True
        # procenta zapsaná jako podíl (0,25 vs 25 %)
        if abs(n / 100 - value) < 1e-9 or abs(n - value / 100) < 1e-9:
            return True
    return False


def pages_text(extractions: dict[str, DocumentExtraction], doc_id: str, pages: list[int]) -> dict[int, str]:
    ex = extractions.get(doc_id)
    if not ex:
        return {}
    out = {}
    for p in pages:
        pg = ex.page(p)
        if pg:
            out[p] = pg.text
    return out
