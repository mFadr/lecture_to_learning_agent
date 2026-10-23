"""Agent Orchestrator – řídí kroky zpracování, cache, opakování a validaci výstupů."""

from __future__ import annotations

import logging
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..llm.base import LLMCancelled, LLMError, LLMInvalidResponse, LLMProvider, Message
from ..llm.factory import ProviderRouter
from ..llm.json_utils import ask_json, extract_json
from ..models import (CardKind, ChunkAnalysis, DetailLevel, DocStatus, DocumentExtraction, Example,
                      ExampleStatus, Flashcard, Formula, Origin, Project, QualityIssue, QuestionKind,
                      QuizQuestion, Relation, SourceRef, StepRecord, StepStatus, StudyResults, Summary,
                      SummaryPoint, SummarySection, Term, Topic, Visualization, VizSpec, VizTreeNode, VizType,
                      format_pages, utcnow)
from ..pdf.extractor import OcrConfig, PdfError, extract_pdf
from ..storage import ProjectStore
from ..viz.render import RenderError, render_to_files
from ..viz.svg import render_svg
from ..viz.validate import CHART_TYPES, validate_spec
from . import prompts
from .chunking import Chunk, make_chunks, page_header
from .grounding import (find_quote_pages, find_term_pages, key, normalize, numbers_in_text, pages_text,
                        valid_pages)
from .schemas import AnalysisOut, CardsOut, PointOut, QuizOut, SummaryOut

log = logging.getLogger(__name__)

PROMPT_VERSION = "3"

STEP_ORDER = ["extract", "analyze", "summary", "flashcards", "quiz", "visualize", "topic_map", "validate"]
STEP_LABELS = {
    "extract": "Extrakce PDF",
    "analyze": "Analýza obsahu a katalog příkladů",
    "summary": "Strukturované shrnutí",
    "flashcards": "Flashcards",
    "quiz": "Procvičovací test",
    "visualize": "Vizualizace příkladů",
    "topic_map": "Tematická mapa",
    "validate": "Kontrola kvality",
}
LLM_STEPS = {"analyze", "summary", "flashcards", "quiz", "visualize"}

ProgressFn = Callable[[str, float, str], None]


class ConsentRequired(Exception):
    """Cloudový režim vyžaduje souhlas uživatele s odesíláním dat."""


class StepError(Exception):
    """Krok nelze provést – srozumitelná zpráva pro uživatele."""


@dataclass
class RunOptions:
    steps: list[str] = field(default_factory=lambda: list(STEP_ORDER))
    detail: DetailLevel = DetailLevel.STANDARD
    doc_ids: list[str] | None = None
    example_ids: list[str] | None = None  # které příklady vizualizovat (None = automatický výběr)
    viz_overrides: dict[str, VizType] = field(default_factory=dict)
    max_auto_viz: int = 4
    n_flashcards: int = 30
    n_questions: int = 15
    force: bool = False  # ignorovat cache LLM výstupů


_VIZ_SYNONYMS = {"flow": VizType.FLOWCHART, "process": VizType.FLOWCHART, "steps": VizType.FLOWCHART,
                 "state_diagram": VizType.STATE, "cause": VizType.CAUSAL, "hierarchy": VizType.TREE,
                 "mind_map": VizType.MINDMAP, "matrix": VizType.COMPARISON, "compare": VizType.COMPARISON,
                 "bar_chart": VizType.BAR, "line_chart": VizType.LINE, "scatter_plot": VizType.SCATTER,
                 "function_plot": VizType.FUNCTION, "graph": VizType.CONCEPT, "concept_map": VizType.CONCEPT}


def parse_viz_type(v: str) -> VizType:
    v = (v or "").strip().lower().split("|")[0].strip().replace(" ", "_").replace("-", "_")
    try:
        return VizType(v)
    except ValueError:
        return _VIZ_SYNONYMS.get(v, VizType.CONCEPT)


def parse_origin(v: str) -> Origin:
    v = (v or "").strip().lower()
    if v.startswith("source") or v.startswith("zdroj"):
        return Origin.SOURCE
    if v.startswith("model") or v.startswith("extern"):
        return Origin.MODEL
    return Origin.DERIVED


class Orchestrator:
    def __init__(self, store: ProjectStore, project: Project, settings: Settings, router: ProviderRouter,
                 cancel: threading.Event | None = None, progress: ProgressFn | None = None):
        self.store = store
        self.project = project
        self.settings = settings
        self.router = router
        self.cancel = cancel or threading.Event()
        self._progress = progress or (lambda s, f, m: None)
        self.extractions: dict[str, DocumentExtraction] = {}
        self._checked: dict[int, str | None] = {}

    # ------------------------------------------------------------------ util
    @property
    def pdir(self) -> Path:
        return self.store.project_dir(self.project.id)

    def progress(self, step: str, frac: float, msg: str) -> None:
        self._progress(step, max(0.0, min(1.0, frac)), msg)

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise LLMCancelled("Úloha byla zrušena.")

    def _docs(self, opts: RunOptions):
        docs = self.project.documents
        if opts.doc_ids:
            docs = [d for d in docs if d.id in opts.doc_ids]
        return docs

    def _provider(self, step: str) -> LLMProvider:
        p = self.router.for_step(step)
        if not p.is_local and not self.settings.cloud_consent:
            raise ConsentRequired("Krok by odeslal části dokumentu cloudovému poskytovateli, ale souhlas "
                                  "s přenosem dat nebyl udělen (Nastavení → AI model).")
        if id(p) not in self._checked:
            try:
                st = p.check()
                self._checked[id(p)] = None if st.ok else st.message
            except LLMError as exc:
                self._checked[id(p)] = str(exc)
        problem = self._checked[id(p)]
        if problem:
            raise StepError(f"Jazykový model není dostupný: {problem}")
        return p

    def doc_name(self, doc_id: str) -> str:
        ref = self.project.doc(doc_id)
        return ref.filename if ref else doc_id

    def source_label(self, src: SourceRef) -> str:
        return f"{self.doc_name(src.doc_id)}, s. {format_pages(src.pages) or '?'}"

    # ------------------------------------------------------------------- běh
    def run(self, opts: RunOptions) -> StudyResults:
        steps = [s for s in STEP_ORDER if s in opts.steps]
        if self.router.uses_cloud([s for s in steps if s in LLM_STEPS]) and not self.settings.cloud_consent:
            raise ConsentRequired("Pro cloudový/hybridní režim je potřeba souhlas s odesíláním dat.")
        results = self.store.load_results(self.project.id)
        results.steps = [StepRecord(name=s) for s in steps]
        failed_hard: set[str] = set()
        for rec in results.steps:
            step = rec.name
            if self.cancel.is_set():
                rec.status, rec.message = StepStatus.CANCELLED, "Zrušeno uživatelem."
                continue
            if step != "extract" and "extract" in failed_hard:
                rec.status, rec.message = StepStatus.SKIPPED, "Přeskočeno – extrakce selhala."
                continue
            if step in ("summary", "flashcards", "quiz", "visualize", "topic_map") and "analyze" in failed_hard:
                rec.status, rec.message = StepStatus.SKIPPED, "Přeskočeno – analýza obsahu selhala."
                continue
            rec.status, rec.started_at = StepStatus.RUNNING, utcnow()
            self.progress(step, 0.0, STEP_LABELS[step] + "…")
            try:
                msg = getattr(self, f"_step_{step}")(results, opts) or ""
                partial = msg.startswith("!")
                rec.status = StepStatus.PARTIAL if partial else StepStatus.DONE
                rec.message = msg.lstrip("!")
            except (LLMCancelled, InterruptedError):
                rec.status, rec.message = StepStatus.CANCELLED, "Zrušeno uživatelem."
                self.cancel.set()
            except (StepError, LLMError, PdfError, ConsentRequired, RenderError) as exc:
                rec.status, rec.message = StepStatus.FAILED, str(exc)
                failed_hard.add(step)
                log.warning("Krok %s selhal: %s", step, exc)
            except Exception as exc:  # neočekávaná chyba – zalogovat, ale nespadnout
                log.exception("Neočekávaná chyba v kroku %s", step)
                rec.status, rec.message = StepStatus.FAILED, f"Neočekávaná chyba: {exc}"
                failed_hard.add(step)
            rec.finished_at = utcnow()
            self.progress(step, 1.0, f"{STEP_LABELS[step]}: {rec.status.value}")
            results.llm_label = self.router.describe() if any(s in LLM_STEPS for s in steps) else results.llm_label
            self.store.save_results(self.project.id, results)
        self.store.save_project(self.project)
        return results

    # ------------------------------------------------------------- extrakce
    def _step_extract(self, results: StudyResults, opts: RunOptions) -> str:
        docs = self._docs(opts)
        if not docs:
            raise StepError("Projekt neobsahuje žádné PDF.")
        ocr = OcrConfig(self.settings.ocr_enabled, self.settings.ocr_language, self.settings.tesseract_path)
        errors = []
        for i, ref in enumerate(docs):
            self._check_cancel()
            cached = None if opts.force else self.store.load_extraction(self.project.id, ref)
            if cached:
                self.extractions[ref.id] = cached
                continue
            try:
                if self.store.document_changed(ref):
                    path = Path(ref.path)
                    if not path.exists():
                        raise PdfError(f"Soubor {ref.filename} už neexistuje na původním místě ({ref.path}).")
                    from ..pdf.extractor import file_hash, page_count
                    ref.content_hash, ref.page_count = file_hash(path), page_count(path)
                ex = extract_pdf(Path(ref.path), ref.id, ocr=ocr, cancelled=self.cancel.is_set,
                                 progress=lambda a, b, i=i: self.progress(
                                     "extract", (i + a / b) / len(docs), f"{ref.filename}: strana {a}/{b}"))
                self.store.save_extraction(self.project.id, ex)
                self.extractions[ref.id] = ex
                ref.status, ref.error = DocStatus.EXTRACTED, None
            except InterruptedError:
                raise
            except PdfError as exc:
                ref.status, ref.error = DocStatus.ERROR, str(exc)
                errors.append(str(exc))
        self.store.save_project(self.project)
        if errors and len(errors) == len(docs):
            raise StepError("; ".join(errors))
        readable = [e for e in self.extractions.values() if any(p.text for p in e.pages)]
        if not readable:
            raise StepError("Žádný dokument neobsahuje čitelný text (sken bez OCR?). Obsah nelze analyzovat.")
        return ("!" + "; ".join(errors)) if errors else f"{len(self.extractions)} dokumentů připraveno."

    def _ensure_extractions(self, opts: RunOptions) -> None:
        for ref in self._docs(opts):
            if ref.id not in self.extractions:
                ex = self.store.load_extraction(self.project.id, ref)
                if ex:
                    self.extractions[ref.id] = ex
        if not self.extractions:
            raise StepError("Dokumenty ještě nebyly extrahovány – spusťte krok Extrakce PDF.")

    # --------------------------------------------------------------- analýza
    def _step_analyze(self, results: StudyResults, opts: RunOptions) -> str:
        self._ensure_extractions(opts)
        provider = self._provider("analyze")
        chunks: list[Chunk] = []
        for ref in self._docs(opts):
            ex = self.extractions.get(ref.id)
            if ex:
                chunks.extend(make_chunks(ex, provider.max_request_chars, ref.selected_pages()))
        if not chunks:
            raise StepError("Ve vybraném rozsahu stránek není žádný čitelný text.")
        analyses: list[ChunkAnalysis] = []
        failed: list[str] = []
        known_topics: list[str] = []
        for i, ch in enumerate(chunks):
            self._check_cancel()
            self.progress("analyze", i / len(chunks), f"Analyzuji {ch.label} ({i + 1}/{len(chunks)})")
            ckey = ProjectStore.cache_key("analyze", PROMPT_VERSION, provider.label, ch.digest)
            raw = None if opts.force else self.store.cache_get(self.project.id, ckey)
            try:
                if raw is None:
                    out = ask_json(provider, prompts.BASE_SYSTEM,
                                   prompts.analysis_prompt(ch.text, ch.label, known_topics), AnalysisOut,
                                   cancel=self.cancel)
                    raw = out.model_dump(by_alias=True)
                    self.store.cache_put(self.project.id, ckey, raw)
                out = AnalysisOut.model_validate(raw)
            except LLMCancelled:
                raise
            except LLMError as exc:
                failed.append(f"{ch.label}: {exc}")
                if self._is_fatal(exc):
                    raise
                continue
            ca = self._ground_analysis(out, ch)
            analyses.append(ca)
            known_topics += [t.title for t in ca.topics if t.title not in known_topics]
        if not analyses:
            raise StepError("Analýza selhala pro všechny části dokumentu: " + "; ".join(failed[:3]))
        self._merge(results, analyses)
        msg = (f"{len(results.topics)} témat, {len(results.terms)} pojmů, {len(results.formulas)} vzorců, "
               f"{len(results.examples)} příkladů.")
        for ref in self._docs(opts):
            if ref.id in self.extractions:
                ref.status = DocStatus.ANALYZED
        if failed:
            results.analysis_warnings.append(f"Neanalyzované části ({len(failed)}): " + "; ".join(failed))
            return f"!Částečně – {len(failed)} z {len(chunks)} částí selhalo (lze zopakovat). " + msg
        return msg

    @staticmethod
    def _is_fatal(exc: LLMError) -> bool:
        from ..llm.base import LLMAuthError, LLMBudgetExceeded
        return isinstance(exc, (LLMAuthError, LLMBudgetExceeded))

    def _ground_analysis(self, out: AnalysisOut, ch: Chunk) -> ChunkAnalysis:
        allowed = set(ch.pages)

        def src(pages: list[int], quote: str = "", *find: str) -> SourceRef:
            p = valid_pages(pages, allowed)
            verified = False
            if quote:
                qp = find_quote_pages(quote, ch.page_texts)
                if qp:
                    verified = True
                    p = sorted(set(p) & set(qp)) or qp
            if not p:
                for f in find:
                    p = find_term_pages(f, ch.page_texts)
                    if p:
                        break
            return SourceRef(doc_id=ch.doc_id, pages=p[:12], quote=quote or None, quote_verified=verified)

        ca = ChunkAnalysis(warnings=[f"{ch.label}: {u}" for u in out.unclear])
        for t in out.topics:
            if t.title:
                ca.topics.append(Topic(title=t.title, summary=t.summary, subtopics=t.subtopics,
                                       source=src(t.pages, "", t.title)))
        for t in out.terms:
            if t.name and (t.definition or t.quote):
                s = src(t.pages, t.quote, t.name)
                ca.terms.append(Term(name=t.name, definition=t.definition or t.quote,
                                     simple_explanation=t.simple_explanation, example=t.example,
                                     related=t.related, source=s, topic=t.topic,
                                     origin=Origin.SOURCE if s.pages else Origin.DERIVED))
        for f in out.formulas:
            if f.expression:
                ca.formulas.append(Formula(expression=f.expression, meaning=f.meaning, variables=f.variables,
                                           source=src(f.pages, "", f.expression), topic=f.topic))
        for e in out.examples:
            if e.title and e.description:
                ca.examples.append(Example(title=e.title, topic=e.topic, subtopic=e.subtopic,
                                           description=e.description, concept=e.concept, actors=e.actors,
                                           relations=e.relations, numeric_data=e.numeric_data,
                                           source=src(e.pages, "", e.title, e.concept),
                                           recommended_viz=parse_viz_type(e.viz_type), viz_reason=e.viz_reason))
        for r in out.relations:
            if r.source and r.target:
                ca.relations.append(Relation(source_concept=r.source, target_concept=r.target, kind=r.kind,
                                             description=r.description, source=src(r.pages, "", r.source)))
        return ca

    def _merge(self, results: StudyResults, analyses: list[ChunkAnalysis]) -> None:
        """Sjednotí výsledky částí: odstraní duplicity, zachová všechny zdrojové stránky."""
        doc_order = {d.id: i for i, d in enumerate(self.project.documents)}
        topics: dict[tuple, Topic] = {}
        terms: dict[tuple, Term] = {}
        formulas: dict[tuple, Formula] = {}
        examples: dict[tuple, Example] = {}
        relations: dict[tuple, Relation] = {}

        def merge_src(a: SourceRef, b: SourceRef) -> None:
            a.pages = sorted(set(a.pages) | set(b.pages))
            if b.quote_verified and not a.quote_verified:
                a.quote, a.quote_verified = b.quote, True

        for ca in analyses:
            for t in ca.topics:
                k = (t.source.doc_id, key(t.title))
                if k in topics:
                    cur = topics[k]
                    merge_src(cur.source, t.source)
                    cur.subtopics += [s for s in t.subtopics if key(s) not in {key(x) for x in cur.subtopics}]
                    if len(t.summary) > len(cur.summary):
                        cur.summary = t.summary
                else:
                    topics[k] = t
            for t in ca.terms:
                k = (t.source.doc_id, key(t.name))
                if k in terms:
                    cur = terms[k]
                    merge_src(cur.source, t.source)
                    if t.source.quote_verified and not cur.source.quote_verified:
                        cur.definition = t.definition
                    cur.related += [r for r in t.related if r not in cur.related]
                    cur.example = cur.example or t.example
                    cur.simple_explanation = cur.simple_explanation or t.simple_explanation
                else:
                    terms[k] = t
            for f in ca.formulas:
                k = (f.source.doc_id, normalize(f.expression).replace(" ", ""))
                if k in formulas:
                    merge_src(formulas[k].source, f.source)
                    formulas[k].variables.update({a: b for a, b in f.variables.items() if a not in formulas[k].variables})
                else:
                    formulas[k] = f
            for e in ca.examples:
                k = (e.source.doc_id, key(e.title))
                if k in examples:
                    merge_src(examples[k].source, e.source)
                else:
                    examples[k] = e
            for r in ca.relations:
                k = (r.source.doc_id, key(r.source_concept), key(r.target_concept), key(r.kind))
                if k not in relations:
                    relations[k] = r
            results.analysis_warnings += ca.warnings

        def sort_key(item: Any) -> tuple:
            return (doc_order.get(item.source.doc_id, 99), min(item.source.pages, default=10 ** 6))

        results.topics = sorted(topics.values(), key=sort_key)
        for i, t in enumerate(results.topics):
            t.order = i
        results.terms = sorted(terms.values(), key=lambda t: (doc_order.get(t.source.doc_id, 99), key(t.name)))
        results.formulas = sorted(formulas.values(), key=sort_key)
        results.relations = list(relations.values())
        # zachovat id a stav dříve nalezených příkladů (aby vizualizace zůstaly propojené)
        old = {(e.source.doc_id, key(e.title)): e for e in results.examples}
        merged_examples = []
        for k, e in examples.items():
            if k in old:
                e.id, e.status, e.status_note = old[k].id, old[k].status, old[k].status_note
            merged_examples.append(e)
        results.examples = sorted(merged_examples, key=sort_key)
        results.analysis_warnings = list(dict.fromkeys(results.analysis_warnings))
        # témata společná pro více dokumentů + rozdílné definice
        by_title: dict[str, list[Topic]] = {}
        for t in results.topics:
            by_title.setdefault(key(t.title), []).append(t)
        results.cross_document_topics = [
            {"title": ts[0].title,
             "documents": [{"doc_id": t.source.doc_id, "doc": self.doc_name(t.source.doc_id),
                            "pages": t.source.pages} for t in ts]}
            for ts in by_title.values() if len({t.source.doc_id for t in ts}) > 1]

    # ------------------------------------------------------------- shrnutí
    def _step_summary(self, results: StudyResults, opts: RunOptions) -> str:
        if not results.topics:
            raise StepError("Nejsou k dispozici témata z analýzy – nejprve spusťte analýzu obsahu.")
        self._ensure_extractions(opts)
        provider = self._provider("summary")
        budget = provider.max_request_chars
        groups = self._topic_groups(results.topics, budget)
        sections: list[SummarySection] = []
        failed = 0
        for gi, group in enumerate(groups):
            self._check_cancel()
            self.progress("summary", gi / len(groups), f"Shrnutí: {group[0].title} ({gi + 1}/{len(groups)})")
            doc_id = group[0].source.doc_id
            pages = sorted({p for t in group for p in t.source.pages})
            text = self._pages_block(doc_id, pages, budget)
            block = "\n".join(f"{i + 1}. {t.title} (strany {format_pages(t.source.pages) or '?'})"
                              + (f": {t.summary}" if t.summary else "")
                              + (f" Podtémata: {', '.join(t.subtopics)}." if t.subtopics else "")
                              for i, t in enumerate(group))
            ckey = ProjectStore.cache_key("summary", PROMPT_VERSION, provider.label, opts.detail.value, block, text)
            raw = None if opts.force else self.store.cache_get(self.project.id, ckey)
            try:
                if raw is None:
                    out = ask_json(provider, prompts.BASE_SYSTEM, prompts.summary_prompt(block, opts.detail, text),
                                   SummaryOut, cancel=self.cancel)
                    raw = out.model_dump()
                    self.store.cache_put(self.project.id, ckey, raw)
                out = SummaryOut.model_validate(raw)
            except LLMCancelled:
                raise
            except LLMError as exc:
                if self._is_fatal(exc):
                    raise
                failed += 1
                for t in group:
                    sections.append(SummarySection(title=t.title, overview="", source=t.source,
                                                   missing_info=[f"Shrnutí se nepodařilo vytvořit: {exc}"]))
                continue
            allowed = set(pages)
            for i, t in enumerate(group):
                so = out.sections[i] if i < len(out.sections) else next(
                    (s for s in out.sections if key(s.title) == key(t.title)), None)
                if so is None:
                    sections.append(SummarySection(title=t.title, source=t.source,
                                                   missing_info=["Model pro tuto kapitolu nevrátil shrnutí."]))
                    continue

                def pts(lst: list[PointOut]) -> list[SummaryPoint]:
                    return [SummaryPoint(text=p.text, pages=valid_pages(p.pages, allowed), origin=parse_origin(p.origin))
                            for p in lst if p.text.strip()]
                sec_pages = valid_pages(so.pages, allowed) or t.source.pages
                sections.append(SummarySection(
                    title=t.title, overview=so.overview, explanation=pts(so.explanation),
                    key_definitions=pts(so.key_definitions), formulas=pts(so.formulas), examples=pts(so.examples),
                    connections=pts(so.connections), pitfalls=pts(so.pitfalls), missing_info=so.missing_info,
                    source=SourceRef(doc_id=doc_id, pages=sec_pages)))
        results.summary = Summary(detail=opts.detail, sections=sections)
        if failed:
            return f"!Částečně – {failed} z {len(groups)} skupin kapitol se nepodařilo shrnout."
        return f"{len(sections)} kapitol."

    def _topic_groups(self, topics: list[Topic], budget: int) -> list[list[Topic]]:
        groups: list[list[Topic]] = []
        cur: list[Topic] = []
        cur_pages: set[int] = set()
        for t in topics:
            same_doc = cur and cur[0].source.doc_id == t.source.doc_id
            new_pages = cur_pages | set(t.source.pages)
            size = self._pages_size(t.source.doc_id, sorted(new_pages))
            if cur and (not same_doc or size > budget or len(cur) >= 6):
                groups.append(cur)
                cur, new_pages = [], set(t.source.pages)
            cur.append(t)
            cur_pages = new_pages
        if cur:
            groups.append(cur)
        return groups

    def _pages_size(self, doc_id: str, pages: list[int]) -> int:
        return sum(len(t) for t in pages_text(self.extractions, doc_id, pages).values())

    def _pages_block(self, doc_id: str, pages: list[int], budget: int) -> str:
        from ..security import DOC_CLOSE, DOC_OPEN, sanitize_document_text
        texts = pages_text(self.extractions, doc_id, pages)
        if not texts:
            return f"{DOC_OPEN}\n(text stránek není k dispozici)\n{DOC_CLOSE}"
        per_page = max(400, budget // max(1, len(texts)))
        name = self.doc_name(doc_id)
        parts = []
        for p, t in sorted(texts.items()):
            t = sanitize_document_text(t)
            if len(t) > per_page:
                t = t[:per_page] + " […zkráceno…]"
            parts.append(f"{page_header(name, p)}\n{t}")
        return f"{DOC_OPEN}\n" + "\n\n".join(parts) + f"\n{DOC_CLOSE}"

    # ---------------------------------------------------- kartičky a testy
    def _items_by_doc(self, results: StudyResults) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}

        def add(src: SourceRef, line: str) -> None:
            out.setdefault(src.doc_id, []).append(f"{line} (strany {format_pages(src.pages) or '?'})")
        for t in results.terms:
            add(t.source, f"POJEM: {t.name} – {t.definition}" + (f" Příklad: {t.example}" if t.example else ""))
        for f in results.formulas:
            vars_ = "; ".join(f"{k} = {v}" for k, v in f.variables.items())
            add(f.source, f"VZOREC: {f.expression} – {f.meaning}" + (f" Proměnné: {vars_}" if vars_ else ""))
        for e in results.examples:
            add(e.source, f"PŘÍKLAD: {e.title} – {e.description}" +
                (f" Data: {e.numeric_data}" if e.numeric_data else "") + (f" Koncept: {e.concept}" if e.concept else ""))
        for r in results.relations:
            add(r.source, f"VZTAH: {r.source_concept} {r.kind} {r.target_concept}" +
                (f" – {r.description}" if r.description else ""))
        return out

    def _batches(self, results: StudyResults, budget: int) -> list[tuple[str, list[str]]]:
        batches: list[tuple[str, list[str]]] = []
        for doc_id, items in self._items_by_doc(results).items():
            cur: list[str] = []
            size = 0
            for it in items:
                if cur and (size + len(it) > budget or len(cur) >= 40):
                    batches.append((doc_id, cur))
                    cur, size = [], 0
                cur.append(it)
                size += len(it) + 1
            if cur:
                batches.append((doc_id, cur))
        return batches

    def _step_flashcards(self, results: StudyResults, opts: RunOptions) -> str:
        provider = self._provider("flashcards")
        batches = self._batches(results, provider.max_request_chars)
        if not batches:
            raise StepError("Nejsou k dispozici pojmy ani příklady z analýzy – nejprve spusťte analýzu.")
        total_items = sum(len(b) for _, b in batches)
        cards: list[Flashcard] = []
        seen: set[str] = set()
        failed = 0
        for bi, (doc_id, items) in enumerate(batches):
            self._check_cancel()
            self.progress("flashcards", bi / len(batches), f"Kartičky {bi + 1}/{len(batches)}")
            n = max(5, round(opts.n_flashcards * len(items) / total_items))
            block = "\n".join(items)
            allowed = self._doc_pages(doc_id)
            ckey = ProjectStore.cache_key("cards", PROMPT_VERSION, provider.label, n, block)
            try:
                raw = None if opts.force else self.store.cache_get(self.project.id, ckey)
                if raw is None:
                    raw = ask_json(provider, prompts.BASE_SYSTEM, prompts.flashcards_prompt(block, n), CardsOut,
                                   cancel=self.cancel).model_dump()
                    self.store.cache_put(self.project.id, ckey, raw)
                out = CardsOut.model_validate(raw)
            except LLMCancelled:
                raise
            except LLMError as exc:
                if self._is_fatal(exc):
                    raise
                failed += 1
                continue
            for c in out.cards:
                if not c.question.strip() or not c.answer.strip() or key(c.question) in seen:
                    continue
                seen.add(key(c.question))
                try:
                    kind = CardKind(c.kind.strip().lower())
                except ValueError:
                    kind = CardKind.DEFINITION
                cards.append(Flashcard(question=c.question.strip(), answer=c.answer.strip(), kind=kind,
                                       topic=c.topic, source=SourceRef(doc_id=doc_id, pages=valid_pages(c.pages, allowed))))
        if not cards:
            raise StepError("Nepodařilo se vytvořit žádné kartičky.")
        results.flashcards = cards
        return (f"!Částečně – {failed} dávek selhalo. " if failed else "") + f"{len(cards)} kartiček."

    def _doc_pages(self, doc_id: str) -> set[int]:
        ref = self.project.doc(doc_id)
        return set(range(1, (ref.page_count if ref else 0) + 1))

    def _step_quiz(self, results: StudyResults, opts: RunOptions) -> str:
        provider = self._provider("quiz")
        batches = self._batches(results, provider.max_request_chars)
        if not batches:
            raise StepError("Nejsou k dispozici pojmy ani příklady z analýzy – nejprve spusťte analýzu.")
        total_items = sum(len(b) for _, b in batches)
        questions: list[QuizQuestion] = []
        dropped = failed = 0
        rng = random.Random(self.project.id)
        for bi, (doc_id, items) in enumerate(batches):
            self._check_cancel()
            self.progress("quiz", bi / len(batches), f"Testové otázky {bi + 1}/{len(batches)}")
            n = max(4, round(opts.n_questions * len(items) / total_items))
            block = "\n".join(items)
            allowed = self._doc_pages(doc_id)
            ckey = ProjectStore.cache_key("quiz", PROMPT_VERSION, provider.label, n, block)
            try:
                raw = None if opts.force else self.store.cache_get(self.project.id, ckey)
                if raw is None:
                    raw = ask_json(provider, prompts.BASE_SYSTEM, prompts.quiz_prompt(block, n), QuizOut,
                                   cancel=self.cancel).model_dump()
                    self.store.cache_put(self.project.id, ckey, raw)
                out = QuizOut.model_validate(raw)
            except LLMCancelled:
                raise
            except LLMError as exc:
                if self._is_fatal(exc):
                    raise
                failed += 1
                continue
            for q in out.questions:
                qq = normalize_question(q.kind, q.question, q.options, q.correct_answer, rng)
                if qq is None:
                    dropped += 1
                    continue
                kind, options, correct = qq
                questions.append(QuizQuestion(kind=kind, question=q.question.strip(), options=options,
                                              correct_answer=correct, explanation=q.explanation, topic=q.topic,
                                              source=SourceRef(doc_id=doc_id, pages=valid_pages(q.pages, allowed))))
        if not questions:
            raise StepError("Nepodařilo se vytvořit žádné platné testové otázky.")
        results.quiz = questions
        note = f" ({dropped} neplatných otázek vyřazeno)" if dropped else ""
        return (f"!Částečně – {failed} dávek selhalo. " if failed else "") + f"{len(questions)} otázek{note}."

    # ---------------------------------------------------------- vizualizace
    def _step_visualize(self, results: StudyResults, opts: RunOptions) -> str:
        if not results.examples:
            raise StepError("Analýza nenašla žádné konkrétní příklady vhodné pro vizualizaci.")
        self._ensure_extractions(opts)
        if opts.example_ids:
            targets = [e for e in results.examples if e.id in opts.example_ids]
        else:
            targets = [e for e in results.examples if e.status == ExampleStatus.SELECTED]
            if not targets:
                has_valid = {v.example_id for v in results.visualizations if v.valid}
                cands = [e for e in results.examples if e.id not in has_valid and e.status != ExampleStatus.FAILED]
                cands.sort(key=lambda e: (e.recommended_viz == VizType.CONCEPT, not e.numeric_data))
                targets = cands[:opts.max_auto_viz]
        if not targets:
            return "Všechny vybrané příklady už mají vizualizaci."
        ok = 0
        for i, ex in enumerate(targets):
            self._check_cancel()
            self.progress("visualize", i / len(targets), f"Vizualizace: {ex.title} ({i + 1}/{len(targets)})")
            viz = self.visualize_example(results, ex, opts.viz_overrides.get(ex.id))
            ok += viz.valid
            self.store.save_results(self.project.id, results)
        if ok < len(targets):
            return f"!{ok} z {len(targets)} vizualizací vytvořeno; ostatní neprošly validací (viz katalog příkladů)."
        return f"{ok} vizualizací."

    def visualize_example(self, results: StudyResults, ex: Example, viz_type: VizType | None = None) -> Visualization:
        """Vytvoří (novou verzi) vizualizace příkladu. Neplatná data se nevykreslí."""
        provider = self._provider("visualize")
        viz_type = viz_type or ex.recommended_viz
        pages = ex.source.pages
        if not pages:
            pages = find_term_pages(ex.title, pages_text(self.extractions, ex.source.doc_id,
                                                         sorted(self._doc_pages(ex.source.doc_id))))[:3]
        src_text = self._pages_block(ex.source.doc_id, pages, provider.max_request_chars) if pages else ""
        all_src = " ".join(pages_text(self.extractions, ex.source.doc_id, pages).values())
        src_numbers = numbers_in_text(all_src + " " + ex.numeric_data)
        example_block = "\n".join(filter(None, [
            f"Název: {ex.title}", f"Téma: {ex.topic}" if ex.topic else "", f"Popis podle zdroje: {ex.description}",
            f"Vysvětlovaný koncept: {ex.concept}" if ex.concept else "",
            f"Aktéři / proměnné: {', '.join(ex.actors)}" if ex.actors else "",
            f"Vztahy: {'; '.join(ex.relations)}" if ex.relations else "",
            f"Číselná data: {ex.numeric_data}" if ex.numeric_data else "",
            f"Stránky: {format_pages(pages)}"]))
        version = 1 + max((v.version for v in results.visualizations if v.example_id == ex.id), default=0)
        viz = Visualization(example_id=ex.id, topic=ex.topic, version=version,
                            spec=VizSpec(viz_type=viz_type, title=ex.title),
                            source=SourceRef(doc_id=ex.source.doc_id, pages=pages))
        errors: list[str] = ["Příklad nemá dohledatelnou zdrojovou stránku."] if not pages else []
        warns: list[str] = []
        feedback = ""
        for attempt in range(2 if pages else 0):
            self._check_cancel()
            try:
                resp = provider.chat(
                    [Message("system", prompts.BASE_SYSTEM),
                     Message("user", prompts.viz_prompt(example_block, viz_type, src_text, feedback))],
                    json_output=True, cancel=self.cancel)
                spec = spec_from_raw(extract_json(resp.text), viz_type, ex.title)
            except LLMCancelled:
                raise
            except (LLMInvalidResponse, ValueError) as exc:
                errors, feedback = [f"Neplatná odpověď modelu: {exc}"], str(exc)
                continue
            numbers = src_numbers if (viz_type in CHART_TYPES or viz_type == VizType.TABLE) else None
            errors, warns = validate_spec(spec, numbers)
            viz.spec = spec
            if not errors:
                break
            feedback = " ".join(errors)
        viz.validation_warnings = errors + warns
        viz.valid = not errors
        if viz.valid:
            footer = f"Zdroj: {self.source_label(viz.source)}" + \
                     (" | modelové hodnoty" if viz.spec.illustrative_values else "")
            try:
                render_to_files(viz, self.pdir, footer)
            except RenderError as exc:
                viz.valid = False
                viz.validation_warnings.insert(0, str(exc))
        ex.status = ExampleStatus.GENERATED if viz.valid else ExampleStatus.FAILED
        ex.status_note = "" if viz.valid else "; ".join(viz.validation_warnings[:3])
        results.visualizations.append(viz)
        return viz

    # -------------------------------------------------------- tematická mapa
    def _step_topic_map(self, results: StudyResults, opts: RunOptions) -> str:
        if not results.topics:
            raise StepError("Nejsou k dispozici témata – nejprve spusťte analýzu.")
        docs = [d for d in self.project.documents if any(t.source.doc_id == d.id for t in results.topics)]

        def topic_nodes(doc_id: str, limit: int) -> list[VizTreeNode]:
            ts = [t for t in results.topics if t.source.doc_id == doc_id][:limit]
            return [VizTreeNode(label=f"{t.title} (s. {format_pages(t.source.pages)})" if t.source.pages else t.title,
                                children=[VizTreeNode(label=s) for s in t.subtopics[:4]]) for t in ts]
        if len(docs) == 1:
            root = VizTreeNode(label=self.project.name, children=topic_nodes(docs[0].id, 14))
        else:
            root = VizTreeNode(label=self.project.name,
                               children=[VizTreeNode(label=d.filename, children=topic_nodes(d.id, 8)) for d in docs])
        n_nodes = _count(root)
        vtype = VizType.MINDMAP if n_nodes <= 40 and len(docs) == 1 else VizType.TREE
        spec = VizSpec(viz_type=vtype, title=f"Tematická mapa – {self.project.name}", tree=root)
        out = self.pdir / "visualizations"
        out.mkdir(exist_ok=True)
        (out / "topic_map.svg").write_text(render_svg(spec, "Témata a podtémata podle analýzy přednášek; "
                                                            "čísla stran odkazují do PDF."), encoding="utf-8")
        results.topic_map_svg = "visualizations/topic_map.svg"
        # mapa vztahů mezi pojmy
        rels = results.relations[:40]
        msg = f"Mapa {n_nodes} uzlů"
        if rels:
            ids: dict[str, str] = {}
            nodes, edges = [], []
            for r in rels:
                for c in (r.source_concept, r.target_concept):
                    if key(c) not in ids and len(ids) < 30:
                        ids[key(c)] = f"c{len(ids)}"
                        nodes.append({"id": ids[key(c)], "label": c})
                if key(r.source_concept) in ids and key(r.target_concept) in ids:
                    edges.append({"source": ids[key(r.source_concept)], "target": ids[key(r.target_concept)],
                                  "label": r.kind})
            rspec = VizSpec.model_validate({"viz_type": "concept", "title": "Vztahy mezi pojmy", "nodes": nodes,
                                            "edges": edges})
            (out / "relation_map.svg").write_text(render_svg(rspec, "Vztahy nalezené v přednáškách"), encoding="utf-8")
            results.relation_map_svg = "visualizations/relation_map.svg"
            msg += f", {len(edges)} vztahů"
        return msg + "."

    # ------------------------------------------------------------ validace
    def _step_validate(self, results: StudyResults, opts: RunOptions) -> str:
        issues = validate_results(results, self.project)
        results.issues = issues
        n_err = sum(1 for i in issues if i.severity == "error")
        n_warn = sum(1 for i in issues if i.severity == "warning")
        return f"{n_err} chyb, {n_warn} upozornění."


# ---------------------------------------------------------------------------
# Pomocné funkce (testovatelné samostatně)
# ---------------------------------------------------------------------------


def _count(n: VizTreeNode) -> int:
    return 1 + sum(_count(c) for c in n.children)


_TRUE = {"pravda", "true", "ano", "yes", "p", "správně", "pravdivé"}
_FALSE = {"nepravda", "false", "ne", "no", "n", "nesprávně", "nepravdivé"}


def normalize_question(kind_raw: str, question: str, options: list[str], correct: str,
                       rng: random.Random) -> tuple[QuestionKind, list[str], str] | None:
    """Ověří a normalizuje testovou otázku. Vrací None pro neplatné otázky."""
    if not question.strip():
        return None
    try:
        kind = QuestionKind(kind_raw.strip().lower())
    except ValueError:
        kind = QuestionKind.MULTIPLE_CHOICE if len(options) >= 3 else QuestionKind.SHORT
    correct = correct.strip()
    if kind == QuestionKind.TRUE_FALSE:
        c = correct.lower().strip(" .!")
        if c in _TRUE:
            return kind, ["Pravda", "Nepravda"], "Pravda"
        if c in _FALSE:
            return kind, ["Pravda", "Nepravda"], "Nepravda"
        return None
    if kind == QuestionKind.MULTIPLE_CHOICE:
        opts = [o for o in dict.fromkeys(options)]
        if len(opts) < 3 or not correct:
            return None
        clean = [_strip_letter(o) for o in opts]
        target = _strip_letter(correct)
        match = next((o for o in clean if normalize(o) == normalize(target)), None)
        if match is None and len(correct) == 1 and correct.upper() in "ABCDEF":
            idx = "ABCDEF".index(correct.upper())
            match = clean[idx] if idx < len(clean) else None
        if match is None:
            return None
        rng.shuffle(clean)  # správná odpověď nesmí být vždy na stejné pozici
        return kind, clean, match
    if not correct:
        return None
    return kind, [], correct


def _strip_letter(o: str) -> str:
    import re
    return re.sub(r"^\s*[A-Fa-f][.)]\s+", "", o).strip()


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(" ", "").replace(" ", "").replace("−", "-").rstrip("%")
        if s.count(",") == 1 and "." not in s:
            s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def spec_from_raw(raw: Any, viz_type: VizType, fallback_title: str) -> VizSpec:
    """Převede (nedůvěryhodný) JSON od modelu na validní VizSpec; typ vynucuje agent."""
    if not isinstance(raw, dict):
        raise ValueError("Odpověď není JSON objekt.")
    d: dict[str, Any] = {"viz_type": viz_type.value,
                         "title": str(raw.get("title") or fallback_title)[:150],
                         "explanation": str(raw.get("explanation") or "")[:1500],
                         "x_label": str(raw.get("x_label") or "")[:80], "y_label": str(raw.get("y_label") or "")[:80],
                         "notes": [str(n)[:300] for n in (raw.get("notes") or []) if n][:6]
                         if isinstance(raw.get("notes"), list) else ([str(raw["notes"])] if raw.get("notes") else [])}
    ill = raw.get("illustrative_values", False)
    d["illustrative_values"] = ill if isinstance(ill, bool) else str(ill).strip().lower() in ("true", "ano", "1")
    nodes = []
    for i, n in enumerate(raw.get("nodes") or []):
        if isinstance(n, dict):
            nodes.append({"id": str(n.get("id", f"n{i}")), "label": str(n.get("label") or n.get("name") or "")[:150],
                          "group": str(n.get("group") or "")})
        elif isinstance(n, str):
            nodes.append({"id": f"n{i}", "label": n[:150]})
    d["nodes"] = nodes
    edges = []
    for e in raw.get("edges") or []:
        if isinstance(e, dict):
            sign = str(e.get("sign") or "").strip()
            sign = "+" if sign in ("+", "plus", "positive") else ("-" if sign in ("-", "−", "minus", "negative") else "")
            edges.append({"source": str(e.get("source", e.get("from", ""))), "target": str(e.get("target", e.get("to", ""))),
                          "label": str(e.get("label") or "")[:80], "sign": sign})
    d["edges"] = edges
    d["events"] = [{"when": str(e.get("when") or e.get("date") or ""), "label": str(e.get("label") or "")[:200]}
                   for e in (raw.get("events") or []) if isinstance(e, dict)]

    def tree(t: Any, depth: int = 0) -> dict | None:
        if isinstance(t, str):
            return {"label": t[:150], "children": []}
        if not isinstance(t, dict) or depth > 6:
            return None
        kids = [c for c in (tree(x, depth + 1) for x in (t.get("children") or [])) if c]
        return {"label": str(t.get("label") or t.get("name") or "")[:150], "children": kids}
    d["tree"] = tree(raw.get("tree")) if raw.get("tree") else None
    d["columns"] = [str(c)[:80] for c in (raw.get("columns") or [])]
    d["rows"] = [[str(c)[:200] for c in r] for r in (raw.get("rows") or []) if isinstance(r, list)]
    series = []
    for s in raw.get("series") or []:
        if not isinstance(s, dict):
            continue
        ys = [_num(v) for v in (s.get("y") or [])]
        if any(v is None for v in ys):
            raise ValueError(f"Řada „{s.get('name', '')}“ obsahuje nečíselné hodnoty y.")
        xs = []
        for v in s.get("x") or []:
            nv = _num(v)
            xs.append(nv if nv is not None else str(v)[:40])
        series.append({"name": str(s.get("name") or "")[:60], "x": xs, "y": ys})
    d["series"] = series
    d["sets"] = [{"label": str(s.get("label") or "")[:60], "items": [str(i)[:80] for i in (s.get("items") or [])]}
                 for s in (raw.get("sets") or []) if isinstance(s, dict)]
    d["expression"] = str(raw.get("expression") or "")[:200]
    d["x_min"], d["x_max"] = _num(raw.get("x_min")), _num(raw.get("x_max"))
    return VizSpec.model_validate(d)


def validate_results(results: StudyResults, project: Project) -> list[QualityIssue]:
    """Automatické kontroly kvality a návaznosti na zdroj."""
    issues: list[QualityIssue] = []
    page_counts = {d.id: d.page_count for d in project.documents}

    def check_src(area: str, label: str, src: SourceRef) -> str | None:
        if src.doc_id not in page_counts:
            return f"{label}: odkazuje na dokument, který už v projektu není."
        bad = [p for p in src.pages if not 1 <= p <= page_counts[src.doc_id]]
        if bad:
            return f"{label}: neexistující stránky {bad}."
        if not src.pages:
            return ""
        return None

    groups = [("Pojmy", [(t.name, t.source) for t in results.terms]),
              ("Vzorce", [(f.expression, f.source) for f in results.formulas]),
              ("Příklady", [(e.title, e.source) for e in results.examples]),
              ("Témata", [(t.title, t.source) for t in results.topics]),
              ("Flashcards", [(c.question[:40], c.source) for c in results.flashcards]),
              ("Test", [(q.question[:40], q.source) for q in results.quiz]),
              ("Vizualizace", [(v.spec.title, v.source) for v in results.visualizations])]
    for area, items in groups:
        missing = 0
        for label, src in items:
            r = check_src(area, label, src)
            if r == "":
                missing += 1
            elif r:
                issues.append(QualityIssue(severity="error", area=area, message=r))
        if missing:
            issues.append(QualityIssue(severity="warning", area=area,
                                       message=f"{missing} z {len(items)} položek nemá dohledanou zdrojovou stránku – "
                                               "ověřte je v PDF."))
    if results.terms:
        verified = sum(1 for t in results.terms if t.source.quote_verified)
        issues.append(QualityIssue(severity="info", area="Pojmy",
                                   message=f"{verified} z {len(results.terms)} definic má doslovnou citaci ověřenou v "
                                           "textu PDF; ostatní jsou parafráze."))
    if results.summary:
        for s in results.summary.sections:
            body = len(s.overview) + sum(len(p.text) for p in s.explanation)
            if body < 60:
                issues.append(QualityIssue(severity="warning", area="Shrnutí",
                                           message=f"Kapitola „{s.title}“ má prázdné nebo podezřele krátké shrnutí."))
            nopage = [p for p in s.explanation + s.key_definitions + s.examples
                      if p.origin != Origin.MODEL and not p.pages]
            if nopage:
                issues.append(QualityIssue(severity="info", area="Shrnutí",
                                           message=f"Kapitola „{s.title}“: {len(nopage)} bodů bez konkrétní stránky "
                                                   f"(platí stránky kapitoly {format_pages(s.source.pages)})."))
        n_model = sum(1 for s in results.summary.sections for p in
                      s.explanation + s.pitfalls + s.connections + s.examples if p.origin == Origin.MODEL)
        if n_model:
            issues.append(QualityIssue(severity="info", area="Shrnutí",
                                       message=f"{n_model} bodů je doplňující vysvětlení modelu (označeno „externí“), "
                                               "nikoli obsah přednášky."))
    invalid = [v for v in results.visualizations if not v.valid]
    if invalid:
        issues.append(QualityIssue(severity="warning", area="Vizualizace",
                                   message=f"{len(invalid)} vizualizací neprošlo validací a nebylo vykresleno – "
                                           "zkuste jiný typ nebo nové vygenerování."))
    if not results.flashcards and not results.quiz and not results.summary:
        issues.append(QualityIssue(severity="info", area="Výstupy", message="Zatím nebyly vytvořeny studijní výstupy."))
    for w in results.analysis_warnings[:30]:
        issues.append(QualityIssue(severity="info", area="Zdroj", message=w))
    return issues
