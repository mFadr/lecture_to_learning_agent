"""Výběr poskytovatele LLM podle režimu (lokální / cloud / hybrid) a kroku workflow."""

from __future__ import annotations

import threading

from ..config import LLMMode, ProviderSettings, Settings
from .base import LLMBudgetExceeded, LLMError, LLMProvider, LLMResponse, Message, ProviderStatus
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider
from .secrets import get_api_key


def _common(ps: ProviderSettings) -> dict:
    return dict(temperature=ps.temperature, max_output_tokens=ps.max_output_tokens, timeout_s=ps.timeout_s,
                json_mode=ps.json_mode, max_request_chars=ps.max_request_chars)


def build_local(settings: Settings) -> OllamaProvider:
    ps = settings.ollama
    return OllamaProvider(ps.model, ps.base_url, context_tokens=ps.context_tokens, **_common(ps))


def build_cloud(settings: Settings, api_key: str | None = None) -> OpenAICompatProvider:
    ps = settings.cloud
    key = api_key if api_key is not None else get_api_key()[0]
    return OpenAICompatProvider(ps.model, ps.base_url, key, display_name=settings.cloud_provider_name,
                                **_common(ps))


class BudgetedProvider(LLMProvider):
    """Obal, který omezuje počet volání (ochrana nákladů u cloudového API)."""

    def __init__(self, inner: LLMProvider, max_calls: int):
        self.inner = inner
        self.max_calls = max_calls
        self.used = 0
        self._lock = threading.Lock()
        self.name = inner.name
        self.is_local = inner.is_local
        self.model = inner.model
        self.max_request_chars = inner.max_request_chars

    @property
    def label(self) -> str:
        return self.inner.label

    def chat(self, messages: list[Message], *, json_output: bool = False,
             cancel: threading.Event | None = None) -> LLMResponse:
        with self._lock:
            if self.max_calls and self.used >= self.max_calls:
                raise LLMBudgetExceeded(f"Dosažen limit {self.max_calls} volání cloudového API pro tento běh. "
                                        "Limit lze změnit v Nastavení.")
            self.used += 1
        return self.inner.chat(messages, json_output=json_output, cancel=cancel)

    def check(self) -> ProviderStatus:
        return self.inner.check()


class ProviderRouter:
    """Určuje, který poskytovatel obslouží daný krok workflow."""

    def __init__(self, settings: Settings, local: LLMProvider | None = None, cloud: LLMProvider | None = None):
        self.settings = settings
        self.mode = settings.mode
        self._local = local
        self._cloud = cloud

    @property
    def local(self) -> LLMProvider:
        if self._local is None:
            self._local = build_local(self.settings)
        return self._local

    @property
    def cloud(self) -> LLMProvider:
        if self._cloud is None:
            self._cloud = BudgetedProvider(build_cloud(self.settings), self.settings.cloud_max_calls_per_run)
        return self._cloud

    def for_step(self, step: str) -> LLMProvider:
        if self.mode == LLMMode.LOCAL:
            return self.local
        if self.mode == LLMMode.CLOUD:
            return self.cloud
        return self.cloud if step in self.settings.hybrid_cloud_steps else self.local

    def uses_cloud(self, steps: list[str]) -> bool:
        return any(not self.for_step(s).is_local for s in steps)

    def check(self, steps: list[str]) -> list[str]:
        """Vrátí seznam problémů s dostupností poskytovatelů pro dané kroky."""
        problems: list[str] = []
        seen: set[int] = set()
        for s in steps:
            p = self.for_step(s)
            if id(p) in seen:
                continue
            seen.add(id(p))
            try:
                st = p.check()
            except LLMError as exc:
                st = ProviderStatus(False, str(exc))
            if not st.ok:
                problems.append(st.message)
        return problems

    def describe(self) -> str:
        if self.mode == LLMMode.LOCAL:
            return f"Lokálně – {self.local.label}"
        if self.mode == LLMMode.CLOUD:
            return f"Cloud – {self.cloud.label}"
        return f"Hybridní – lokálně {self.local.label}, cloud {self.cloud.label} pro: " + \
            ", ".join(self.settings.hybrid_cloud_steps)
