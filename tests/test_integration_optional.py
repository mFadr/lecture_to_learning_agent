"""Volitelné integrační testy proti skutečnému LLM. Standardní sada je přeskakuje.

  set L2L_RUN_OLLAMA_TESTS=1   (+ volitelně L2L_OLLAMA_MODEL=llama3.1:8b)
  set L2L_RUN_CLOUD_TESTS=1    (+ L2L_API_KEY, volitelně L2L_CLOUD_MODEL, L2L_CLOUD_BASE_URL)
"""

import os

import pytest

from lecture_to_learning.agent.orchestrator import Orchestrator, RunOptions
from lecture_to_learning.config import LLMMode, Settings
from lecture_to_learning.llm.factory import ProviderRouter
from lecture_to_learning.models import StepStatus


def _run(store, settings, sample_pdf):
    project = store.create_project("Integrace")
    store.add_document(project, sample_pdf)
    res = Orchestrator(store, project, settings, ProviderRouter(settings)).run(RunOptions(max_auto_viz=2))
    status = {s.name: s.status for s in res.steps}
    assert status["analyze"] in (StepStatus.DONE, StepStatus.PARTIAL), res.steps
    assert res.topics and res.terms
    assert any(1 in t.source.pages for t in res.terms)  # definice poptávky je na s. 1
    return res


@pytest.mark.ollama
@pytest.mark.skipif(os.environ.get("L2L_RUN_OLLAMA_TESTS") != "1", reason="vyžaduje běžící Ollamu")
def test_real_ollama(store, sample_pdf):
    s = Settings(mode=LLMMode.LOCAL)
    s.ollama.model = os.environ.get("L2L_OLLAMA_MODEL", s.ollama.model)
    _run(store, s, sample_pdf)


@pytest.mark.cloud
@pytest.mark.skipif(os.environ.get("L2L_RUN_CLOUD_TESTS") != "1", reason="volitelný placený test cloudového API")
def test_real_cloud(store, sample_pdf):
    s = Settings(mode=LLMMode.CLOUD, cloud_consent=True, cloud_max_calls_per_run=20)
    s.cloud.model = os.environ.get("L2L_CLOUD_MODEL", s.cloud.model)
    s.cloud.base_url = os.environ.get("L2L_CLOUD_BASE_URL", s.cloud.base_url)
    _run(store, s, sample_pdf)
