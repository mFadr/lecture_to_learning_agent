"""Společné ošetření HTTP chyb, opakování a zrušení."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, TypeVar

import httpx

from .base import (LLMAuthError, LLMCancelled, LLMError, LLMInvalidResponse, LLMRateLimit, LLMTimeout,
                   LLMUnavailable)

log = logging.getLogger(__name__)
T = TypeVar("T")


def error_from_status(resp: httpx.Response, provider: str) -> LLMError:
    try:
        body = resp.read().decode("utf-8", "replace")[:500]
    except Exception:
        body = ""
    code = resp.status_code
    if code in (401, 403):
        return LLMAuthError(f"{provider}: přístup odmítnut (HTTP {code}). Zkontrolujte API klíč a oprávnění.")
    if code == 404:
        return LLMUnavailable(f"{provider}: model nebo endpoint nebyl nalezen (HTTP 404). {body}")
    if code == 429:
        err = LLMRateLimit(f"{provider}: překročen limit požadavků nebo vyčerpán kredit (HTTP 429). {body}")
        retry_after = resp.headers.get("retry-after")
        err.retry_after = float(retry_after) if retry_after and retry_after.isdigit() else None  # type: ignore[attr-defined]
        return err
    if code >= 500:
        return LLMUnavailable(f"{provider}: chyba serveru (HTTP {code}). Zkuste to později. {body}")
    return LLMInvalidResponse(f"{provider}: požadavek odmítnut (HTTP {code}): {body}")


def with_retries(fn: Callable[[], T], *, attempts: int = 3, cancel: threading.Event | None = None,
                 base_delay: float = 2.0) -> T:
    """Opakuje přechodné chyby (síť, 429, 5xx) s exponenciálním čekáním."""
    last: LLMError | None = None
    for i in range(attempts):
        if cancel and cancel.is_set():
            raise LLMCancelled("Úloha byla zrušena.")
        try:
            return fn()
        except (LLMRateLimit, LLMUnavailable, LLMTimeout) as exc:
            last = exc
            if i == attempts - 1:
                break
            delay = getattr(exc, "retry_after", None) or base_delay * (2 ** i)
            log.warning("Přechodná chyba LLM (%s), opakuji za %.0f s.", exc, delay)
            if cancel:
                if cancel.wait(min(delay, 60)):
                    raise LLMCancelled("Úloha byla zrušena.")
            else:
                time.sleep(min(delay, 60))
    assert last is not None
    raise last


def translate_transport_error(exc: Exception, provider: str, base_url: str) -> LLMError:
    if isinstance(exc, httpx.TimeoutException):
        return LLMTimeout(f"{provider}: model neodpověděl v časovém limitu. Zkuste menší model, kratší "
                          "části dokumentu nebo delší časový limit v Nastavení.")
    if isinstance(exc, httpx.ConnectError):
        return LLMUnavailable(f"{provider}: nelze se připojit k {base_url}. Je služba spuštěná a "
                              "je k dispozici síťové připojení?")
    return LLMUnavailable(f"{provider}: chyba komunikace ({type(exc).__name__}: {exc}).")
