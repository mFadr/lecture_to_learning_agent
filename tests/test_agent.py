"""Testy workflow agenta s testovacím LLM (bez sítě)."""

import threading
from pathlib import Path

from lecture_to_learning.agent.orchestrator import (ConsentRequired, Orchestrator, RunOptions, normalize_question)
from lecture_to_learning.config import LLMMode, Settings
from lecture_to_learning.llm.factory import ProviderRouter
from lecture_to_learning.models import ExampleStatus, Origin, QuestionKind, StepStatus, VizType

from .conftest import ScriptedProvider


def _setup(store, settings, sample_pdf, provider=None):
    project = store.create_project("Ekonomie – test")
    store.add_document(project, sample_pdf)
    provider = provider or ScriptedProvider()
    router = ProviderRouter(settings, local=provider, cloud=provider)
    return project, provider, Orchestrator(store, project, settings, router)


def test_full_workflow(store, settings, sample_pdf):
    project, provider, orch = _setup(store, settings, sample_pdf)
    res = orch.run(RunOptions())
    status = {s.name: s.status for s in res.steps}
    assert all(v in (StepStatus.DONE, StepStatus.PARTIAL) for v in status.values()), res.steps

    # analýza: témata, pojmy, ověřené citace, neexistující stránka odfiltrována
    assert [t.title for t in res.topics] == ["Poptávka", "Nabídka", "Tržní rovnováha", "Cenová elasticita poptávky"]
    demand = next(t for t in res.terms if t.name == "Poptávka")
    assert demand.source.pages == [1] and demand.source.quote_verified
    eq = next(t for t in res.terms if t.name == "Rovnovážná cena")
    assert eq.source.pages == [2]  # model uvedl s. 7, citace nalezena na s. 2

    # shrnutí
    assert res.summary and len(res.summary.sections) == 4
    s0 = res.summary.sections[0]
    assert s0.explanation[0].origin == Origin.SOURCE and s0.pitfalls[0].origin == Origin.MODEL
    assert res.summary.sections[3].source.pages == [3]  # stránka 99 neplatná → stránky tématu

    # flashcards: duplicita odstraněna, zdroj přiřazen
    assert len(res.flashcards) == 3 and res.flashcards[0].source.pages == [1]

    # test: neplatná otázka vyřazena, odpověď MC dohledána a TF normalizována
    assert len(res.quiz) == 3
    mc = next(q for q in res.quiz if q.kind == QuestionKind.MULTIPLE_CHOICE)
    assert mc.correct_answer == "S rostoucí cenou poptávané množství klesá" and mc.correct_answer in mc.options
    tf = next(q for q in res.quiz if q.kind == QuestionKind.TRUE_FALSE)
    assert tf.correct_answer == "Nepravda"


def test_example_identified_and_linked_to_source_page(store, settings, sample_pdf):
    project, provider, orch = _setup(store, settings, sample_pdf)
    res = orch.run(RunOptions(steps=["extract", "analyze"]))
    coffee = next(e for e in res.examples if "kávě" in e.title)
    assert coffee.source.pages == [1]
    assert coffee.recommended_viz == VizType.LINE
    assert "120" in coffee.numeric_data
    flow = next(e for e in res.examples if "Mechanismus" in e.title)
    assert flow.source.pages == [2] and flow.recommended_viz == VizType.FLOWCHART


def test_visualizations_generated_from_verified_data(store, settings, sample_pdf):
    project, provider, orch = _setup(store, settings, sample_pdf)
    res = orch.run(RunOptions(steps=["extract", "analyze", "visualize", "topic_map"], max_auto_viz=3))
    assert len(res.visualizations) == 3
    pdir = store.project_dir(project.id)
    for v in res.visualizations:
        assert v.valid, v.validation_warnings
        assert v.source.pages and (pdir / v.svg_path).exists()
        assert v.spec.explanation
    line = next(v for v in res.visualizations if v.spec.viz_type == VizType.LINE)
    assert (pdir / line.png_path).exists()
    flow = next(v for v in res.visualizations if v.spec.viz_type == VizType.FLOWCHART)
    assert flow.mermaid and "flowchart TD" in flow.mermaid
    svg = (pdir / flow.svg_path).read_text(encoding="utf-8")
    assert "Zdroj:" in svg and "s. 2" in svg  # propojení se zdrojovou stránkou
    assert all(e.status == ExampleStatus.GENERATED for e in res.examples)
    assert (pdir / res.topic_map_svg).exists() and (pdir / res.relation_map_svg).exists()


def test_fabricated_numbers_are_rejected(store, settings, sample_pdf):
    fake = {"title": "Káva", "explanation": "x", "series": [{"x": [30, 40], "y": [500, 700]}],
            "x_label": "cena", "y_label": "ks"}
    provider = ScriptedProvider(overrides={"viz_line": fake})
    project, provider, orch = _setup(store, settings, sample_pdf, provider)
    res = orch.run(RunOptions(steps=["extract", "analyze"]))
    coffee = next(e for e in res.examples if "kávě" in e.title)
    viz = orch.visualize_example(res, coffee)
    assert not viz.valid and viz.svg_path is None
    assert any("nebyly nalezeny" in w for w in viz.validation_warnings)
    assert coffee.status == ExampleStatus.FAILED
    # nová verze s jiným typem (diagram) projde
    viz2 = orch.visualize_example(res, coffee, VizType.FLOWCHART)
    assert viz2.valid and viz2.version == 2


def test_model_unavailable_produces_no_fake_results(store, settings, sample_pdf):
    provider = ScriptedProvider(available=False)
    project, provider, orch = _setup(store, settings, sample_pdf, provider)
    res = orch.run(RunOptions())
    status = {s.name: s.status for s in res.steps}
    assert status["extract"] == StepStatus.DONE
    assert status["analyze"] == StepStatus.FAILED
    assert "není dostupný" in next(s.message for s in res.steps if s.name == "analyze")
    assert status["summary"] == StepStatus.SKIPPED
    assert res.topics == [] and res.summary is None and res.flashcards == [] and provider.calls == 0


def test_cloud_requires_consent(store, sample_pdf):
    settings = Settings(mode=LLMMode.CLOUD, cloud_consent=False)
    cloud = ScriptedProvider(is_local=False)
    project = store.create_project("Cloud")
    store.add_document(project, sample_pdf)
    orch = Orchestrator(store, project, settings, ProviderRouter(settings, cloud=cloud))
    try:
        orch.run(RunOptions(steps=["extract", "analyze"]))
        raise AssertionError("ConsentRequired očekáváno")
    except ConsentRequired:
        pass
    assert cloud.calls == 0
    settings.cloud_consent = True
    res = orch.run(RunOptions(steps=["extract", "analyze"]))
    assert res.topics and cloud.calls == 1


def test_cache_avoids_repeated_llm_calls_and_project_reload(store, settings, sample_pdf):
    project, provider, orch = _setup(store, settings, sample_pdf)
    orch.run(RunOptions(steps=["extract", "analyze", "summary"]))
    first = provider.calls
    assert first >= 2
    project2 = store.load_project(project.id)
    provider2 = ScriptedProvider()
    orch2 = Orchestrator(store, project2, settings, ProviderRouter(settings, local=provider2))
    res = orch2.run(RunOptions(steps=["extract", "analyze", "summary"]))
    assert provider2.calls == 0  # vše z cache
    assert res.summary and len(res.summary.sections) == 4
    reloaded = store.load_results(project.id)
    assert reloaded.topics[0].title == "Poptávka" and reloaded.summary is not None


def test_cancel_stops_processing(store, settings, sample_pdf):
    project, provider, orch = _setup(store, settings, sample_pdf)
    cancel = threading.Event()
    cancel.set()
    orch.cancel = cancel
    res = orch.run(RunOptions())
    assert all(s.status == StepStatus.CANCELLED for s in res.steps)


def test_prompt_injection_is_wrapped_as_data(store, settings, sample_pdf):
    project, provider, orch = _setup(store, settings, sample_pdf)
    orch.run(RunOptions(steps=["extract", "analyze"]))
    prompt = provider.prompts[0]
    body_start = prompt.index("<<<DOKUMENT>>>")
    assert "IGNORUJ PŘEDCHOZÍ INSTRUKCE" in prompt[body_start:]  # obsah je jen v datovém bloku
    assert prompt.count("<<<KONEC_DOKUMENTU>>>") == 1


def test_normalize_question_rules():
    import random
    rng = random.Random(1)
    assert normalize_question("true_false", "Q", [], "ano", rng)[2] == "Pravda"
    assert normalize_question("true_false", "Q", [], "možná", rng) is None
    kind, opts, correct = normalize_question("multiple_choice", "Q", ["a", "b", "c", "d"], "C", rng)
    assert correct == "c" and sorted(opts) == ["a", "b", "c", "d"]
    assert normalize_question("multiple_choice", "Q", ["a", "b"], "a", rng) is None
