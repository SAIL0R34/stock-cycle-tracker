"""Local, gitignored store for credentials entered via the Settings UI.

Lives at ``outputs/app_secrets.json`` (``outputs/*`` is gitignored, so the
file never enters version control). Values are never returned to the client
verbatim — endpoints expose only "configured" flags and masked hints.

Precedence everywhere: explicit constructor args > environment/.env > this
store — except the LLM fields, where the store wins over the import-time
default so the most recent UI edit applies without a restart.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from stock_cycle_tracker.settings import settings

logger = logging.getLogger("stock_cycle_tracker.web.secrets")

FIELDS = {
    "alpaca_api_key_id",
    "alpaca_api_secret_key",
    "alpaca_data_feed",
    "llm_base_url",
    "llm_model",
}


class SecretsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.resolve_app_path("outputs") / "app_secrets.json"
        self._lock = threading.Lock()
        self._cache: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text())
                self._cache = {k: str(v) for k, v in data.items() if k in FIELDS and v}
                return self._cache
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Ignoring corrupt secrets store: %s", exc)
        self._cache = {}
        return self._cache

    def get(self, key: str) -> str:
        return self._load().get(key, "")

    def update(self, fields: dict[str, str]) -> dict[str, str]:
        """Persist non-empty values for known fields; blanks are ignored so a
        masked UI field never wipes a stored secret."""
        with self._lock:
            current = dict(self._load())
            changed = False
            for key, value in fields.items():
                if key in FIELDS and isinstance(value, str) and value.strip():
                    if current.get(key) != value.strip():
                        current[key] = value.strip()
                        changed = True
            if changed:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.write_text(json.dumps(current, indent=2))
                except OSError as exc:
                    logger.warning("Could not persist secrets: %s", exc)
                self._cache = current
            return current

    def clear(self, keys: set[str]) -> None:
        """Remove specific stored fields (e.g. mistyped Alpaca keys)."""
        with self._lock:
            current = dict(self._load())
            changed = False
            for key in keys:
                if key in current:
                    del current[key]
                    changed = True
            if changed:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.write_text(json.dumps(current, indent=2))
                except OSError as exc:
                    logger.warning("Could not persist secrets: %s", exc)
                self._cache = current

    def status(self) -> dict:
        """Client-safe view: configured flags + masked hints, never values."""
        data = self._load()

        def hint(key: str) -> str | None:
            value = data.get(key, "")
            return f"{'*' * 8}{value[-4:]}" if len(value) >= 4 else ("*" * len(value) or None)

        return {
            "alpaca": {
                "configured": bool(data.get("alpaca_api_key_id") and data.get("alpaca_api_secret_key")),
                "key_id_hint": hint("alpaca_api_key_id"),
                "feed": data.get("alpaca_data_feed", ""),
            },
            "llm": {
                "base_url": data.get("llm_base_url", ""),  # not a secret — shown in full
                "model": data.get("llm_model", ""),
            },
        }


secrets_store = SecretsStore()
