"""Cloudové LLM přes API kompatibilní s OpenAI (/v1/chat/completions)."""

from __future__ import annotations

import json
import logging
import threading

import httpx

from .base import (LLMAuthError, LLMCancelled, LLMInvalidResponse, LLMProvider, LLMResponse, Message,
                   ProviderStatus)
from .http_util import error_from_status, translate_transport_error, with_retries

log = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    name = "Cloud API"
    is_local = False

    def __init__(self, model: str, base_url: str, api_key: str | None, *, display_name: str = "Cloud API", **kw):
        super().__init__(model, **kw)
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key  # nikdy se neloguje
        self.name = display_name
        # Adaptace na rozdílné schopnosti modelů (zjišťuje se za běhu z chyb 400).
        self._use_response_format = True
        self._use_temperature = True
        self._max_tokens_param = "max_tokens"

    def __repr__(self) -> str:  # zabrání úniku klíče v repr/logu
        return f"OpenAICompatProvider(model={self.model!r}, base_url={self.base_url!r})"

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise LLMAuthError("Chybí API klíč. Nastavte proměnnou prostředí L2L_API_KEY (nebo OPENAI_API_KEY) "
                               "nebo jej uložte v Nastavení do správce pověření Windows.")
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def check(self) -> ProviderStatus:
        try:
            headers = self._headers()
        except LLMAuthError as exc:
            return ProviderStatus(False, str(exc))
        try:
            with httpx.Client(transport=self.transport, timeout=10.0) as client:
                r = client.get(f"{self.base_url}/models", headers=headers)
        except httpx.HTTPError as exc:
            return ProviderStatus(False, str(translate_transport_error(exc, self.name, self.base_url)))
        if r.status_code in (401, 403):
            return ProviderStatus(False, "API klíč byl odmítnut (neplatný nebo bez oprávnění).")
        if r.status_code != 200:
            return ProviderStatus(False, f"Služba vrátila HTTP {r.status_code}.")
        try:
            models = sorted(m.get("id", "") for m in r.json().get("data", []))
        except ValueError:
            models = []
        if models and self.model not in models:
            return ProviderStatus(False, f"Model „{self.model}“ není u poskytovatele k dispozici.", models)
        return ProviderStatus(True, f"Připojeno k {self.base_url}, model „{self.model}“.", models)

    def chat(self, messages: list[Message], *, json_output: bool = False,
             cancel: threading.Event | None = None) -> LLMResponse:
        for _ in range(4):  # max. několik adaptací parametrů
            try:
                return with_retries(lambda: self._chat_once(messages, json_output, cancel), attempts=3,
                                    cancel=cancel)
            except LLMInvalidResponse as exc:
                msg = str(exc).lower()
                if "response_format" in msg and self._use_response_format:
                    self._use_response_format = False
                elif "temperature" in msg and self._use_temperature:
                    self._use_temperature = False
                elif "max_tokens" in msg and self._max_tokens_param == "max_tokens":
                    self._max_tokens_param = "max_completion_tokens"
                else:
                    raise
                log.info("Model nepodporuje některý parametr, opakuji s upraveným požadavkem.")
        raise LLMInvalidResponse("Model odmítá požadavek i po úpravě parametrů.")

    def _chat_once(self, messages: list[Message], json_output: bool,
                   cancel: threading.Event | None) -> LLMResponse:
        payload: dict = {"model": self.model, "stream": True,
                         "messages": [{"role": m.role, "content": m.content} for m in messages],
                         self._max_tokens_param: self.max_output_tokens}
        if self._use_temperature:
            payload["temperature"] = self.temperature
        if json_output and self.json_mode and self._use_response_format:
            payload["response_format"] = {"type": "json_object"}
        headers = self._headers()
        self.calls += 1
        parts: list[str] = []
        finish = None
        usage: dict = {}
        timeout = httpx.Timeout(connect=10.0, read=self.timeout_s, write=30.0, pool=5.0)
        try:
            with httpx.Client(transport=self.transport, timeout=timeout) as client:
                with client.stream("POST", f"{self.base_url}/chat/completions", json=payload,
                                   headers=headers) as resp:
                    if resp.status_code != 200:
                        raise error_from_status(resp, self.name)
                    for line in resp.iter_lines():
                        if cancel and cancel.is_set():
                            raise LLMCancelled("Úloha byla zrušena.")
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("usage"):
                            usage = obj["usage"]
                        for ch in obj.get("choices", []):
                            delta = ch.get("delta", {})
                            if delta.get("content"):
                                parts.append(delta["content"])
                            if ch.get("finish_reason"):
                                finish = ch["finish_reason"]
        except httpx.HTTPError as exc:
            raise translate_transport_error(exc, self.name, self.base_url) from exc
        return LLMResponse(text="".join(parts), model=self.model, prompt_tokens=usage.get("prompt_tokens"),
                           completion_tokens=usage.get("completion_tokens"), truncated=finish == "length")
