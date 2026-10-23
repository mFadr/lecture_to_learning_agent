from pathlib import Path

import pytest

from lecture_to_learning.agent.chunking import make_chunks
from lecture_to_learning.models import PageQuality
from lecture_to_learning.pdf.extractor import PdfError, extract_pdf, render_page_png


def test_extract_regular_pdf_pages_and_numbering(sample_pdf: Path):
    ex = extract_pdf(sample_pdf, "d1")
    assert ex.page_count == 3
    assert [p.number for p in ex.pages] == [1, 2, 3]
    assert "Poptávka je množství statku" in ex.pages[0].text
    assert "Rovnovážná cena" in ex.pages[1].text
    assert "elasticita" in ex.pages[2].text
    assert all(p.quality == PageQuality.OK for p in ex.pages)
    assert ex.title.startswith("Přednáška 3")
    headings = [b.text for b in ex.pages[0].blocks if b.kind == "heading"]
    assert "Přednáška 3: Nabídka, poptávka a tržní rovnováha" in headings  # zalomený nadpis sloučen
    assert any(b.kind == "bullet" for b in ex.pages[1].blocks)
    assert any(b.kind == "formula" and "Ed =" in b.text for b in ex.pages[2].blocks)
    assert len(ex.content_hash) == 64


def test_scanned_pdf_detected_without_text(scanned_pdf: Path):
    ex = extract_pdf(scanned_pdf, "d2")
    assert ex.pages[0].quality == PageQuality.NO_TEXT
    assert ex.pages[0].text == ""  # nic se nevymýšlí
    assert any("sken" in w for w in ex.warnings)
    assert make_chunks(ex, 5000) == []  # bez textu nevzniknou části pro LLM


def test_corrupted_pdf_raises_friendly_error(tmp_path: Path):
    bad = tmp_path / "poskozeny.pdf"
    bad.write_bytes(b"%PDF-1.4\n garbage without structure")
    with pytest.raises(PdfError):
        extract_pdf(bad, "x")


def test_missing_file(tmp_path: Path):
    with pytest.raises(PdfError):
        extract_pdf(tmp_path / "neexistuje.pdf", "x")


def test_chunking_respects_limits_and_marks_pages(sample_pdf: Path):
    ex = extract_pdf(sample_pdf, "d1")
    chunks = make_chunks(ex, 1500)
    assert len(chunks) >= 2
    assert all(len(c.text) <= 1500 + 100 for c in chunks)
    covered = sorted({p for c in chunks for p in c.pages})
    assert covered == [1, 2, 3]
    assert "[[" in chunks[0].text and "strana 1" in chunks[0].text
    only_two = make_chunks(ex, 20000, range(2, 3))
    assert [c.pages for c in only_two] == [[2]]


def test_render_page_png(sample_pdf: Path):
    png = render_page_png(sample_pdf, 2, zoom=0.5)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(PdfError):
        render_page_png(sample_pdf, 9)
