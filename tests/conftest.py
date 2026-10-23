"""Společné fixtures. Testy nevolají žádné placené ani síťové API.

`ScriptedProvider` je testovací dvojník LLM: vrací předem připravené odpovědi, které odpovídají
obsahu syntetické přednášky (lecture_to_learning.sample_pdf). V aplikaci se nepoužívá."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lecture_to_learning.config import Settings  # noqa: E402
from lecture_to_learning.llm.base import LLMProvider, LLMResponse, Message, ProviderStatus  # noqa: E402
from lecture_to_learning.sample_pdf import make_sample_pdf, make_scanned_pdf  # noqa: E402
from lecture_to_learning.storage import ProjectStore  # noqa: E402

ANALYSIS = {
    "topics": [
        {"title": "Poptávka", "summary": "Definice poptávky a zákon klesající poptávky.", "subtopics": ["Zákon klesající poptávky"], "pages": [1]},
        {"title": "Nabídka", "summary": "Definice nabídky.", "subtopics": [], "pages": "2"},
        {"title": "Tržní rovnováha", "summary": "Mechanismus přizpůsobení trhu.", "subtopics": ["Převis poptávky"], "pages": [2]},
        {"title": "Cenová elasticita poptávky", "summary": "Měření citlivosti poptávky.", "subtopics": [], "pages": [3]},
    ],
    "terms": [
        {"name": "Poptávka", "definition": "Množství statku, které jsou kupující ochotni a schopni koupit při různých cenách.",
         "quote": "Poptávka je množství statku, které jsou kupující ochotni a schopni koupit při různých cenách.",
         "simple_explanation": "Kolik lidé chtějí koupit za jakou cenu.", "related": ["Nabídka"], "topic": "Poptávka", "pages": [1]},
        {"name": "Nabídka", "definition": "Množství statku, které jsou výrobci ochotni prodat při různých cenách.",
         "quote": "Nabídka je množství statku, které jsou výrobci ochotni prodat při různých cenách.", "pages": [2]},
        {"name": "Rovnovážná cena", "definition": "Cena, při které se nabízené a poptávané množství rovnají.",
         "quote": "Rovnovážná cena je cena, při které se nabízené a poptávané množství rovnají.", "pages": [7]},
    ],
    "formulas": [{"expression": "Ed = (ΔQ / Q) / (ΔP / P)", "meaning": "cenová elasticita poptávky",
                  "variables": {"ΔQ": "změna množství", "P": "výchozí cena"}, "topic": "Cenová elasticita poptávky", "pages": [3]}],
    "examples": [
        {"title": "Poptávka po kávě v menze", "topic": "Poptávka", "description": "Prodej kávy při různých cenách.",
         "concept": "klesající poptávková křivka", "actors": ["cena", "prodané množství"],
         "numeric_data": "30 Kč: 120, 40 Kč: 90, 50 Kč: 60, 60 Kč: 35", "viz_type": "line",
         "viz_reason": "číselná závislost množství na ceně", "pages": [1]},
        {"title": "Mechanismus přizpůsobení trhu", "topic": "Tržní rovnováha", "description": "Čtyři kroky od převisu poptávky k rovnováze.",
         "concept": "tržní rovnováha", "viz_type": "flowchart", "pages": [2]},
        {"title": "Elastická vs. neelastická poptávka", "topic": "Cenová elasticita poptávky",
         "description": "Srovnání luxusních a nezbytných statků.", "viz_type": "comparison", "pages": [3]},
    ],
    "relations": [{"from": "Cena", "to": "Poptávané množství", "kind": "snižuje", "pages": [1]},
                  {"from": "Převis poptávky", "to": "Cena", "kind": "zvyšuje", "pages": [2]}],
    "unclear": [],
}

SUMMARY = {"sections": [
    {"title": "Poptávka", "overview": "Poptávka popisuje vztah mezi cenou a množstvím, které kupující chtějí koupit.",
     "explanation": [{"text": "S rostoucí cenou poptávané množství klesá.", "pages": [1], "origin": "source"}],
     "key_definitions": [{"text": "Poptávka – množství statku, které jsou kupující ochotni koupit.", "pages": [1], "origin": "source"}],
     "examples": [{"text": "Káva v menze: při 30 Kč 120 káv, při 60 Kč 35 káv.", "pages": [1], "origin": "source"}],
     "pitfalls": [{"text": "Nezaměňujte poptávku s poptávaným množstvím.", "pages": [], "origin": "model"}],
     "pages": [1]},
    {"title": "Nabídka", "overview": "Nabídka vyjadřuje ochotu výrobců prodávat za různé ceny.",
     "explanation": ["Vyšší cena motivuje výrobce zvyšovat nabízené množství."], "pages": [2]},
    {"title": "Tržní rovnováha", "overview": "Trh se přizpůsobuje přes cenu, dokud se nabídka a poptávka nevyrovnají.",
     "explanation": [{"text": "Při převisu poptávky cena roste.", "pages": [2], "origin": "source"}], "pages": [2]},
    {"title": "Cenová elasticita poptávky", "overview": "Elasticita měří citlivost množství na cenu.",
     "formulas": [{"text": "Ed = (ΔQ/Q)/(ΔP/P)", "pages": [3], "origin": "source"}], "pages": [99]},
]}

CARDS = {"cards": [
    {"question": "Co je poptávka?", "answer": "Množství statku, které jsou kupující ochotni a schopni koupit při různých cenách.", "kind": "definition", "topic": "Poptávka", "pages": [1]},
    {"question": "Jak se počítá cenová elasticita poptávky?", "answer": "Ed = (ΔQ/Q)/(ΔP/P)", "kind": "formula", "pages": [3]},
    {"question": "Co je poptávka?", "answer": "duplicitní", "pages": [1]},
    {"question": "Co se stane s cenou při převisu poptávky?", "answer": "Cena roste.", "kind": "causal", "pages": [2]},
]}

QUIZ = {"questions": [
    {"kind": "multiple_choice", "question": "Co platí podle zákona klesající poptávky?",
     "options": ["A) S rostoucí cenou poptávané množství klesá", "B) S rostoucí cenou roste poptávka",
                 "C) Cena nemá vliv", "D) Množství roste vždy"],
     "correct_answer": "A", "explanation": "Zákon klesající poptávky.", "pages": [1]},
    {"kind": "true_false", "question": "Při převisu poptávky cena klesá.", "correct_answer": "false", "pages": [2]},
    {"kind": "multiple_choice", "question": "Neplatná otázka", "options": ["x", "y", "z"], "correct_answer": "w"},
    {"kind": "short", "question": "Definujte rovnovážnou cenu.", "correct_answer": "Cena, při které se nabízené a poptávané množství rovnají.", "pages": [2]},
]}

VIZ = {
    "line": {"title": "Poptávka po kávě", "explanation": "S rostoucí cenou klesá prodané množství.",
             "series": [{"name": "káva", "x": [30, 40, 50, 60], "y": [120, 90, 60, 35]}],
             "x_label": "cena [Kč]", "y_label": "počet káv za den", "illustrative_values": False},
    "flowchart": {"title": "Přizpůsobení trhu", "explanation": "Cena se mění, dokud trh nedosáhne rovnováhy.",
                  "nodes": [{"id": "a", "label": "Převis poptávky"}, {"id": "b", "label": "Cena roste"},
                            {"id": "c", "label": "Qd klesá, Qs roste"}, {"id": "d", "label": "Rovnováha"}],
                  "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}, {"source": "c", "target": "d"}]},
    "comparison": {"title": "Elastická vs. neelastická", "explanation": "Rozdíly mezi typy poptávky.",
                   "columns": ["Vlastnost", "Elastická", "Neelastická"],
                   "rows": [["Typ statku", "luxusní", "nezbytný"], ["Substituty", "mnoho", "málo"]]},
}


class ScriptedProvider(LLMProvider):
    """Testovací LLM: odpovídá podle typu promptu předem připraveným JSONem."""

    name = "Scripted"

    def __init__(self, *, is_local: bool = True, overrides: dict | None = None, available: bool = True,
                 max_request_chars: int = 9000):
        super().__init__("scripted-model", max_request_chars=max_request_chars)
        self.is_local = is_local
        self.overrides = overrides or {}
        self.available = available
        self.prompts: list[str] = []
        self._lock = threading.Lock()

    def check(self) -> ProviderStatus:
        return ProviderStatus(self.available, "ok" if self.available else "Model není dostupný (test).")

    def chat(self, messages: list[Message], *, json_output: bool = False, cancel=None) -> LLMResponse:
        user = messages[1].content if len(messages) > 1 else messages[0].content
        with self._lock:
            self.calls += 1
            self.prompts.append(user)
        if "Analyzuj následující část" in user:
            data = self.overrides.get("analysis", ANALYSIS)
        elif "Vytvoř studijní shrnutí" in user:
            data = self.overrides.get("summary", SUMMARY)
        elif "kartiček" in user:
            data = self.overrides.get("cards", CARDS)
        elif "procvičovacího testu" in user:
            data = self.overrides.get("quiz", QUIZ)
        elif "datovou specifikaci vizualizace" in user:
            vt = user.split('typu "')[1].split('"')[0]
            data = self.overrides.get(f"viz_{vt}", VIZ.get(vt, {}))
        else:
            data = {}
        text = data if isinstance(data, str) else "Zde je JSON:\n```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"
        return LLMResponse(text=text, model=self.model)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    return make_sample_pdf(tmp_path / "přednáška s mezerou.pdf")


@pytest.fixture
def scanned_pdf(tmp_path: Path) -> Path:
    return make_scanned_pdf(tmp_path / "sken.pdf")


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path / "data")


@pytest.fixture
def settings() -> Settings:
    return Settings()
