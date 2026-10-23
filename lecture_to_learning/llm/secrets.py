"""Bezpečné načítání API klíčů: proměnné prostředí nebo Správce pověření Windows (keyring)."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

KEYRING_SERVICE = "LectureToLearning"
KEYRING_USER = "cloud_api_key"
ENV_VARS = ("L2L_API_KEY", "OPENAI_API_KEY")


def get_api_key() -> tuple[str | None, str]:
    """Vrátí (klíč, popis zdroje). Klíč se nikdy neloguje."""
    for var in ENV_VARS:
        if os.environ.get(var):
            return os.environ[var].strip(), f"proměnná prostředí {var}"
    try:
        import keyring
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if key:
            return key, "Správce pověření Windows"
    except Exception as exc:  # keyring nemusí být dostupný
        log.debug("keyring nedostupný: %s", type(exc).__name__)
    return None, "nenastaven"


def store_api_key(key: str) -> None:
    import keyring
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key.strip())


def delete_api_key() -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        pass
