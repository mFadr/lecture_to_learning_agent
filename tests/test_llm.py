import json

import httpx
import pytest

from lecture_to_learning.agent.schemas import AnalysisOut, QuizOut
from lecture_to_learning.config import LLMMode, Settings
from lecture_to_learning.llm.base import LLMAuthError, LLMBudgetExceeded, LLMInvalidResponse, LLMResponse, LLMUnavailable, Message
from lecture_to_learning.llm.factory import BudgetedProvider, ProviderRouter
from lecture_to_learning.llm.json_utils import ask_json, extract_json, parse_model
from lecture_to_learning.llm.ollama import OllamaProvider
from lecture_to_learning.llm.openai_compat import OpenAICompatProvider

from .conftest import ScriptedProvider

# ------------------------------------------------------------------ JSON


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Tady:\n```json\n{"a": [1, 2,]}\n```') == {"a": [1, 2]}
    assert extract_json('blabla {"a": {"b": "x}"}} konec') == {"a": {"b": "x}"}}
    assert extract_json('{"a": [1, 2') == {"a": [1, 2]}  # useknutá odpověď
    with pytest.raises(LLMInvalidResponse):
        extract_json("žádný json")


def test_schema_is_lenient_but_typed():
    out = parse_model(json.dumps({"topics": [{"title": "T", "pages": "3-5, 7"}],
                                  "terms": [{"name": "X", "definition": "d", "related": "A; B", "pages": [1, "2"]}],
                                  "relations": [{"from": "a", "to": "b"}]}), AnalysisOut)
    assert out.topics[0].pages == [3, 4, 5, 7]
    assert out.terms[0].related == ["A", "B"] and out.terms[0].pages == [1, 2]
    assert out.relations[0].source == "a"


def test_invalid_structure_rejected():
    with pytest.raises(LLMInvalidResponse):
        parse_model('{"questions": [{"kind": "short"}]}', QuizOut)  # chybí question


class _Flaky(ScriptedProvider):
    def __init__(self):
        super().__init__()
        self.n = 0

    def chat(self, messages, *, json_output=False, cancel=None):
        self.n += 1
        if self.n == 1:
            return LLMResponse(text="Promiň, tady nic.", model="x")
        assert "nebyla platná" in messages[-1].content  # požadavek na opravu
        return LLMResponse(text='{"questions": []}', model="x")


def test_ask_json_repairs_invalid_answer():
    p = _Flaky()
    out = ask_json(p, "sys", "user", QuizOut)
    assert out.questions == [] and p.n == 2


# --------------------------------------------------------------- Ollama


def _ollama_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
    if request.url.path == "/api/chat":
        body = json.loads(request.content)
        assert body["format"] == "json" and body["options"]["num_ctx"] == 8192
        lines = [{"message": {"content": '{"ok": '}, "done": False},
                 {"message": {"content": "true}"}, "done": False},
                 {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 5}]
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))
    return httpx.Response(404)


def test_ollama_provider_streaming_and_check():
    p = OllamaProvider("llama3.1:8b")
    p.transport = httpx.MockTransport(_ollama_handler)
    assert p.check().ok
    r = p.chat([Message("user", "hi")], json_output=True)
    assert json.loads(r.text) == {"ok": True} and r.completion_tokens == 5
    p.model = "neexistuje"
    st = p.check()
    assert not st.ok and "ollama pull neexistuje" in st.message


def test_ollama_unavailable_gives_clear_message():
    p = OllamaProvider("llama3.1:8b", "http://127.0.0.1:9", timeout_s=2)
    st = p.check()
    assert not st.ok and "Ollama neběží" in st.message
    with pytest.raises(LLMUnavailable):
        p.chat([Message("user", "x")])


# --------------------------------------------------------------- OpenAI


def test_openai_compat_sse_and_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer sk-test-123456789":
            return httpx.Response(401, json={"error": "bad key"})
        body = json.loads(request.content)
        seen.update(body)
        sse = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in ['{"a"', ': 1}'])
        sse += 'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=sse)

    p = OpenAICompatProvider("gpt-test", "https://example.invalid/v1", "sk-test-123456789")
    p.transport = httpx.MockTransport(handler)
    r = p.chat([Message("user", "x")], json_output=True)
    assert json.loads(r.text) == {"a": 1}
    assert seen["response_format"] == {"type": "json_object"}
    assert "sk-test" not in repr(p)

    bad = OpenAICompatProvider("gpt-test", "https://example.invalid/v1", "sk-wrong-000000000")
    bad.transport = httpx.MockTransport(handler)
    with pytest.raises(LLMAuthError):
        bad.chat([Message("user", "x")])
    with pytest.raises(LLMAuthError):
        OpenAICompatProvider("m", "https://example.invalid/v1", None).chat([Message("user", "x")])


def test_openai_adapts_unsupported_parameter():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'temperature'"}})
        return httpx.Response(200, text='data: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: [DONE]\n\n')

    p = OpenAICompatProvider("m", "https://example.invalid/v1", "sk-xxxxxxxxxxxx")
    p.transport = httpx.MockTransport(handler)
    assert p.chat([Message("user", "x")]).text == "ok"
    assert "temperature" not in calls[-1]


def test_rate_limit_is_retried(monkeypatch):
    n = {"c": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["c"] += 1
        if n["c"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})
        return httpx.Response(200, text='data: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: [DONE]\n\n')

    monkeypatch.setattr("lecture_to_learning.llm.http_util.time.sleep", lambda s: None)
    p = OpenAICompatProvider("m", "https://example.invalid/v1", "sk-xxxxxxxxxxxx")
    p.transport = httpx.MockTransport(handler)
    assert p.chat([Message("user", "x")]).text == "ok" and n["c"] == 2


# ----------------------------------------------------- přepínání režimů


def test_router_switches_between_local_cloud_hybrid():
    local, cloud = ScriptedProvider(is_local=True), ScriptedProvider(is_local=False)
    s = Settings(mode=LLMMode.LOCAL)
    r = ProviderRouter(s, local, cloud)
    assert r.for_step("summary") is local and not r.uses_cloud(["analyze", "summary"])
    s.mode = LLMMode.CLOUD
    r = ProviderRouter(s, local, cloud)
    assert r.for_step("analyze") is cloud and r.uses_cloud(["analyze"])
    s.mode = LLMMode.HYBRID
    s.hybrid_cloud_steps = ["summary"]
    r = ProviderRouter(s, local, cloud)
    assert r.for_step("analyze") is local and r.for_step("summary") is cloud


def test_budget_limits_cloud_calls():
    b = BudgetedProvider(ScriptedProvider(is_local=False), max_calls=1)
    b.chat([Message("system", "s"), Message("user", "u")])
    with pytest.raises(LLMBudgetExceeded):
        b.chat([Message("system", "s"), Message("user", "u")])
