"""Konfigurace aplikace (JSON v datové složce) a standardní cesty."""

from __future__ import annotations

import json
import logging
import os
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from . import APP_NAME

log = logging.getLogger(__name__)


class LLMMode(str, Enum):
    LOCAL = "local"  # Ollama, nic neodchází ze zařízení
    CLOUD = "cloud"  # API kompatibilní s OpenAI
    HYBRID = "hybrid"  # lokální zpracování, cloud jen pro vybrané kroky


class Preference(str, Enum):
    SPEED = "speed"
    QUALITY = "quality"
    COST = "cost"


class ProviderSettings(BaseModel):
    model: str = ""
    base_url: str = ""
    temperature: float = 0.2
    max_output_tokens: int = 2048
    timeout_s: float = 300.0
    # Maximální velikost textu dokumentu v jednom požadavku (znaky).
    max_request_chars: int = 9000
    # Kontextové okno modelu v tokenech (Ollama num_ctx); 0 = výchozí modelu.
    context_tokens: int = 8192
    # Pokus o nativní JSON režim (Ollama format=json / OpenAI response_format).
    json_mode: bool = True


class Settings(BaseModel):
    mode: LLMMode = LLMMode.LOCAL
    preference: Preference = Preference.QUALITY
    ollama: ProviderSettings = Field(default_factory=lambda: ProviderSettings(
        model="llama3.1:8b", base_url="http://localhost:11434"))
    cloud: ProviderSettings = Field(default_factory=lambda: ProviderSettings(
        model="gpt-4o-mini", base_url="https://api.openai.com/v1", max_request_chars=24000,
        context_tokens=0, timeout_s=120.0))
    cloud_provider_name: str = "OpenAI-kompatibilní API"
    # Kroky, které v hybridním režimu jdou do cloudu (ostatní lokálně).
    hybrid_cloud_steps: list[str] = Field(default_factory=lambda: ["summary", "flashcards", "quiz"])
    cloud_consent: bool = False  # uživatel potvrdil odesílání dat mimo zařízení
    # Limit počtu volání cloudového API na jeden běh (ochrana nákladů), 0 = bez limitu.
    cloud_max_calls_per_run: int = 60
    ocr_enabled: bool = False
    ocr_language: str = "ces+eng"
    tesseract_path: str = ""
    data_dir: str = ""  # prázdné = %LOCALAPPDATA%\LectureToLearning
    copy_sources_default: bool = False
    export_formats: list[str] = Field(default_factory=lambda: ["md", "html", "csv", "png"])
    detail_level: str = "standard"
    language: str = "cs"


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def config_dir() -> Path:
    return default_data_dir()


def config_path() -> Path:
    return config_dir() / "settings.json"


def data_dir(settings: Settings) -> Path:
    return Path(settings.data_dir).expanduser() if settings.data_dir else default_data_dir()


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_path()
    if path.exists():
        try:
            return Settings.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # poškozená konfigurace nesmí shodit aplikaci
            log.warning("Nastavení nelze načíst (%s), použijí se výchozí hodnoty.", exc)
    return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # API klíče se do nastavení nikdy neukládají – model je ani neobsahuje.
    tmp.write_text(json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)
