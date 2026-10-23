"""Lokální LLM přes Ollama REST API (http://localhost:11434)."""

from __future__ import annotations

import json
import logging
import threading

import httpx

from .base import LLMCancelled, LLMInvalidResponse, LLMProvider, LLMResponse, LLMUnavailable, Message, ProviderStatus
from .http_util import error_from_status, translate_transport_error, with_retries

log = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    name = "Ollama"
    is_local = True

    def __init__(self, model: str, base_url: str = "http://localhost:11434", *, context_tokens: int = 8192,
                 **kw):
        super().__init__(model, **kw)
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.context_tokens = context_tokens
        self._json_supported = True

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(connect=5.0, read=self.timeout_s, write=30.0, pool=5.0)

    def check(self) -> ProviderStatus:
        try:
            with httpx.Client(transport=self.transport, timeout=5.0) as client:
                r = client.get(f"{self.base_url}/api/tags")
                r.raise_for_status()
                models = [m.get("name", "") for m in r.json().get("models", [])]
        except httpx.HTTPError as exc:
            return ProviderStatus(False, "Ollama neběží nebo není nainstalovaná "
                                         f"({self.base_url}). Nainstalujte ji z https://ollama.com a spusťte. "
                                         f"Detail: {type(exc).__name__}")
        except ValueError:
            return ProviderStatus(False, f"Na adrese {self.base_url} neodpovídá Ollama API.")
        if not models:
            return ProviderStatus(False, f"Ollama běží, ale nemá stažený žádný model. Spusťte např. "
                                         f"`ollama pull {self.model or 'llama3.1:8b'}`.", models)
        if self.model not in models and f"{self.model}:latest" not in models:
            return ProviderStatus(False, f"Model „{self.model}“ není stažený. Spusťte `ollama pull {self.model}` "
                                         "nebo vyberte jeden z dostupných modelů.", models)
        return ProviderStatus(True, f"Ollama běží, model „{self.model}“ je k dispozici.", models)

    def chat(self, messages: list[Message], *, json_output: bool = False,
             cancel: threading.Event | None = None) -> LLMResponse:
        def attempt() -> LLMResponse:
            return self._chat_once(messages, json_output and self.json_mode and self._json_supported, cancel)
        try:
            return with_retries(attempt, attempts=2, cancel=cancel)
        except LLMInvalidResponse as exc:
            # Některé modely/verze nepodporují format=json – zkusíme bez něj.
            if json_output and self._json_supported and "format" in str(exc).lower():
                log.warning("Model nepodporuje JSON režim, pokračuji bez něj.")
                self._json_supported = False
                return with_retries(lambda: self._chat_once(messages, False, cancel), attempts=2, cancel=cancel)
            raise

    def _chat_once(self, messages: list[Message], json_fmt: bool,
                   cancel: threading.Event | None) -> LLMResponse:
        options: dict = {"temperature": self.temperature, "num_predict": self.max_output_tokens}
        if self.context_tokens:
            options["num_ctx"] = self.context_tokens
        payload: dict = {"model": self.model, "stream": True, "options": options,
                         "messages": [{"role": m.role, "content": m.content} for m in messages]}
        if json_fmt:
            payload["format"] = "json"
        self.calls += 1
        parts: list[str] = []
        final: dict = {}
        try:
            with httpx.Client(transport=self.transport, timeout=self._timeout()) as client:
                with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                    if resp.status_code != 200:
                        err = error_from_status(resp, self.name)
                        if resp.status_code == 404:
                            raise LLMUnavailable(f"Model „{self.model}“ není v Ollamě dostupný. "
                                                 f"Stáhněte ho příkazem `ollama pull {self.model}`.")
                        raise err
                    for line in resp.iter_lines():
                        if cancel and cancel.is_set():
                            raise LLMCancelled("Úloha byla zrušena.")
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if "error" in obj:
                            raise LLMInvalidResponse(f"Ollama: {obj['error']}")
                        parts.append(obj.get("message", {}).get("content", ""))
                        if obj.get("done"):
                            final = obj
                            break
        except httpx.HTTPError as exc:
            raise translate_transport_error(exc, self.name, self.base_url) from exc
        text = "".join(parts)
        return LLMResponse(text=text, model=self.model, prompt_tokens=final.get("prompt_eval_count"),
                           completion_tokens=final.get("eval_count"),
                           truncated=final.get("done_reason") == "length")
