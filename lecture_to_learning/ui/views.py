"""Záložky hlavního okna."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from ..agent.orchestrator import STEP_LABELS
from ..export.exporters import export_visualization
from ..models import CardKind, ExampleStatus, PageQuality, StepStatus, Visualization, VizType, format_pages
from .common import CSS, SvgView, h, parse_src_link, src_link, summary_html, terms_html

if TYPE_CHECKING:
    from .main_window import MainWindow

STATUS_ICON = {StepStatus.DONE: "✔", StepStatus.PARTIAL: "◐", StepStatus.FAILED: "✖", StepStatus.SKIPPED: "–",
               StepStatus.CANCELLED: "⏹", StepStatus.RUNNING: "…", StepStatus.PENDING: "·"}
STATUS_CLASS = {StepStatus.DONE: "ok", StepStatus.PARTIAL: "warn", StepStatus.FAILED: "err",
                StepStatus.CANCELLED: "warn"}


class LinkBrowser(QTextBrowser):
    """QTextBrowser, který neotevírá externí odkazy a odkazy src:doc:page posílá oknu."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._clicked)

    def _clicked(self, url) -> None:
        target = parse_src_link(url.toString())
        if target:
            self.win.open_source(*target)


# ------------------------------------------------------------------ přehled


class OverviewView(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        self.browser = LinkBrowser(win)
        lay.addWidget(self.browser)

    def refresh(self) -> None:
        w = self.win
        if not w.project:
            self.browser.setHtml(CSS + "<h1>Lecture-to-Learning Agent</h1><p>Vytvořte projekt (<b>Nový projekt</b>) a "
                                 "přidejte přednáškové PDF – tlačítkem nebo přetažením souborů do okna.</p>"
                                 "<p>Poté spusťte <b>Analýzu</b>. Výsledky najdete v záložkách.</p>")
            return
        p, r = w.project, w.results
        out = [CSS, f"<h1>{h(p.name)}</h1>", f"<p><b>Model:</b> {h(w.router_description())}</p>"]
        out.append("<h2>Dokumenty</h2><table><tr><th>Soubor</th><th>Stran</th><th>Stav</th><th>Rozsah</th></tr>")
        for d in p.documents:
            rng = f"{d.page_from or 1}–{d.page_to or d.page_count}" if d.page_from or d.page_to else "vše"
            st = h(d.status.value) + (f" <span class='err'>{h(d.error)}</span>" if d.error else "")
            out.append(f"<tr><td>{h(d.filename)}</td><td>{d.page_count}</td><td>{st}</td><td>{rng}</td></tr>")
        out.append("</table>")
        if r.steps:
            out.append(f"<h2>Poslední běh</h2><p class='src'>{h(r.llm_label)} · {r.updated_at:%d.%m.%Y %H:%M}</p><table>")
            for s in r.steps:
                cls = STATUS_CLASS.get(s.status, "")
                out.append(f"<tr><td class='{cls}'>{STATUS_ICON[s.status]}</td><td>{h(STEP_LABELS.get(s.name, s.name))}</td>"
                           f"<td class='{cls}'>{h(s.message)}</td></tr>")
            out.append("</table>")
            if any(s.status in (StepStatus.FAILED, StepStatus.PARTIAL) for s in r.steps):
                out.append("<p class='warn'>Některé kroky nejsou kompletní – výstupy mohou být neúplné. Neúspěšné kroky "
                           "lze znovu spustit; hotové části se načtou z mezipaměti.</p>")
        n_viz = len([v for v in r.visualizations if v.valid])
        out.append("<h2>Výstupy</h2><ul>"
                   f"<li>Témata: {len(r.topics)}, pojmy: {len(r.terms)}, vzorce: {len(r.formulas)}</li>"
                   f"<li>Shrnutí: {len(r.summary.sections) if r.summary else 0} kapitol</li>"
                   f"<li>Flashcards: {len(r.flashcards)} · testové otázky: {len(r.quiz)}</li>"
                   f"<li>Nalezené příklady: {len(r.examples)} · platné vizualizace: {n_viz}</li></ul>")
        if r.issues:
            out.append("<h2>Kontrola kvality</h2><ul>")
            for i in r.issues:
                cls = {"error": "err", "warning": "warn"}.get(i.severity, "src")
                out.append(f"<li class='{cls}'><b>{h(i.area)}:</b> {h(i.message)}</li>")
            out.append("</ul>")
        self.browser.setHtml("".join(out))


# ----------------------------------------------------------------- dokument


class DocumentView(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.doc_combo = QComboBox()
        self.doc_combo.currentIndexChanged.connect(self._doc_changed)
        self.search = QLineEdit(placeholderText="Hledat v textu projektu…")
        self.search.returnPressed.connect(self._search)
        top.addWidget(QLabel("Dokument:"))
        top.addWidget(self.doc_combo, 1)
        top.addWidget(self.search, 1)
        lay.addLayout(top)
        self.info = QLabel(wordWrap=True)
        lay.addWidget(self.info)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.pages = QListWidget()
        self.pages.currentRowChanged.connect(self._page_changed)
        self.topics = QListWidget()
        self.topics.itemDoubleClicked.connect(self._topic_clicked)
        ll.addWidget(QLabel("Stránky:"))
        ll.addWidget(self.pages, 2)
        ll.addWidget(QLabel("Identifikovaná témata (dvojklik = zdroj):"))
        ll.addWidget(self.topics, 1)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.page_warn = QLabel(wordWrap=True)
        self.page_warn.setStyleSheet("color:#b45309")
        self.text = QPlainTextEdit(readOnly=True)
        show = QPushButton("Zobrazit stránku z PDF")
        show.clicked.connect(lambda: self._open_current())
        rl.addWidget(self.page_warn)
        rl.addWidget(self.text, 1)
        rl.addWidget(show)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([300, 700])
        lay.addWidget(split, 1)
        self._ex = None

    def refresh(self) -> None:
        cur = self.doc_combo.currentData()
        self.doc_combo.blockSignals(True)
        self.doc_combo.clear()
        for d in (self.win.project.documents if self.win.project else []):
            self.doc_combo.addItem(d.filename, d.id)
        idx = self.doc_combo.findData(cur)
        self.doc_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.doc_combo.blockSignals(False)
        self._doc_changed()

    def select_doc(self, doc_id: str) -> None:
        idx = self.doc_combo.findData(doc_id)
        if idx >= 0:
            self.doc_combo.setCurrentIndex(idx)

    def _doc_changed(self) -> None:
        self.pages.clear()
        self.topics.clear()
        self.text.clear()
        doc_id = self.doc_combo.currentData()
        w = self.win
        if not doc_id or not w.project:
            self.info.setText("Žádný dokument.")
            return
        ref = w.project.doc(doc_id)
        self._ex = w.extraction(doc_id)
        if not self._ex:
            self.info.setText(f"<b>{h(ref.filename)}</b> – {ref.page_count} stran – stav: {ref.status.value}. "
                              "Text zatím nebyl extrahován (spusťte zpracování)." +
                              (f"<br><span style='color:#b91c1c'>{h(ref.error)}</span>" if ref.error else ""))
            return
        ex = self._ex
        bad = [p for p in ex.pages if p.quality != PageQuality.OK]
        self.info.setText(f"<b>{h(ref.filename)}</b> – {ex.page_count} stran – stav: {ref.status.value} – "
                          f"nadpis: {h(ex.title)}" + "".join(f"<br><span style='color:#b45309'>⚠ {h(wn)}</span>"
                                                            for wn in ex.warnings))
        icons = {PageQuality.OK: "", PageQuality.LOW_TEXT: " ⚠ málo textu", PageQuality.NO_TEXT: " ✖ bez textu",
                 PageQuality.OCR: " (OCR)"}
        for p in ex.pages:
            it = QListWidgetItem(f"Strana {p.number}{icons[p.quality]}")
            if p.quality == PageQuality.NO_TEXT:
                it.setForeground(QColor("#b91c1c"))
            self.pages.addItem(it)
        for t in w.results.topics:
            if t.source.doc_id == doc_id:
                it = QListWidgetItem(f"{t.title}  (s. {format_pages(t.source.pages)})")
                it.setData(Qt.ItemDataRole.UserRole, t.source.pages[0] if t.source.pages else None)
                self.topics.addItem(it)
        if bad:
            self.info.setText(self.info.text() + f"<br>{len(bad)} stránek s problémem extrakce.")
        self.pages.setCurrentRow(0)

    def _page_changed(self, row: int) -> None:
        if not self._ex or row < 0:
            return
        p = self._ex.pages[row]
        self.page_warn.setText(" ".join(p.warnings))
        blocks = "\n".join(("# " if b.kind == "heading" else "• " if b.kind == "bullet" else
                            "ƒ " if b.kind == "formula" else "[tabulka]\n" if b.kind == "table" else "") + b.text
                           for b in p.blocks)
        self.text.setPlainText(blocks or p.text or "(bez textu)")

    def _open_current(self) -> None:
        if self._ex and self.pages.currentRow() >= 0:
            self.win.open_source(self.doc_combo.currentData(), self.pages.currentRow() + 1)

    def _topic_clicked(self, it: QListWidgetItem) -> None:
        page = it.data(Qt.ItemDataRole.UserRole)
        if page:
            self.pages.setCurrentRow(page - 1)

    def _search(self) -> None:
        w = self.win
        if not w.project:
            return
        hits = w.store.search(w.project.id, self.search.text())
        self.topics.clear()
        names = w.project.doc_names()
        if not hits:
            self.topics.addItem("Nic nenalezeno.")
        for hit in hits:
            it = QListWidgetItem(f"{names.get(hit['doc_id'], '?')} s. {hit['page']}: {hit['snip']}")
            it.setData(Qt.ItemDataRole.UserRole, hit["page"])
            it.setData(Qt.ItemDataRole.UserRole + 1, hit["doc_id"])
            self.topics.addItem(it)
        self.topics.itemDoubleClicked.disconnect()
        self.topics.itemDoubleClicked.connect(
            lambda it: w.open_source(it.data(Qt.ItemDataRole.UserRole + 1), it.data(Qt.ItemDataRole.UserRole))
            if it.data(Qt.ItemDataRole.UserRole + 1) else None)


# ----------------------------------------------------------- HTML záložky


class HtmlView(QWidget):
    def __init__(self, win: "MainWindow", builder):
        super().__init__()
        self.win, self.builder = win, builder
        lay = QVBoxLayout(self)
        self.browser = LinkBrowser(win)
        lay.addWidget(self.browser)

    def refresh(self) -> None:
        if self.win.project:
            pos = self.browser.verticalScrollBar().value()
            self.browser.setHtml(self.builder(self.win.project, self.win.results))
            self.browser.verticalScrollBar().setValue(pos)
        else:
            self.browser.clear()


def make_summary_view(win):
    return HtmlView(win, summary_html)


def make_terms_view(win):
    return HtmlView(win, terms_html)


# -------------------------------------------------------------- flashcards


class FlashcardsView(QWidget):
    COLS = ["Otázka", "Odpověď", "Typ", "Téma", "Zdroj"]

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        study, add, delete, src = (QPushButton("Režim učení"), QPushButton("Přidat kartičku"),
                                   QPushButton("Smazat vybrané"), QPushButton("Zdrojová stránka"))
        study.clicked.connect(self._study)
        add.clicked.connect(self._add)
        delete.clicked.connect(self._delete)
        src.clicked.connect(self._source)
        for b in (study, add, delete, src):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(QLabel("Úpravy otázek a odpovědí se ukládají automaticky."))
        lay.addLayout(bar)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table)

    def refresh(self) -> None:
        self.table.blockSignals(True)
        cards = self.win.results.flashcards
        self.table.setRowCount(len(cards))
        names = self.win.project.doc_names() if self.win.project else {}
        for i, c in enumerate(cards):
            vals = [c.question, c.answer, c.kind.value, c.topic,
                    f"{names.get(c.source.doc_id, '?')} s. {format_pages(c.source.pages) or '?'}"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j >= 2:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(i, j, it)
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)

    def _edited(self, it: QTableWidgetItem) -> None:
        if self.win.busy:
            QMessageBox.information(self, "Úpravy", "Během zpracování nelze výstupy upravovat.")
            self.refresh()
            return
        c = self.win.results.flashcards[it.row()]
        if it.column() == 0:
            c.question = it.text()
        elif it.column() == 1:
            c.answer = it.text()
        self.win.save_results()

    def _add(self) -> None:
        from ..models import Flashcard, SourceRef
        if self.win.busy or not self.win.project or not self.win.project.documents:
            return
        self.win.results.flashcards.append(Flashcard(question="Nová otázka", answer="Odpověď", kind=CardKind.APPLICATION,
                                                     source=SourceRef(doc_id=self.win.project.documents[0].id)))
        self.win.save_results()
        self.refresh()

    def _delete(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if self.win.busy or not rows:
            return
        for r in rows:
            del self.win.results.flashcards[r]
        self.win.save_results()
        self.refresh()

    def _source(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            c = self.win.results.flashcards[r]
            if c.source.pages:
                self.win.open_source(c.source.doc_id, c.source.pages[0])

    def _study(self) -> None:
        if self.win.results.flashcards:
            StudyDialog(self, self.win).exec()


class StudyDialog(QDialog):
    def __init__(self, parent: QWidget, win: "MainWindow"):
        super().__init__(parent)
        import random
        self.win = win
        self.cards = list(win.results.flashcards)
        random.shuffle(self.cards)
        self.i, self.known = 0, 0
        self.setWindowTitle("Režim učení")
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        self.counter = QLabel()
        self.q = QLabel(wordWrap=True, alignment=Qt.AlignmentFlag.AlignCenter)
        self.q.setStyleSheet("font-size:15pt;font-weight:600;padding:20px")
        self.a = QLabel(wordWrap=True, alignment=Qt.AlignmentFlag.AlignCenter)
        self.a.setStyleSheet("font-size:12pt;color:#1e3a8a;padding:12px")
        self.a.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row = QHBoxLayout()
        self.flip, self.ok, self.again = QPushButton("Ukázat odpověď"), QPushButton("Umím ✔"), QPushButton("Zopakovat ↻")
        self.flip.clicked.connect(self._flip)
        self.ok.clicked.connect(lambda: self._next(True))
        self.again.clicked.connect(lambda: self._next(False))
        for b in (self.flip, self.again, self.ok):
            row.addWidget(b)
        lay.addWidget(self.counter)
        lay.addWidget(self.q, 1)
        lay.addWidget(self.a, 1)
        lay.addLayout(row)
        self._show()

    def _show(self) -> None:
        if self.i >= len(self.cards):
            self.q.setText(f"Hotovo! Umíte {self.known} z {self.known + len(self.cards) - self.i or self.known} kartiček.")
            self.a.setText("")
            for b in (self.flip, self.ok, self.again):
                b.setEnabled(False)
            return
        c = self.cards[self.i]
        self.counter.setText(f"Kartička {self.i + 1} / {len(self.cards)} · {c.topic}")
        self.q.setText(c.question)
        self.a.setText("")
        self.ok.setEnabled(False)
        self.again.setEnabled(False)

    def _flip(self) -> None:
        c = self.cards[self.i]
        names = self.win.project.doc_names()
        self.a.setText(f"{c.answer}\n\n({names.get(c.source.doc_id, '')} s. {format_pages(c.source.pages) or '?'})")
        self.ok.setEnabled(True)
        self.again.setEnabled(True)

    def _next(self, known: bool) -> None:
        if known:
            self.known += 1
        else:
            self.cards.append(self.cards[self.i])
        self.i += 1
        self._show()


# -------------------------------------------------------------------- test


class QuizView(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QHBoxLayout(self)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        lay.addWidget(self.list, 1)
        right = QWidget()
        rl = QVBoxLayout(right)
        self.kind = QLabel()
        self.kind.setStyleSheet("color:#52606d")
        self.question = QLabel(wordWrap=True)
        self.question.setStyleSheet("font-size:12.5pt;font-weight:600")
        self.opts_box = QWidget()
        self.opts_lay = QVBoxLayout(self.opts_box)
        self.answer_edit = QPlainTextEdit(placeholderText="Vaše odpověď…")
        self.answer_edit.setMaximumHeight(110)
        btns = QHBoxLayout()
        self.check_btn, self.src_btn = QPushButton("Zkontrolovat / ukázat řešení"), QPushButton("Zdrojová stránka")
        self.check_btn.clicked.connect(self._check)
        self.src_btn.clicked.connect(self._source)
        btns.addWidget(self.check_btn)
        btns.addWidget(self.src_btn)
        btns.addStretch()
        self.feedback = QTextBrowser()
        self.score = QLabel()
        for wdg in (self.kind, self.question, self.opts_box, self.answer_edit):
            rl.addWidget(wdg)
        rl.addLayout(btns)
        rl.addWidget(self.feedback, 1)
        rl.addWidget(self.score)
        lay.addWidget(right, 2)
        self.group = QButtonGroup(self)
        self.answered: dict[str, bool] = {}

    def refresh(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.answered.clear()
        for i, q in enumerate(self.win.results.quiz, 1):
            self.list.addItem(f"{i}. {q.question[:70]}")
        self.list.blockSignals(False)
        if self.win.results.quiz:
            self.list.setCurrentRow(0)
        else:
            self.question.setText("Test zatím nebyl vytvořen.")
            self.kind.clear()
        self._score()

    def _current(self):
        r = self.list.currentRow()
        return self.win.results.quiz[r] if 0 <= r < len(self.win.results.quiz) else None

    def _show(self, row: int) -> None:
        q = self._current()
        if not q:
            return
        self.kind.setText(q.kind.label + (f" · {q.topic}" if q.topic else ""))
        self.question.setText(q.question)
        for b in self.group.buttons():
            self.group.removeButton(b)
            b.deleteLater()
        for i, o in enumerate(q.options):
            rb = QRadioButton(f"{'ABCDEF'[i]}) {o}")
            rb.setProperty("opt", o)
            self.group.addButton(rb)
            self.opts_lay.addWidget(rb)
        self.answer_edit.setVisible(not q.options)
        self.answer_edit.clear()
        self.feedback.clear()

    def _check(self) -> None:
        q = self._current()
        if not q:
            return
        names = self.win.project.doc_names()
        src = f"{h(names.get(q.source.doc_id, ''))} s. {h(format_pages(q.source.pages) or '?')}"
        if q.options:
            chosen = self.group.checkedButton()
            if not chosen:
                self.feedback.setHtml("Vyberte odpověď.")
                return
            ok = chosen.property("opt") == q.correct_answer
            self.answered[q.id] = ok
            verdict = "<span style='color:#15803d'>✔ Správně</span>" if ok else \
                f"<span style='color:#b91c1c'>✖ Špatně</span> – správně: <b>{h(q.correct_answer)}</b>"
        else:
            verdict = f"Vzorová odpověď: <b>{h(q.correct_answer)}</b> <br><i>Porovnejte se svou odpovědí.</i>"
        self.feedback.setHtml(f"{verdict}<p>{h(q.explanation)}</p><p style='color:#52606d'>Zdroj: {src}</p>")
        self._score()

    def _score(self) -> None:
        n = len(self.answered)
        self.score.setText(f"Skóre (uzavřené otázky): {sum(self.answered.values())} / {n}" if n else "")

    def _source(self) -> None:
        q = self._current()
        if q and q.source.pages:
            self.win.open_source(q.source.doc_id, q.source.pages[0])


# --------------------------------------------------------- tematická mapa


class MapView(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.which = QComboBox()
        self.which.addItems(["Tematická mapa", "Vztahy mezi pojmy"])
        self.which.currentIndexChanged.connect(self.refresh)
        fit = QPushButton("Přizpůsobit oknu")
        exp = QPushButton("Exportovat SVG/PNG…")
        fit.clicked.connect(lambda: self.view.fit())
        exp.clicked.connect(self._export)
        bar.addWidget(self.which)
        bar.addWidget(fit)
        bar.addWidget(exp)
        bar.addStretch()
        bar.addWidget(QLabel("Ctrl + kolečko = zoom"))
        lay.addLayout(bar)
        self.view = SvgView()
        self.msg = QLabel()
        lay.addWidget(self.msg)
        lay.addWidget(self.view, 1)

    def _rel(self) -> str | None:
        r = self.win.results
        return r.topic_map_svg if self.which.currentIndex() == 0 else r.relation_map_svg

    def refresh(self) -> None:
        rel = self._rel()
        if self.win.project and rel and self.view.load_svg(self.win.pdir / rel):
            self.msg.setText("")
        else:
            self.view.clear()
            self.msg.setText("Mapa zatím nebyla vytvořena (krok Tematická mapa).")

    def _export(self) -> None:
        rel = self._rel()
        if not rel:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export mapy", "tematicka_mapa.svg", "SVG (*.svg);;PNG (*.png)")
        if path:
            v = Visualization(spec={"viz_type": "mindmap", "title": "mapa"}, source={"doc_id": ""}, svg_path=rel)
            try:
                export_visualization(v, self.win.pdir, Path(path))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Export", str(exc))


# -------------------------------------------------------- katalog příkladů


class ExamplesView(QWidget):
    COLS = ["Vybrat", "Příklad", "Téma", "Stránky", "Doporučená vizualizace", "Stav"]

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Konkrétní příklady, procesy a situace nalezené v PDF. Zaškrtněte ty, které chcete "
                             "vizualizovat, případně změňte typ vizualizace.", wordWrap=True))
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.currentCellChanged.connect(lambda r, *_: self._detail(r))
        self.table.itemChanged.connect(self._check_changed)
        self.detail = LinkBrowser(win)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([400, 220])
        lay.addWidget(split, 1)
        bar = QHBoxLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem("Doporučený typ", None)
        for vt in VizType:
            self.type_combo.addItem(vt.label, vt.value)
        run_sel = QPushButton("Vizualizovat zaškrtnuté")
        run_one = QPushButton("Vizualizovat aktuální zvoleným typem")
        src = QPushButton("Zdrojová stránka")
        run_sel.clicked.connect(self._run_selected)
        run_one.clicked.connect(self._run_current)
        src.clicked.connect(self._source)
        bar.addWidget(run_sel)
        bar.addWidget(QLabel("Typ:"))
        bar.addWidget(self.type_combo)
        bar.addWidget(run_one)
        bar.addWidget(src)
        bar.addStretch()
        lay.addLayout(bar)

    def refresh(self) -> None:
        self.table.blockSignals(True)
        ex = self.win.results.examples
        self.table.setRowCount(len(ex))
        status_txt = {ExampleStatus.FOUND: "nalezen", ExampleStatus.SELECTED: "vybrán",
                      ExampleStatus.GENERATED: "✔ vizualizováno", ExampleStatus.FAILED: "✖ selhalo"}
        for i, e in enumerate(ex):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if e.status == ExampleStatus.SELECTED else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, chk)
            for j, v in enumerate([e.title, e.topic, format_pages(e.source.pages) or "?", e.recommended_viz.label,
                                   status_txt[e.status]], 1):
                it = QTableWidgetItem(v)
                if j == 5 and e.status == ExampleStatus.FAILED:
                    it.setForeground(QColor("#b91c1c"))
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.blockSignals(False)
        self._detail(self.table.currentRow())

    def _check_changed(self, it: QTableWidgetItem) -> None:
        if it.column() != 0:
            return
        e = self.win.results.examples[it.row()]
        if it.checkState() == Qt.CheckState.Checked:
            e.status = ExampleStatus.SELECTED
        elif e.status == ExampleStatus.SELECTED:
            e.status = ExampleStatus.FOUND
        if not self.win.busy:
            self.win.save_results()

    def _detail(self, row: int) -> None:
        ex = self.win.results.examples
        if not 0 <= row < len(ex) or not self.win.project:
            self.detail.setHtml(CSS + "<p>Vyberte příklad.</p>" if ex else CSS + "<p>Zatím nebyly nalezeny žádné "
                                "příklady (spusťte analýzu).</p>")
            return
        e = ex[row]
        rows = [("Popis podle zdroje", e.description), ("Vysvětlovaný koncept", e.concept),
                ("Objekty / proměnné / aktéři", ", ".join(e.actors)), ("Vztahy", "; ".join(e.relations)),
                ("Číselná data", e.numeric_data or "– (zdroj neuvádí čísla)"),
                ("Doporučená vizualizace", f"{e.recommended_viz.label} – {e.viz_reason}"), ("Poznámka", e.status_note)]
        self.detail.setHtml(CSS + f"<h2>{h(e.title)}</h2>{src_link(self.win.project, e.source, 'Zdroj: ')}<table>" +
                            "".join(f"<tr><th>{h(k)}</th><td>{h(v)}</td></tr>" for k, v in rows if v) + "</table>")

    def _run_selected(self) -> None:
        ids = [e.id for e in self.win.results.examples if e.status == ExampleStatus.SELECTED]
        if not ids:
            QMessageBox.information(self, "Vizualizace", "Zaškrtněte alespoň jeden příklad.")
            return
        vt = self.type_combo.currentData()
        self.win.run_visualizations(ids, VizType(vt) if vt else None)

    def _run_current(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        vt = self.type_combo.currentData()
        self.win.run_visualizations([self.win.results.examples[r].id], VizType(vt) if vt else None)

    def _source(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            e = self.win.results.examples[r]
            if e.source.pages:
                self.win.open_source(e.source.doc_id, e.source.pages[0])


# --------------------------------------------------------- galerie vizualizací


class GalleryView(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        filt = QHBoxLayout()
        self.f_topic, self.f_type, self.f_doc = QComboBox(), QComboBox(), QComboBox()
        self.latest = QCheckBox("Jen poslední verze")
        self.latest.setChecked(True)
        self.show_invalid = QCheckBox("Zobrazit i neplatné")
        for wdg, label in ((self.f_topic, "Téma:"), (self.f_type, "Typ:"), (self.f_doc, "Dokument:")):
            filt.addWidget(QLabel(label))
            filt.addWidget(wdg, 1)
            wdg.currentIndexChanged.connect(self._fill)
        self.latest.toggled.connect(self._fill)
        self.show_invalid.toggled.connect(self._fill)
        filt.addWidget(self.latest)
        filt.addWidget(self.show_invalid)
        lay.addLayout(filt)
        split = QSplitter()
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(180, 130))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setWordWrap(True)
        self.list.setGridSize(QSize(200, 175))
        self.list.currentItemChanged.connect(lambda *_: self._show())
        right = QWidget()
        rl = QVBoxLayout(right)
        self.view = SvgView()
        self.info = LinkBrowser(win)
        self.info.setMaximumHeight(200)
        btns = QHBoxLayout()
        self.type_combo = QComboBox()
        for vt in VizType:
            self.type_combo.addItem(vt.label, vt.value)
        b_src, b_new, b_png, b_svg = (QPushButton("Zdrojová stránka"), QPushButton("Nová verze zvoleným typem"),
                                      QPushButton("Export PNG"), QPushButton("Export SVG"))
        b_src.clicked.connect(self._source)
        b_new.clicked.connect(self._regenerate)
        b_png.clicked.connect(lambda: self._export("png"))
        b_svg.clicked.connect(lambda: self._export("svg"))
        btns.addWidget(b_src)
        btns.addWidget(self.type_combo)
        btns.addWidget(b_new)
        btns.addStretch()
        btns.addWidget(b_png)
        btns.addWidget(b_svg)
        rl.addWidget(self.view, 1)
        rl.addWidget(self.info)
        rl.addLayout(btns)
        split.addWidget(self.list)
        split.addWidget(right)
        split.setSizes([300, 800])
        lay.addWidget(split, 1)

    def refresh(self) -> None:
        r = self.win.results
        for combo, values in ((self.f_topic, sorted({v.topic for v in r.visualizations if v.topic})),
                              (self.f_type, sorted({v.spec.viz_type.value for v in r.visualizations})),
                              (self.f_doc, sorted({v.source.doc_id for v in r.visualizations}))):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("vše", None)
            names = self.win.project.doc_names() if self.win.project else {}
            for v in values:
                label = names.get(v, v) if combo is self.f_doc else (VizType(v).label if combo is self.f_type else v)
                combo.addItem(label, v)
            idx = combo.findData(cur)
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)
        self._fill()

    def _items(self) -> list[Visualization]:
        vs = self.win.results.visualizations
        if not self.show_invalid.isChecked():
            vs = [v for v in vs if v.valid]
        if self.latest.isChecked():
            best: dict[str, Visualization] = {}
            for v in vs:
                k = v.example_id or v.id
                if k not in best or v.version > best[k].version:
                    best[k] = v
            vs = list(best.values())
        t, ty, d = self.f_topic.currentData(), self.f_type.currentData(), self.f_doc.currentData()
        return [v for v in vs if (not t or v.topic == t) and (not ty or v.spec.viz_type.value == ty)
                and (not d or v.source.doc_id == d)]

    def _fill(self) -> None:
        self.list.clear()
        for v in self._items():
            label = f"{v.spec.title}\n(v{v.version})" + ("" if v.valid else "\n✖ neplatná")
            it = QListWidgetItem(label)
            if v.png_path and (self.win.pdir / v.png_path).exists():
                it.setIcon(QIcon(QPixmap(str(self.win.pdir / v.png_path))))
            if not v.valid:
                it.setForeground(QColor("#b91c1c"))
            it.setData(Qt.ItemDataRole.UserRole, v.id)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.view.clear()
            self.info.setHtml(CSS + "<p>Žádné vizualizace. Vyberte příklady v záložce <b>Příklady</b>.</p>")

    def _current(self) -> Visualization | None:
        it = self.list.currentItem()
        if not it:
            return None
        vid = it.data(Qt.ItemDataRole.UserRole)
        return next((v for v in self.win.results.visualizations if v.id == vid), None)

    def _show(self) -> None:
        v = self._current()
        if not v or not self.win.project:
            return
        if v.valid and v.svg_path:
            self.view.load_svg(self.win.pdir / v.svg_path)
        else:
            self.view.clear()
        idx = self.type_combo.findData(v.spec.viz_type.value)
        self.type_combo.setCurrentIndex(idx)
        warn = "".join(f"<li class='{'warn' if v.valid else 'err'}'>{h(w)}</li>" for w in v.validation_warnings)
        notes = "".join(f"<li>{h(n)}</li>" for n in v.spec.notes)
        concept = next((e.concept for e in self.win.results.examples if e.id == v.example_id), "")
        self.info.setHtml(CSS + f"<h3>{h(v.spec.title)} <span class='src'>({h(v.spec.viz_type.label)}, verze {v.version})"
                          f"</span></h3><p>{h(v.spec.explanation)}</p>"
                          + (f"<p><b>Koncept:</b> {h(concept)} · <b>Téma:</b> {h(v.topic)}</p>" if concept or v.topic else "")
                          + src_link(self.win.project, v.source, "Zdroj: ")
                          + (f"<ul>{notes}</ul>" if notes else "") + (f"<ul>{warn}</ul>" if warn else ""))

    def _source(self) -> None:
        v = self._current()
        if v and v.source.pages:
            self.win.open_source(v.source.doc_id, v.source.pages[0])

    def _regenerate(self) -> None:
        v = self._current()
        if v and v.example_id:
            self.win.run_visualizations([v.example_id], VizType(self.type_combo.currentData()))

    def _export(self, kind: str) -> None:
        v = self._current()
        if not v or not v.valid:
            return
        from ..security import safe_filename
        path, _ = QFileDialog.getSaveFileName(self, "Export vizualizace", safe_filename(f"{v.spec.title}.{kind}"),
                                              f"{kind.upper()} (*.{kind})")
        if path:
            try:
                export_visualization(v, self.win.pdir, Path(path))
                self.win.statusBar().showMessage(f"Uloženo: {path}", 5000)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Export", f"Export se nezdařil: {exc}")
