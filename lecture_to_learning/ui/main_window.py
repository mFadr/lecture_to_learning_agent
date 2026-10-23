"""Hlavní okno aplikace."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QSplitter, QTabWidget, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import APP_DISPLAY_NAME, __version__
from ..agent.orchestrator import STEP_LABELS, ConsentRequired, Orchestrator, RunOptions
from ..config import LLMMode, Settings, save_settings
from ..export.exporters import export_all
from ..llm.factory import ProviderRouter
from ..models import DocStatus, DocumentExtraction, Project, StudyResults, VizType
from ..pdf.extractor import PdfError
from ..storage import ProjectStore, StorageError
from .common import SourcePageDialog, TaskWorker, start_worker
from .dialogs import AnalysisDialog, PageRangeDialog, SettingsDialog, ask_cloud_consent, open_folder
from .views import (DocumentView, ExamplesView, FlashcardsView, GalleryView, MapView, OverviewView, QuizView,
                    make_summary_view, make_terms_view)

log = logging.getLogger(__name__)

STATUS_TEXT = {DocStatus.NEW: "nový", DocStatus.EXTRACTED: "extrahován", DocStatus.ANALYZED: "analyzován",
               DocStatus.ERROR: "chyba"}


class MainWindow(QMainWindow):
    def __init__(self, store: ProjectStore, settings: Settings):
        super().__init__()
        self.store, self.settings = store, settings
        self.project: Project | None = None
        self.results = StudyResults()
        self.busy = False
        self._worker: TaskWorker | None = None
        self._thread = None
        self._extractions: dict[str, DocumentExtraction] = {}
        self.setWindowTitle(f"{APP_DISPLAY_NAME} {__version__}")
        self.resize(1400, 880)
        self.setAcceptDrops(True)
        self._build_toolbar()
        self._build_ui()
        self._build_statusbar()
        self.reload_projects()

    # ---------------------------------------------------------------- UI
    def _build_toolbar(self) -> None:
        tb = QToolBar("Hlavní")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)

        def act(text: str, slot, shortcut: str | None = None) -> QAction:
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            return a
        self.a_new = act("➕ Nový projekt", self.new_project, "Ctrl+N")
        self.a_add = act("📄 Přidat PDF", self.add_pdfs, "Ctrl+O")
        tb.addSeparator()
        self.a_run = act("▶ Spustit analýzu", self.run_analysis, "F5")
        self.a_export = act("💾 Exportovat", self.export, "Ctrl+E")
        tb.addSeparator()
        self.a_settings = act("⚙ Nastavení", self.open_settings)
        self.a_folder = act("📂 Složka projektu", lambda: self.project and open_folder(self.pdir))
        tb.addSeparator()
        self.mode_label = QLabel()
        self.mode_label.setStyleSheet("padding:0 10px")
        tb.addWidget(self.mode_label)
        self._update_mode_label()

    def _build_ui(self) -> None:
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("<b>Studijní projekty</b>"))
        self.projects = QListWidget()
        self.projects.currentItemChanged.connect(self._project_selected)
        self.projects.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.projects.customContextMenuRequested.connect(self._project_menu)
        ll.addWidget(self.projects, 1)
        ll.addWidget(QLabel("<b>PDF v projektu</b> (přetáhněte soubory do okna)"))
        self.docs = QTreeWidget()
        self.docs.setHeaderLabels(["Soubor", "Stran", "Stav"])
        self.docs.setRootIsDecorated(False)
        self.docs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.docs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.docs.customContextMenuRequested.connect(self._doc_menu)
        self.docs.itemDoubleClicked.connect(self._doc_open)
        ll.addWidget(self.docs, 1)
        row = QHBoxLayout()
        add, rem = QPushButton("Přidat PDF"), QPushButton("Odebrat")
        add.clicked.connect(self.add_pdfs)
        rem.clicked.connect(self.remove_docs)
        row.addWidget(add)
        row.addWidget(rem)
        ll.addLayout(row)

        self.tabs = QTabWidget()
        self.overview = OverviewView(self)
        self.document_view = DocumentView(self)
        self.summary_view = make_summary_view(self)
        self.terms_view = make_terms_view(self)
        self.cards_view = FlashcardsView(self)
        self.quiz_view = QuizView(self)
        self.map_view = MapView(self)
        self.examples_view = ExamplesView(self)
        self.gallery_view = GalleryView(self)
        for wdg, title in ((self.overview, "Přehled"), (self.document_view, "Dokument"),
                           (self.summary_view, "Shrnutí"), (self.terms_view, "Pojmy a definice"),
                           (self.cards_view, "Flashcards"), (self.quiz_view, "Test"),
                           (self.map_view, "Tematická mapa"), (self.examples_view, "Příklady"),
                           (self.gallery_view, "Galerie vizualizací")):
            self.tabs.addTab(wdg, title)
        self.tabs.currentChanged.connect(lambda _: self._refresh_current())
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setSizes([330, 1070])
        self.setCentralWidget(split)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        self.cancel_btn = QPushButton("Zrušit")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_task)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.cancel_btn)

    def _update_mode_label(self) -> None:
        m = self.settings.mode
        txt = {LLMMode.LOCAL: f"🔒 Lokální: {self.settings.ollama.model}",
               LLMMode.CLOUD: f"☁ Cloud: {self.settings.cloud.model} – data opouštějí zařízení",
               LLMMode.HYBRID: "◐ Hybridní – části dat jdou do cloudu"}[m]
        self.mode_label.setText(txt)
        self.mode_label.setStyleSheet("padding:0 10px;" + ("color:#15803d" if m == LLMMode.LOCAL else "color:#b45309"))

    # ------------------------------------------------------------ stav
    @property
    def pdir(self) -> Path:
        assert self.project
        return self.store.project_dir(self.project.id)

    def router_description(self) -> str:
        try:
            return ProviderRouter(self.settings).describe()
        except Exception as exc:
            return f"nenastaven ({exc})"

    def extraction(self, doc_id: str) -> DocumentExtraction | None:
        if doc_id not in self._extractions and self.project:
            ref = self.project.doc(doc_id)
            ex = self.store.load_extraction(self.project.id, ref) if ref else None
            if ex:
                self._extractions[doc_id] = ex
        return self._extractions.get(doc_id)

    def save_results(self) -> None:
        if self.project:
            try:
                self.store.save_results(self.project.id, self.results)
            except StorageError as exc:
                QMessageBox.critical(self, "Ukládání", str(exc))

    def reload_projects(self, select: str | None = None) -> None:
        cur = select or (self.project.id if self.project else None)
        self.projects.blockSignals(True)
        self.projects.clear()
        for p in self.store.list_projects():
            it = QListWidgetItem(f"{p.name}\n{len(p.documents)} PDF · {p.updated_at.astimezone():%d.%m.%Y %H:%M}")
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            self.projects.addItem(it)
            if p.id == cur:
                self.projects.setCurrentItem(it)
        self.projects.blockSignals(False)
        if self.projects.currentItem() is None and self.projects.count():
            self.projects.setCurrentRow(0)
        self._project_selected()

    def _project_selected(self, *_) -> None:
        it = self.projects.currentItem()
        if not it:
            self.project, self.results = None, StudyResults()
        else:
            try:
                self.project = self.store.load_project(it.data(Qt.ItemDataRole.UserRole))
                self.results = self.store.load_results(self.project.id)
            except (StorageError, ValueError) as exc:
                QMessageBox.critical(self, "Projekt", f"Projekt nelze načíst: {exc}")
                self.project, self.results = None, StudyResults()
        self._extractions.clear()
        self.refresh_all()

    def refresh_docs(self) -> None:
        self.docs.clear()
        if not self.project:
            return
        for d in self.project.documents:
            rng = f" [{d.page_from or 1}–{d.page_to or d.page_count}]" if d.page_from or d.page_to else ""
            it = QTreeWidgetItem([d.filename + rng, str(d.page_count), STATUS_TEXT[d.status]])
            it.setData(0, Qt.ItemDataRole.UserRole, d.id)
            it.setToolTip(0, d.error or d.path)
            if d.status == DocStatus.ERROR:
                it.setForeground(2, Qt.GlobalColor.red)
            self.docs.addTopLevelItem(it)
        self.docs.resizeColumnToContents(0)

    def refresh_all(self) -> None:
        self.refresh_docs()
        for a in (self.a_add, self.a_run, self.a_export, self.a_folder):
            a.setEnabled(self.project is not None and not (self.busy and a is not self.a_folder))
        self.a_new.setEnabled(not self.busy)
        self.a_settings.setEnabled(not self.busy)
        self._dirty = set(range(self.tabs.count()))
        self._refresh_current()

    def _refresh_current(self) -> None:
        i = self.tabs.currentIndex()
        if i in getattr(self, "_dirty", set()):
            self._dirty.discard(i)
            self.tabs.widget(i).refresh()

    # --------------------------------------------------------- projekty
    def new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "Nový projekt", "Název studijního projektu (např. předmět):")
        if ok and name.strip():
            p = self.store.create_project(name, copy_sources=self.settings.copy_sources_default)
            self.reload_projects(p.id)

    def _project_menu(self, pos) -> None:
        it = self.projects.itemAt(pos)
        if not it or self.busy:
            return
        m = QMenu(self)
        ren, dele = m.addAction("Přejmenovat"), m.addAction("Smazat projekt…")
        a = m.exec(self.projects.mapToGlobal(pos))
        if a == ren and self.project:
            name, ok = QInputDialog.getText(self, "Přejmenovat", "Nový název:", text=self.project.name)
            if ok and name.strip():
                self.project.name = name.strip()
                self.store.save_project(self.project)
                self.reload_projects()
        elif a == dele and self.project:
            if QMessageBox.question(self, "Smazat projekt",
                                    f"Smazat projekt „{self.project.name}“ včetně všech vytvořených výstupů?\n"
                                    "Originální PDF soubory mimo projekt zůstanou zachovány.") == QMessageBox.StandardButton.Yes:
                self.store.delete_project(self.project.id)
                self.project = None
                self.reload_projects()

    # -------------------------------------------------------- dokumenty
    def add_pdfs(self, paths: list[str] | None = None) -> None:
        if not self.project:
            QMessageBox.information(self, "Projekt", "Nejprve vytvořte nebo vyberte projekt.")
            return
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, "Vyberte přednášková PDF", "", "PDF (*.pdf)")
        errors = []
        for p in paths or []:
            try:
                self.store.add_document(self.project, Path(p))
            except (PdfError, OSError) as exc:
                errors.append(f"{Path(p).name}: {exc}")
        if errors:
            QMessageBox.warning(self, "Import PDF", "\n".join(errors))
        self.reload_projects()

    def remove_docs(self) -> None:
        sel = [it.data(0, Qt.ItemDataRole.UserRole) for it in self.docs.selectedItems()]
        if not sel or not self.project or self.busy:
            return
        if QMessageBox.question(self, "Odebrat", f"Odebrat {len(sel)} dokument(ů) z projektu? Originální soubory se "
                                                 "nesmažou.") != QMessageBox.StandardButton.Yes:
            return
        for doc_id in sel:
            self.store.remove_document(self.project, doc_id)
        self.reload_projects()

    def _doc_menu(self, pos) -> None:
        it = self.docs.itemAt(pos)
        if not it or not self.project or self.busy:
            return
        doc_id = it.data(0, Qt.ItemDataRole.UserRole)
        ref = self.project.doc(doc_id)
        m = QMenu(self)
        a_show, a_range, a_redo, a_rem = (m.addAction("Zobrazit dokument"), m.addAction("Rozsah stránek…"),
                                          m.addAction("Znovu zpracovat (extrakce + analýza)"), m.addAction("Odebrat"))
        a = m.exec(self.docs.viewport().mapToGlobal(pos))
        if a == a_show:
            self._doc_open(it)
        elif a == a_range:
            dlg = PageRangeDialog(self, ref.page_count, ref.page_from, ref.page_to)
            if dlg.exec():
                a_, b_ = dlg.values()
                ref.page_from, ref.page_to = (None, None) if (a_, b_) == (1, ref.page_count) else (a_, b_)
                self.store.save_project(self.project)
                self.refresh_docs()
        elif a == a_redo:
            self._start_run(RunOptions(steps=["extract", "analyze", "topic_map", "validate"], doc_ids=[doc_id], force=True))
        elif a == a_rem:
            self.remove_docs()

    def _doc_open(self, it, *_) -> None:
        self.tabs.setCurrentWidget(self.document_view)
        self.document_view.select_doc(it.data(0, Qt.ItemDataRole.UserRole))

    def open_source(self, doc_id: str, page: int) -> None:
        if not self.project:
            return
        ref = self.project.doc(doc_id)
        if not ref:
            QMessageBox.warning(self, "Zdroj", "Dokument už v projektu není.")
            return
        ex = self.extraction(doc_id)

        def text(p: int) -> str:
            pg = ex.page(p) if ex else None
            return pg.text if pg else ""
        SourcePageDialog(self, Path(ref.path), ref.filename, page, ref.page_count, text).exec()

    # drag & drop
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(".pdf") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(".pdf")]
        if paths:
            self.add_pdfs(paths)

    # ---------------------------------------------------------- nastavení
    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec():
            self.settings = dlg.settings
            save_settings(self.settings)
            self._update_mode_label()
            self.refresh_all()

    # ----------------------------------------------------------- úlohy
    def _ensure_consent(self) -> bool:
        if self.settings.mode == LLMMode.LOCAL or self.settings.cloud_consent:
            return True
        if ask_cloud_consent(self):
            self.settings.cloud_consent = True
            save_settings(self.settings)
            return True
        return False

    def run_analysis(self) -> None:
        if not self.project:
            return
        if not self.project.documents:
            QMessageBox.information(self, "Analýza", "Projekt neobsahuje žádné PDF.")
            return
        dlg = AnalysisDialog(self, self.project, self.settings, self.router_description())
        if dlg.exec():
            opts = dlg.options()
            if not opts.steps or not opts.doc_ids:
                return
            self.settings.detail_level = opts.detail.value
            save_settings(self.settings)
            self._start_run(opts)

    def run_visualizations(self, example_ids: list[str], viz_type: VizType | None) -> None:
        if self.busy or not self.project:
            return
        self.save_results()  # uložit výběr příkladů
        overrides = {i: viz_type for i in example_ids} if viz_type else {}
        self._start_run(RunOptions(steps=["visualize", "validate"], example_ids=example_ids, viz_overrides=overrides))

    def _start_run(self, opts: RunOptions) -> None:
        if self.busy or not self.project:
            return
        if not self._ensure_consent():
            return
        project_id = self.project.id
        store, settings = self.store, self.settings.model_copy(deep=True)

        def task(cancel, progress):
            project = store.load_project(project_id)
            orch = Orchestrator(store, project, settings, ProviderRouter(settings), cancel=cancel, progress=progress)
            return orch.run(opts)
        self._run_task(task, f"Zpracování: {', '.join(STEP_LABELS[s] for s in opts.steps)}")

    def _run_task(self, fn, title: str) -> None:
        self.busy = True
        self.refresh_all()
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.statusBar().showMessage(title)
        self._worker = TaskWorker(fn)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread = start_worker(self, self._worker)

    def _on_progress(self, step: str, frac: float, msg: str) -> None:
        self.progress.setFormat(f"{STEP_LABELS.get(step, step)} %p%")
        self.progress.setValue(int(frac * 100))
        self.statusBar().showMessage(msg)

    def _task_done(self) -> None:
        self.busy = False
        self.progress.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._worker = None

    def _on_finished(self, results) -> None:
        self._task_done()
        failed = [s for s in getattr(results, "steps", []) if s.status.value in ("failed", "partial", "cancelled")]
        self.reload_projects()
        self.tabs.setCurrentWidget(self.overview)
        if failed:
            self.statusBar().showMessage("Zpracování skončilo s problémy – viz Přehled.", 10000)
            msgs = "\n".join(f"• {STEP_LABELS.get(s.name, s.name)}: {s.message}" for s in failed)
            QMessageBox.warning(self, "Zpracování nebylo kompletní", msgs[:2500])
        else:
            self.statusBar().showMessage("Zpracování dokončeno.", 8000)

    def _on_failed(self, exc: Exception) -> None:
        self._task_done()
        self.reload_projects()
        if isinstance(exc, ConsentRequired):
            QMessageBox.warning(self, "Souhlas s přenosem dat", str(exc))
        else:
            QMessageBox.critical(self, "Chyba", f"Úloha selhala: {exc}\n\nPodrobnosti jsou v logu aplikace.")

    def cancel_task(self) -> None:
        if self._worker:
            self._worker.cancel.set()
            self.cancel_btn.setEnabled(False)
            self.statusBar().showMessage("Ruším… (čekám na dokončení aktuálního požadavku)")

    # ------------------------------------------------------------- export
    def export(self) -> None:
        if not self.project:
            return
        out = QFileDialog.getExistingDirectory(self, "Kam exportovat studijní materiály")
        if not out:
            return
        try:
            files = export_all(self.project, self.results, self.pdir, Path(out), set(self.settings.export_formats))
        except (OSError, StorageError) as exc:
            QMessageBox.critical(self, "Export", f"Export se nezdařil: {exc}")
            return
        if QMessageBox.information(self, "Export", f"Vytvořeno {len(files)} souborů v:\n{files[0].parent if files else out}"
                                   "\n\nOtevřít složku?", QMessageBox.StandardButton.Open |
                                   QMessageBox.StandardButton.Close) == QMessageBox.StandardButton.Open and files:
            open_folder(files[0].parent)

    def closeEvent(self, e) -> None:  # noqa: N802
        if self.busy:
            if QMessageBox.question(self, "Probíhá zpracování", "Zpracování stále běží. Zrušit a ukončit?") \
                    != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.cancel_task()
            if self._thread:
                self._thread.quit()
                self._thread.wait(5000)
        e.accept()
