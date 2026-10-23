"""Ukládání projektů: složková struktura projektu + SQLite index (projekty, fulltext stránek)."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import DocStatus, DocumentExtraction, DocumentRef, Project, StudyResults, utcnow
from .pdf.extractor import EXTRACTOR_VERSION, PdfError, file_hash, page_count
from .security import ensure_within, is_pdf_file, safe_filename, unique_path

log = logging.getLogger(__name__)

SUBDIRS = ("source", "extracted", "study_materials", "flashcards", "quizzes", "visualizations", "cache",
           "exports")
MIN_FREE_BYTES = 50 * 1024 * 1024


class StorageError(Exception):
    pass


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path.parent).free
    if free < MIN_FREE_BYTES + len(text.encode("utf-8")):
        raise StorageError(f"Nedostatek volného místa na disku ({free // (1024 * 1024)} MB).")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class ProjectStore:
    def __init__(self, data_root: Path):
        self.root = data_root
        self.projects_dir = data_root / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_root / "app.db"
        self._lock = threading.Lock()
        self._fts = True
        self._init_db()

    # ------------------------------------------------------------------ DB
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                      "updated_at TEXT NOT NULL)")
            try:
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(project_id UNINDEXED, "
                          "doc_id UNINDEXED, page UNINDEXED, text, tokenize='unicode61 remove_diacritics 2')")
            except sqlite3.OperationalError:
                log.warning("SQLite FTS5 není k dispozici – použije se jednoduché hledání.")
                self._fts = False
                c.execute("CREATE TABLE IF NOT EXISTS pages_fts (project_id TEXT, doc_id TEXT, page INTEGER, "
                          "text TEXT)")

    # ------------------------------------------------------------ projekty
    def project_dir(self, project_id: str) -> Path:
        if not project_id.isalnum():
            raise StorageError("Neplatné ID projektu.")
        return ensure_within(self.projects_dir, Path(project_id))

    def create_project(self, name: str, copy_sources: bool = False) -> Project:
        name = name.strip() or "Nový projekt"
        p = Project(name=name[:120], copy_sources=copy_sources)
        d = self.project_dir(p.id)
        for sub in SUBDIRS:
            (d / sub).mkdir(parents=True, exist_ok=True)
        self.save_project(p)
        return p

    def save_project(self, p: Project) -> None:
        p.updated_at = utcnow()
        d = self.project_dir(p.id)
        _atomic_write(d / "project.json", p.model_dump_json(indent=2))
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO projects (id, name, updated_at) VALUES (?, ?, ?)",
                      (p.id, p.name, p.updated_at.isoformat()))

    def load_project(self, project_id: str) -> Project:
        f = self.project_dir(project_id) / "project.json"
        if not f.exists():
            raise StorageError("Projekt neexistuje.")
        return Project.model_validate_json(f.read_text(encoding="utf-8"))

    def list_projects(self) -> list[Project]:
        out: list[Project] = []
        for f in sorted(self.projects_dir.glob("*/project.json")):
            try:
                out.append(Project.model_validate_json(f.read_text(encoding="utf-8")))
            except Exception as exc:
                log.warning("Projekt %s nelze načíst: %s", f.parent.name, exc)
        out.sort(key=lambda p: p.updated_at, reverse=True)
        return out

    def delete_project(self, project_id: str) -> None:
        """Smaže data projektu v úložišti aplikace. Originální PDF mimo úložiště se nemažou."""
        d = self.project_dir(project_id)
        shutil.rmtree(d, ignore_errors=True)
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            c.execute("DELETE FROM pages_fts WHERE project_id = ?", (project_id,))

    # ----------------------------------------------------------- dokumenty
    def add_document(self, p: Project, pdf_path: Path) -> DocumentRef:
        pdf_path = Path(pdf_path).expanduser().resolve()
        if pdf_path.suffix.lower() != ".pdf" or not is_pdf_file(pdf_path):
            raise PdfError(f"{pdf_path.name} není platný PDF soubor.")
        h = file_hash(pdf_path)
        if any(d.content_hash == h for d in p.documents):
            raise PdfError(f"{pdf_path.name} už v projektu je (stejný obsah).")
        n = page_count(pdf_path)
        stored = pdf_path
        if p.copy_sources:
            target = unique_path(self.project_dir(p.id) / "source" / safe_filename(pdf_path.name))
            shutil.copy2(pdf_path, target)
            stored = target
        ref = DocumentRef(path=str(stored), filename=pdf_path.name, content_hash=h, page_count=n)
        p.documents.append(ref)
        self.save_project(p)
        return ref

    def remove_document(self, p: Project, doc_id: str) -> None:
        ref = p.doc(doc_id)
        if not ref:
            return
        p.documents = [d for d in p.documents if d.id != doc_id]
        d = self.project_dir(p.id)
        (d / "extracted" / f"{doc_id}.json").unlink(missing_ok=True)
        # kopii smažeme jen pokud leží v úložišti projektu; originál uživatele nikdy
        src = Path(ref.path)
        try:
            ensure_within(d / "source", src)
            src.unlink(missing_ok=True)
        except Exception:
            pass
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM pages_fts WHERE project_id = ? AND doc_id = ?", (p.id, doc_id))
        self.save_project(p)

    def document_changed(self, ref: DocumentRef) -> bool:
        path = Path(ref.path)
        return not path.exists() or file_hash(path) != ref.content_hash

    # ----------------------------------------------------------- extrakce
    def save_extraction(self, project_id: str, ex: DocumentExtraction) -> None:
        f = self.project_dir(project_id) / "extracted" / f"{ex.doc_id}.json"
        _atomic_write(f, ex.model_dump_json())
        self.index_pages(project_id, ex)

    def load_extraction(self, project_id: str, ref: DocumentRef) -> DocumentExtraction | None:
        f = self.project_dir(project_id) / "extracted" / f"{ref.id}.json"
        if not f.exists():
            return None
        try:
            ex = DocumentExtraction.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception:
            return None
        if ex.content_hash != ref.content_hash or ex.extractor_version != EXTRACTOR_VERSION:
            return None
        return ex

    def index_pages(self, project_id: str, ex: DocumentExtraction) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM pages_fts WHERE project_id = ? AND doc_id = ?", (project_id, ex.doc_id))
            c.executemany("INSERT INTO pages_fts (project_id, doc_id, page, text) VALUES (?, ?, ?, ?)",
                          [(project_id, ex.doc_id, pg.number, pg.text) for pg in ex.pages if pg.text])

    def search(self, project_id: str, query: str, limit: int = 50) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        with self._conn() as c:
            if self._fts:
                terms = " ".join('"' + t.replace('"', "") + '"' for t in query.split())
                try:
                    rows = c.execute("SELECT doc_id, page, snippet(pages_fts, 3, '[', ']', '…', 12) AS snip "
                                     "FROM pages_fts WHERE project_id = ? AND pages_fts MATCH ? ORDER BY rank "
                                     "LIMIT ?", (project_id, terms, limit)).fetchall()
                    return [dict(r) for r in rows]
                except sqlite3.OperationalError:
                    pass
            rows = c.execute("SELECT doc_id, page, substr(text, 1, 160) AS snip FROM pages_fts WHERE "
                             "project_id = ? AND text LIKE ? LIMIT ?", (project_id, f"%{query}%", limit)).fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------- cache
    @staticmethod
    def cache_key(*parts: Any) -> str:
        raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def cache_get(self, project_id: str, key: str) -> Any | None:
        f = self.project_dir(project_id) / "cache" / f"{key}.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                f.unlink(missing_ok=True)
        return None

    def cache_put(self, project_id: str, key: str, value: Any) -> None:
        f = self.project_dir(project_id) / "cache" / f"{key}.json"
        _atomic_write(f, json.dumps(value, ensure_ascii=False, default=str))

    def clear_cache(self, project_id: str) -> None:
        d = self.project_dir(project_id) / "cache"
        for f in d.glob("*.json"):
            f.unlink(missing_ok=True)

    # ------------------------------------------------------------ výsledky
    def save_results(self, project_id: str, results: StudyResults) -> None:
        results.updated_at = utcnow()
        f = self.project_dir(project_id) / "study_materials" / "results.json"
        _atomic_write(f, results.model_dump_json(indent=1))

    def load_results(self, project_id: str) -> StudyResults:
        f = self.project_dir(project_id) / "study_materials" / "results.json"
        if f.exists():
            try:
                return StudyResults.model_validate_json(f.read_text(encoding="utf-8"))
            except Exception as exc:
                log.error("Výsledky projektu nelze načíst: %s", exc)
                raise StorageError(f"Uložené výsledky projektu jsou poškozené: {exc}") from exc
        return StudyResults()

    def set_doc_status(self, p: Project, doc_id: str, status: DocStatus, error: str | None = None) -> None:
        ref = p.doc(doc_id)
        if ref:
            ref.status, ref.error = status, error
            self.save_project(p)
