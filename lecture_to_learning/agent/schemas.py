"""Schémata odpovědí LLM. Jsou tolerantní k drobným odchylkám modelů (např. stránky jako
řetězec "3-4"), ale výsledek je vždy převeden do přesných doménových modelů."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


def _pages(v: Any) -> list[int]:
    if v is None or v == "":
        return []
    if isinstance(v, (int, float)):
        return [int(v)]
    if isinstance(v, str):
        out: list[int] = []
        for a, b in re.findall(r"(\d+)\s*(?:[-–]\s*(\d+))?", v):
            lo, hi = int(a), int(b or a)
            if hi >= lo and hi - lo < 200:
                out.extend(range(lo, hi + 1))
        return out
    if isinstance(v, (list, tuple)):
        out = []
        for item in v:
            out.extend(_pages(item))
        return out
    return []


def _strlist(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        parts = re.split(r"[;\n]|,\s(?=\S)", v) if len(v) > 0 else []
        return [p.strip() for p in parts if p.strip()]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() if not isinstance(x, dict) else
                " – ".join(str(y) for y in x.values()) for x in v if x not in (None, "")]
    return [str(v)]


def _str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {x}" for k, x in v.items())
    return str(v).strip()


def _strdict(v: Any) -> dict[str, str]:
    if not v:
        return {}
    if isinstance(v, dict):
        return {str(k): _str(x) for k, x in v.items()}
    if isinstance(v, list):
        out = {}
        for item in v:
            if isinstance(item, dict):
                name = item.get("name") or item.get("symbol") or item.get("variable") or ""
                out[str(name)] = _str(item.get("meaning") or item.get("description") or "")
            elif isinstance(item, str) and ":" in item:
                k, _, x = item.partition(":")
                out[k.strip()] = x.strip()
        return out
    return {}


Pages = Annotated[list[int], BeforeValidator(_pages)]
StrList = Annotated[list[str], BeforeValidator(_strlist)]
Str = Annotated[str, BeforeValidator(_str)]
StrDict = Annotated[dict[str, str], BeforeValidator(_strdict)]


class TopicOut(BaseModel):
    title: Str
    summary: Str = ""
    subtopics: StrList = Field(default_factory=list)
    pages: Pages = Field(default_factory=list)


class TermOut(BaseModel):
    name: Str
    definition: Str = ""
    quote: Str = ""
    simple_explanation: Str = ""
    example: Str = ""
    related: StrList = Field(default_factory=list)
    topic: Str = ""
    pages: Pages = Field(default_factory=list)


class FormulaOut(BaseModel):
    expression: Str
    meaning: Str = ""
    variables: StrDict = Field(default_factory=dict)
    topic: Str = ""
    pages: Pages = Field(default_factory=list)


class ExampleOut(BaseModel):
    title: Str
    topic: Str = ""
    subtopic: Str = ""
    description: Str = ""
    concept: Str = ""
    actors: StrList = Field(default_factory=list)
    relations: StrList = Field(default_factory=list)
    numeric_data: Str = ""
    viz_type: Str = "concept"
    viz_reason: Str = ""
    pages: Pages = Field(default_factory=list)


class RelationOut(BaseModel):
    source: Str = Field(alias="from", default="")
    target: Str = Field(alias="to", default="")
    kind: Str = "souvisí"
    description: Str = ""
    pages: Pages = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AnalysisOut(BaseModel):
    topics: list[TopicOut] = Field(default_factory=list)
    terms: list[TermOut] = Field(default_factory=list)
    formulas: list[FormulaOut] = Field(default_factory=list)
    examples: list[ExampleOut] = Field(default_factory=list)
    relations: list[RelationOut] = Field(default_factory=list)
    unclear: StrList = Field(default_factory=list)


class PointOut(BaseModel):
    text: Str
    pages: Pages = Field(default_factory=list)
    origin: Str = "derived"


def _points(v: Any) -> Any:
    if isinstance(v, str):
        return [{"text": v}] if v.strip() else []
    if isinstance(v, list):
        return [{"text": x} if isinstance(x, str) else x for x in v]
    return v or []


Points = Annotated[list[PointOut], BeforeValidator(_points)]


class SectionOut(BaseModel):
    title: Str
    overview: Str = ""
    explanation: Points = Field(default_factory=list)
    key_definitions: Points = Field(default_factory=list)
    formulas: Points = Field(default_factory=list)
    examples: Points = Field(default_factory=list)
    connections: Points = Field(default_factory=list)
    pitfalls: Points = Field(default_factory=list)
    missing_info: StrList = Field(default_factory=list)
    pages: Pages = Field(default_factory=list)


class SummaryOut(BaseModel):
    sections: list[SectionOut] = Field(default_factory=list)


class CardOut(BaseModel):
    question: Str
    answer: Str
    kind: Str = "definition"
    topic: Str = ""
    pages: Pages = Field(default_factory=list)


class CardsOut(BaseModel):
    cards: list[CardOut] = Field(default_factory=list)


class QuestionOut(BaseModel):
    kind: Str = "short"
    question: Str
    options: StrList = Field(default_factory=list)
    correct_answer: Str = ""
    explanation: Str = ""
    topic: Str = ""
    pages: Pages = Field(default_factory=list)


class QuizOut(BaseModel):
    questions: list[QuestionOut] = Field(default_factory=list)
