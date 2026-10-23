"""Sdílené prvky UI: pracovní vlákno, HTML s odkazy na zdroj, prohlížeč SVG a zdrojové stránky."""

from __future__ import annotations

import logging
import threading
from html import escape
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRectF, Qt, QThread, Signal, Slot
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from ..models import Origin, Project, SourceRef, StudyResults, SummaryPoint, format_pages
from ..pdf.extractor import PdfError, render_page_png

log = logging.getLogger(__name__)


def h(s: Any) -> str:
    return escape(str(s), quote=True)


# ---------------------------------------------------------------- vlákna


class TaskWorker(QObject):
    """Spouští dlouhou úlohu mimo UI vlákno. fn(cancel_event, progress_cb) -> výsledek."""

    progress = Signal(str, float, str)
    finished = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[threading.Event, Callable[[str, float, str], None]], Any]):
        super().__init__()
        self.fn = fn
        self.cancel = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(self.cancel, lambda s, f, m: self.progress.emit(s, f, m))
        except Exception as exc:  # chyba se zobrazí uživateli srozumitelně
            log.exception("Úloha selhala")
            self.failed.emit(exc)
            return
        self.finished.emit(result)


def start_worker(parent: QObject, worker: TaskWorker) -> QThread:
    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread


# ------------------------------------------------------------- HTML views

CSS = """<style>
body{font-family:'Segoe UI',Arial;font-size:10.5pt;color:#1f2933}
h1{color:#1e3a8a;font-size:17pt}h2{color:#1e3a8a;font-size:14pt;margin-top:18px}h3{font-size:11pt;margin-bottom:2px}
.src{color:#52606d;font-size:9pt}.ext{background:#fef3c7;color:#92400e;font-size:8.5pt}
.miss{background:#fef2f2;color:#7f1d1d}.ok{color:#15803d}.warn{color:#b45309}.err{color:#b91c1c}
a{color:#2563eb;text-decoration:none}td,th{padding:3px 8px}th{background:#eef2ff;text-align:left}
</style>"""


def src_link(project: Project, src: SourceRef, prefix: str = "") -> str:
    name = project.doc_names().get(src.doc_id, "?")
    if not src.pages:
        return f"<span class='src'>{prefix}{h(name)}, stránka neurčena</span>"
    links = ", ".join(f"<a href='src:{src.doc_id}:{p}'>{p}</a>" for p in sorted(set(src.pages)))
    return f"<span class='src'>{prefix}{h(name)}, s. {links}</span>"


def _point(project: Project, doc_id: str, p: SummaryPoint) -> str:
    pages = f" <span class='src'>({', '.join(f'<a href=\"src:{doc_id}:{n}\">s. {n}</a>' for n in p.pages)})</span>" \
        if p.pages else ""
    ext = " <span class='ext'>&nbsp;externí vysvětlení – není v PDF&nbsp;</span>" if p.origin == Origin.MODEL else ""
    return f"<li>{h(p.text)}{pages}{ext}</li>"


def summary_html(project: Project, r: StudyResults) -> str:
    if not r.summary or not r.summary.sections:
        return CSS + "<p>Shrnutí zatím nebylo vytvořeno. Spusťte analýzu s výstupem <b>Strukturované shrnutí</b>.</p>"
    out = [CSS, f"<h1>{h(project.name)}</h1><p class='src'>{h(r.summary.detail.label)} · klikněte na číslo stránky "
                "pro zobrazení zdroje · <span class='ext'>&nbsp;externí vysvětlení&nbsp;</span> = doplněk modelu, "
                "není obsahem přednášky</p>"]
    for s in r.summary.sections:
        out.append(f"<h2>{h(s.title)}</h2>{src_link(project, s.source, 'Zdroj: ')}")
        if s.overview:
            out.append(f"<p>{h(s.overview)}</p>")
        for title, pts in (("Vysvětlení", s.explanation), ("Klíčové definice", s.key_definitions),
                           ("Vzorce", s.formulas), ("Příklady z přednášky", s.examples),
                           ("Souvislosti", s.connections), ("Časté chyby a nejasnosti", s.pitfalls)):
            if pts:
                out.append(f"<h3>{title}</h3><ul>" + "".join(_point(project, s.source.doc_id, p) for p in pts) + "</ul>")
        if s.missing_info:
            out.append("<p class='miss'><b>Ve zdroji chybí / nejasné:</b> " + h("; ".join(s.missing_info)) + "</p>")
    return "".join(out)


def terms_html(project: Project, r: StudyResults) -> str:
    if not r.terms and not r.formulas:
        return CSS + "<p>Pojmy zatím nebyly extrahovány.</p>"
    out = [CSS, "<h1>Pojmy a definice</h1>"]
    for t in r.terms:
        q = (f"<br><i>„{h(t.source.quote)}“</i> <span class='ok'>✔ doslovná citace ověřena v PDF</span>"
             if t.source.quote_verified and t.source.quote else " <span class='src'>(parafráze)</span>")
        out.append(f"<p><b>{h(t.name)}</b> — {h(t.definition)}{q}"
                   + (f"<br><i>Jednoduše:</i> {h(t.simple_explanation)}" if t.simple_explanation else "")
                   + (f"<br><i>Příklad:</i> {h(t.example)}" if t.example else "")
                   + (f"<br><i>Související:</i> {h(', '.join(t.related))}" if t.related else "")
                   + f"<br>{src_link(project, t.source)}</p>")
    if r.formulas:
        out.append("<h2>Vzorce</h2>")
        for f in r.formulas:
            vars_ = "".join(f"<li><b>{h(k)}</b>: {h(v)}</li>" for k, v in f.variables.items())
            out.append(f"<p><code style='font-size:12pt'>{h(f.expression)}</code> — {h(f.meaning)}"
                       f"{'<ul>' + vars_ + '</ul>' if vars_ else '<br>'}{src_link(project, f.source)}</p>")
    if r.cross_document_topics:
        out.append("<h2>Témata společná pro více PDF</h2><ul>")
        for c in r.cross_document_topics:
            docs = "; ".join(f"{h(d['doc'])} s. {h(format_pages(d['pages']))}" for d in c["documents"])
            out.append(f"<li><b>{h(c['title'])}</b>: {docs} – terminologie se může lišit, porovnejte definice.</li>")
        out.append("</ul>")
    return "".join(out)


def parse_src_link(url: str) -> tuple[str, int] | None:
    parts = url.split(":")
    if len(parts) == 3 and parts[0] == "src" and parts[2].isdigit():
        return parts[1], int(parts[2])
    return None


# ----------------------------------------------------------- SVG viewer


class SvgView(QGraphicsView):
    """Zobrazení SVG se zoomem (kolečko myši + Ctrl) a přizpůsobením oknu."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._renderer: QSvgRenderer | None = None
        self._pix = None

    def clear(self) -> None:
        self.scene().clear()
        self._renderer = None

    def load_svg(self, path: Path) -> bool:
        self.scene().clear()
        if not path.exists():
            return False
        self._renderer = QSvgRenderer(str(path))
        if not self._renderer.isValid():
            return False
        item = QGraphicsSvgItem()
        item.setSharedRenderer(self._renderer)
        self.scene().addItem(item)
        self.scene().setSceneRect(QRectF(item.boundingRect()))
        self.fit()
        return True

    def load_png(self, path: Path) -> bool:
        self.scene().clear()
        pm = QPixmap(str(path))
        if pm.isNull():
            return False
        self.scene().addPixmap(pm)
        self.scene().setSceneRect(QRectF(pm.rect()))
        self.fit()
        return True

    def fit(self) -> None:
        self.resetTransform()
        r = self.scene().sceneRect()
        if r.width() > self.viewport().width() or r.height() > self.viewport().height():
            self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            f = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
        else:
            super().wheelEvent(event)


# ------------------------------------------------------- zdrojová stránka


class SourcePageDialog(QDialog):
    """Zobrazí stránku původního PDF (obrázek) a její extrahovaný text."""

    def __init__(self, parent: QWidget, pdf_path: Path, doc_name: str, page: int, page_count: int,
                 text_for_page: Callable[[int], str]):
        super().__init__(parent)
        self.pdf_path, self.doc_name, self.page, self.page_count = pdf_path, doc_name, page, page_count
        self.text_for_page = text_for_page
        self.setWindowTitle("Zdrojová stránka")
        self.resize(1100, 820)
        lay = QVBoxLayout(self)
        nav = QHBoxLayout()
        self.prev_btn, self.next_btn = QPushButton("◀ Předchozí"), QPushButton("Další ▶")
        self.title = QLabel()
        self.title.setStyleSheet("font-weight:600;font-size:11pt")
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.title, 1, Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)
        split = QSplitter()
        self.image = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(self.image)
        scroll.setWidgetResizable(True)
        self.text = QPlainTextEdit(readOnly=True)
        split.addWidget(scroll)
        split.addWidget(self.text)
        split.setSizes([650, 400])
        lay.addWidget(split, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.prev_btn.clicked.connect(lambda: self.show_page(self.page - 1))
        self.next_btn.clicked.connect(lambda: self.show_page(self.page + 1))
        self.show_page(page)

    def show_page(self, page: int) -> None:
        if not 1 <= page <= self.page_count:
            return
        self.page = page
        self.title.setText(f"{self.doc_name} – strana {page} / {self.page_count}")
        self.prev_btn.setEnabled(page > 1)
        self.next_btn.setEnabled(page < self.page_count)
        try:
            pm = QPixmap()
            pm.loadFromData(render_page_png(self.pdf_path, page, zoom=1.4))
            self.image.setPixmap(pm)
        except (PdfError, OSError) as exc:
            self.image.setText(f"Stránku nelze zobrazit: {exc}\n(Původní PDF bylo možná přesunuto.)")
        self.text.setPlainText(self.text_for_page(page) or "(stránka nemá extrahovaný text)")
