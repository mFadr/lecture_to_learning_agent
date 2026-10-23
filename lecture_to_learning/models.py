"""Pydantic datové modely celé aplikace.

Každá položka odvozená z PDF nese identifikaci zdroje (`SourceRef`) a původ
(`Origin`), aby se nezaměňoval obsah přednášky, odvozené závěry a doplňující
znalosti modelu.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Origin(str, Enum):
    SOURCE = "source"  # výslovně uvedeno v PDF
    DERIVED = "derived"  # odvozeno/shrnuto z obsahu PDF
    MODEL = "model"  # doplňující znalost modelu (externí vysvětlení)


class SourceRef(BaseModel):
    doc_id: str
    pages: list[int] = Field(default_factory=list)  # 1-based čísla stran
    quote: str | None = None
    quote_verified: bool = False  # citace byla nalezena v textu stránky

    def label(self, doc_names: dict[str, str] | None = None) -> str:
        name = (doc_names or {}).get(self.doc_id, "")
        pages = format_pages(self.pages)
        return f"{name} s. {pages}".strip() if pages else name


def format_pages(pages: list[int]) -> str:
    if not pages:
        return ""
    pages = sorted(set(pages))
    parts: list[str] = []
    start = prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
            continue
        parts.append(f"{start}–{prev}" if start != prev else str(start))
        start = prev = p
    parts.append(f"{start}–{prev}" if start != prev else str(start))
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Dokumenty a stránky
# ---------------------------------------------------------------------------


class DocStatus(str, Enum):
    NEW = "new"
    EXTRACTED = "extracted"
    ANALYZED = "analyzed"
    ERROR = "error"


class PageQuality(str, Enum):
    OK = "ok"
    LOW_TEXT = "low_text"  # podezřele málo textu
    NO_TEXT = "no_text"  # bez textové vrstvy (sken / prázdná)
    OCR = "ocr"  # text získaný OCR


class Block(BaseModel):
    kind: Literal["heading", "paragraph", "bullet", "table", "formula"]
    text: str
    level: int = 0


class Page(BaseModel):
    number: int  # 1-based
    text: str
    blocks: list[Block] = Field(default_factory=list)
    quality: PageQuality = PageQuality.OK
    has_images: bool = False
    table_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class DocumentExtraction(BaseModel):
    doc_id: str
    filename: str
    content_hash: str
    page_count: int
    title: str = ""
    pages: list[Page]
    warnings: list[str] = Field(default_factory=list)
    extractor_version: str = "1"

    def page(self, number: int) -> Page | None:
        if 1 <= number <= len(self.pages) and self.pages[number - 1].number == number:
            return self.pages[number - 1]
        return next((p for p in self.pages if p.number == number), None)


class DocumentRef(BaseModel):
    id: str = Field(default_factory=new_id)
    path: str  # absolutní cesta k originálu (nebo ke kopii v source/)
    filename: str
    content_hash: str = ""
    page_count: int = 0
    status: DocStatus = DocStatus.NEW
    page_from: int | None = None
    page_to: int | None = None
    error: str | None = None
    added_at: datetime = Field(default_factory=utcnow)

    def selected_pages(self) -> range:
        start = max(1, self.page_from or 1)
        end = min(self.page_count, self.page_to or self.page_count)
        return range(start, end + 1)


# ---------------------------------------------------------------------------
# Porozumění přednášce
# ---------------------------------------------------------------------------


class Term(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    definition: str  # definice podle zdroje
    simple_explanation: str = ""  # jednodušší vysvětlení (odvozené)
    example: str = ""
    related: list[str] = Field(default_factory=list)
    source: SourceRef
    origin: Origin = Origin.SOURCE
    topic: str = ""


class Formula(BaseModel):
    id: str = Field(default_factory=new_id)
    expression: str
    meaning: str = ""
    variables: dict[str, str] = Field(default_factory=dict)
    source: SourceRef
    topic: str = ""


class Relation(BaseModel):
    source_concept: str
    target_concept: str
    kind: str = "souvisí"  # např. způsobuje, je součástí, je typem, kontrast
    description: str = ""
    source: SourceRef


class VizType(str, Enum):
    FLOWCHART = "flowchart"
    TIMELINE = "timeline"
    STATE = "state"
    CAUSAL = "causal"
    TREE = "tree"
    MINDMAP = "mindmap"
    COMPARISON = "comparison"
    VENN = "venn"
    BAR = "bar"
    LINE = "line"
    SCATTER = "scatter"
    HISTOGRAM = "histogram"
    TABLE = "table"
    FUNCTION = "function"
    CONCEPT = "concept"

    @property
    def label(self) -> str:
        return VIZ_LABELS[self]


VIZ_LABELS: dict[VizType, str] = {
    VizType.FLOWCHART: "Vývojový diagram / kroky",
    VizType.TIMELINE: "Časová osa",
    VizType.STATE: "Diagram stavů",
    VizType.CAUSAL: "Kauzální diagram",
    VizType.TREE: "Stromový / hierarchický diagram",
    VizType.MINDMAP: "Myšlenková mapa",
    VizType.COMPARISON: "Srovnávací matice",
    VizType.VENN: "Vennův diagram",
    VizType.BAR: "Sloupcový graf",
    VizType.LINE: "Spojnicový graf",
    VizType.SCATTER: "Bodový graf",
    VizType.HISTOGRAM: "Histogram",
    VizType.TABLE: "Tabulka výsledků",
    VizType.FUNCTION: "Graf matematické funkce",
    VizType.CONCEPT: "Konceptuální diagram",
}


class ExampleStatus(str, Enum):
    FOUND = "found"
    SELECTED = "selected"
    GENERATED = "generated"
    FAILED = "failed"


class Example(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    topic: str = ""
    subtopic: str = ""
    description: str  # popis podle zdroje
    concept: str = ""  # hlavní vysvětlovaný koncept
    actors: list[str] = Field(default_factory=list)  # objekty, proměnné, aktéři
    relations: list[str] = Field(default_factory=list)
    numeric_data: str = ""  # dostupná číselná data tak, jak jsou v textu
    source: SourceRef
    recommended_viz: VizType = VizType.CONCEPT
    viz_reason: str = ""
    status: ExampleStatus = ExampleStatus.FOUND
    status_note: str = ""


class Topic(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    summary: str = ""
    subtopics: list[str] = Field(default_factory=list)
    source: SourceRef
    order: int = 0


class ChunkAnalysis(BaseModel):
    """Výsledek analýzy jedné části dokumentu (validovaná odpověď LLM)."""

    topics: list[Topic] = Field(default_factory=list)
    terms: list[Term] = Field(default_factory=list)
    formulas: list[Formula] = Field(default_factory=list)
    examples: list[Example] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Studijní výstupy
# ---------------------------------------------------------------------------


class DetailLevel(str, Enum):
    BRIEF = "brief"
    STANDARD = "standard"
    DETAILED = "detailed"

    @property
    def label(self) -> str:
        return {"brief": "Stručné shrnutí", "standard": "Standardní poznámky",
                "detailed": "Detailní studijní materiál"}[self.value]


class SummaryPoint(BaseModel):
    text: str
    pages: list[int] = Field(default_factory=list)
    origin: Origin = Origin.DERIVED


class SummarySection(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    overview: str = ""
    explanation: list[SummaryPoint] = Field(default_factory=list)
    key_definitions: list[SummaryPoint] = Field(default_factory=list)
    formulas: list[SummaryPoint] = Field(default_factory=list)
    examples: list[SummaryPoint] = Field(default_factory=list)
    connections: list[SummaryPoint] = Field(default_factory=list)
    pitfalls: list[SummaryPoint] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    source: SourceRef


class Summary(BaseModel):
    detail: DetailLevel = DetailLevel.STANDARD
    sections: list[SummarySection] = Field(default_factory=list)


class CardKind(str, Enum):
    DEFINITION = "definition"
    FORMULA = "formula"
    DIFFERENCE = "difference"
    CAUSAL = "causal"
    APPLICATION = "application"
    EXAMPLE = "example"


class Flashcard(BaseModel):
    id: str = Field(default_factory=new_id)
    question: str
    answer: str
    kind: CardKind = CardKind.DEFINITION
    topic: str = ""
    source: SourceRef


class QuestionKind(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT = "short"
    EXPLAIN = "explain"
    APPLICATION = "application"
    EXAMPLE = "example"

    @property
    def label(self) -> str:
        return {"multiple_choice": "Výběr z možností", "true_false": "Pravda/nepravda",
                "short": "Krátká odpověď", "explain": "Vysvětlení principu",
                "application": "Aplikační úloha", "example": "Otázka k příkladu"}[self.value]


class QuizQuestion(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: QuestionKind
    question: str
    options: list[str] = Field(default_factory=list)
    correct_answer: str
    explanation: str = ""
    topic: str = ""
    source: SourceRef

    @field_validator("options")
    @classmethod
    def _strip_options(cls, v: list[str]) -> list[str]:
        return [o.strip() for o in v if o and o.strip()]


# ---------------------------------------------------------------------------
# Vizualizace
# ---------------------------------------------------------------------------


class VizNode(BaseModel):
    id: str
    label: str
    group: str = ""


class VizEdge(BaseModel):
    source: str
    target: str
    label: str = ""
    sign: Literal["+", "-", ""] = ""  # pro kauzální diagramy


class VizEvent(BaseModel):
    when: str
    label: str


class VizTreeNode(BaseModel):
    label: str
    children: list["VizTreeNode"] = Field(default_factory=list)


class VizSeries(BaseModel):
    name: str = ""
    x: list[float | str] = Field(default_factory=list)
    y: list[float] = Field(default_factory=list)


class VizSet(BaseModel):
    label: str
    items: list[str] = Field(default_factory=list)


class VizSpec(BaseModel):
    """Datová specifikace vizualizace. Vykresluje se jen kontrolovanými šablonami."""

    viz_type: VizType
    title: str
    explanation: str = ""  # co má student pochopit
    nodes: list[VizNode] = Field(default_factory=list)
    edges: list[VizEdge] = Field(default_factory=list)
    events: list[VizEvent] = Field(default_factory=list)
    tree: VizTreeNode | None = None
    columns: list[str] = Field(default_factory=list)  # srovnání / tabulka
    rows: list[list[str]] = Field(default_factory=list)
    series: list[VizSeries] = Field(default_factory=list)
    sets: list[VizSet] = Field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    expression: str = ""  # pro graf funkce, např. "2*x**2 - 3"
    x_min: float | None = None
    x_max: float | None = None
    illustrative_values: bool = False  # hodnoty jsou modelové, ne ze zdroje
    notes: list[str] = Field(default_factory=list)


class Visualization(BaseModel):
    id: str = Field(default_factory=new_id)
    example_id: str | None = None
    topic: str = ""
    spec: VizSpec
    source: SourceRef
    version: int = 1
    svg_path: str | None = None  # relativně ke složce projektu
    png_path: str | None = None
    mermaid: str | None = None
    validation_warnings: list[str] = Field(default_factory=list)
    valid: bool = True
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Projekt a běh agenta
# ---------------------------------------------------------------------------


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"  # krok doběhl jen částečně – výstup není kompletní
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class StepRecord(BaseModel):
    name: str
    status: StepStatus = StepStatus.PENDING
    message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class QualityIssue(BaseModel):
    severity: Literal["info", "warning", "error"] = "warning"
    area: str
    message: str


class StudyResults(BaseModel):
    """Všechny výstupy projektu – ukládá se do study_materials/results.json."""

    topics: list[Topic] = Field(default_factory=list)
    terms: list[Term] = Field(default_factory=list)
    formulas: list[Formula] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    examples: list[Example] = Field(default_factory=list)
    summary: Summary | None = None
    flashcards: list[Flashcard] = Field(default_factory=list)
    quiz: list[QuizQuestion] = Field(default_factory=list)
    visualizations: list[Visualization] = Field(default_factory=list)
    topic_map_svg: str | None = None  # relativní cesta v projektu
    relation_map_svg: str | None = None
    cross_document_topics: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[StepRecord] = Field(default_factory=list)
    issues: list[QualityIssue] = Field(default_factory=list)
    analysis_warnings: list[str] = Field(default_factory=list)
    llm_label: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class Project(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    documents: list[DocumentRef] = Field(default_factory=list)
    copy_sources: bool = False

    def doc(self, doc_id: str) -> DocumentRef | None:
        return next((d for d in self.documents if d.id == doc_id), None)

    def doc_names(self) -> dict[str, str]:
        return {d.id: d.filename for d in self.documents}


VizTreeNode.model_rebuild()
