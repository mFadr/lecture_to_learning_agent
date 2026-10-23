"""Rozdělení dokumentu na části podle kontextového limitu modelu."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from ..models import DocumentExtraction, PageQuality
from ..security import DOC_CLOSE, DOC_OPEN, sanitize_document_text


@dataclass
class Chunk:
    doc_id: str
    doc_name: str
    pages: list[int]
    text: str  # text s vyznačenými stránkami, připravený pro prompt
    index: int = 0
    total: int = 0
    page_texts: dict[int, str] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:24]

    @property
    def label(self) -> str:
        return f"{self.doc_name}, s. {self.pages[0]}–{self.pages[-1]}" if len(self.pages) > 1 else \
            f"{self.doc_name}, s. {self.pages[0]}"


def page_header(doc_name: str, page: int) -> str:
    return f"[[{doc_name} | strana {page}]]"


def make_chunks(ex: DocumentExtraction, max_chars: int, page_range: range | None = None) -> list[Chunk]:
    """Seskupí po sobě jdoucí stránky do částí do velikosti `max_chars`.

    Příliš dlouhá stránka se rozdělí na více částí (se stejným číslem stránky).
    Stránky bez textu se vynechají (agent o nich jen informuje)."""
    max_chars = max(1500, max_chars)
    chunks: list[Chunk] = []
    cur_pages: list[int] = []
    cur_parts: list[str] = []
    cur_len = 0
    page_texts: dict[int, str] = {}

    def flush() -> None:
        nonlocal cur_pages, cur_parts, cur_len, page_texts
        if cur_pages:
            body = "\n\n".join(cur_parts)
            chunks.append(Chunk(ex.doc_id, ex.filename, cur_pages, f"{DOC_OPEN}\n{body}\n{DOC_CLOSE}",
                                page_texts=page_texts))
        cur_pages, cur_parts, cur_len, page_texts = [], [], 0, {}

    for pg in ex.pages:
        if page_range is not None and pg.number not in page_range:
            continue
        if pg.quality == PageQuality.NO_TEXT or not pg.text.strip():
            continue
        text = sanitize_document_text(pg.text)
        pieces = [text[i:i + max_chars - 200] for i in range(0, len(text), max_chars - 200)] or [""]
        for piece in pieces:
            entry = f"{page_header(ex.filename, pg.number)}\n{piece}"
            if cur_len + len(entry) > max_chars and cur_pages:
                flush()
            if pg.number not in cur_pages:
                cur_pages.append(pg.number)
            page_texts[pg.number] = page_texts.get(pg.number, "") + piece
            cur_parts.append(entry)
            cur_len += len(entry) + 2
    flush()
    for i, c in enumerate(chunks):
        c.index, c.total = i + 1, len(chunks)
    return chunks
