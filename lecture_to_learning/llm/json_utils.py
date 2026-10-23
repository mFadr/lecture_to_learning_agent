"""Robustní získání a validace strukturované (JSON) odpovědi modelu."""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .base import LLMInvalidResponse, LLMProvider, Message

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> object:
    """Najde JSON objekt v textu (i když model přidá komentář nebo ```json blok)."""
    text = text.strip()
    candidates = [text]
    candidates += [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    start = text.find("{")
    if start != -1:
        candidates.append(_balanced(text, start))
    for cand in candidates:
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            try:
                return json.loads(_repair(cand))
            except json.JSONDecodeError:
                continue
    raise LLMInvalidResponse("Odpověď modelu neobsahuje platný JSON.")


def _balanced(text: str, start: int) -> str:
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _repair(s: str) -> str:
    s = re.sub(r",\s*([}\]])", r"\1", s)  # koncové čárky
    # useknutý JSON: dovřít otevřené závorky
    opens = []
    in_str = esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            opens.append("}" if ch == "{" else "]")
        elif ch in "}]" and opens:
            opens.pop()
    if in_str:
        s += '"'
    return s + "".join(reversed(opens))


def parse_model(text: str, model: type[M]) -> M:
    data = extract_json(text)
    if isinstance(data, list):
        # některé modely vrátí rovnou pole – zkusíme ho přiřadit jedinému seznamovému poli
        list_fields = [n for n, f in model.model_fields.items() if "list" in str(f.annotation)]
        if len(list_fields) >= 1:
            data = {list_fields[0]: data}
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise LLMInvalidResponse(f"Strukturovaná odpověď neodpovídá schématu: {_short_errors(exc)}") from exc


def _short_errors(exc: ValidationError, limit: int = 5) -> str:
    errs = exc.errors()[:limit]
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in errs)


def ask_json(provider: LLMProvider, system: str, user: str, model: type[M], *,
             cancel: threading.Event | None = None, repair_attempts: int = 1) -> M:
    """Zavolá model a vrátí validovaný objekt. Při neplatné odpovědi požádá o opravu."""
    messages = [Message("system", system), Message("user", user)]
    last_err: LLMInvalidResponse | None = None
    for attempt in range(repair_attempts + 1):
        resp = provider.chat(messages, json_output=True, cancel=cancel)
        try:
            return parse_model(resp.text, model)
        except LLMInvalidResponse as exc:
            last_err = exc
            log.warning("Neplatná odpověď modelu (pokus %d): %s", attempt + 1, exc)
            hint = " Odpověď byla useknutá – buď stručnější." if resp.truncated else ""
            messages = messages[:2] + [
                Message("assistant", resp.text[:6000]),
                Message("user", f"Tvoje odpověď nebyla platná: {exc}.{hint} Vrať POUZE opravený JSON objekt "
                                "přesně podle zadaného schématu, bez dalšího textu."),
            ]
    assert last_err is not None
    raise last_err
