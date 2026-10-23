"""Jednotné rozhraní mezi agentem a jazykovými modely."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


class LLMError(Exception):
    """Obecná chyba LLM se srozumitelnou zprávou pro uživatele."""

    retryable: bool = False


class LLMUnavailable(LLMError):
    retryable = True


class LLMAuthError(LLMError):
    pass


class LLMRateLimit(LLMError):
    retryable = True


class LLMTimeout(LLMError):
    retryable = True


class LLMInvalidResponse(LLMError):
    retryable = True


class LLMCancelled(LLMError):
    pass


class LLMBudgetExceeded(LLMError):
    pass


@dataclass
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    truncated: bool = False


@dataclass
class ProviderStatus:
    ok: bool
    message: str
    models: list[str] = field(default_factory=list)


class LLMProvider(ABC):
    """Poskytovatel LLM. Implementace musí být bezpečné pro volání z pracovního vlákna."""

    name: str = "provider"
    is_local: bool = True

    def __init__(self, model: str, *, temperature: float = 0.2, max_output_tokens: int = 2048,
                 timeout_s: float = 300.0, json_mode: bool = True, max_request_chars: int = 9000):
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout_s = timeout_s
        self.json_mode = json_mode
        self.max_request_chars = max_request_chars
        self.calls = 0
        self.transport = None  # volitelný httpx transport (testy)

    @property
    def label(self) -> str:
        return f"{self.name}: {self.model}"

    @abstractmethod
    def chat(self, messages: list[Message], *, json_output: bool = False,
             cancel: threading.Event | None = None) -> LLMResponse:
        """Vrátí odpověď modelu. Při zrušení vyhodí LLMCancelled."""

    @abstractmethod
    def check(self) -> ProviderStatus:
        """Ověří dostupnost služby a modelu."""

    def list_models(self) -> list[str]:
        return self.check().models
