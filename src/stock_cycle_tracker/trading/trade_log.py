"""Append-only JSONL trade log (outputs/trade_log.jsonl).

One JSON object per line — previews, submissions, failures, cancellations.
Trivially greppable and tail-able; the pattern comes from alpha-brain-core's
logging_obs layer."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from stock_cycle_tracker.settings import settings

logger = logging.getLogger("stock_cycle_tracker.trading.trade_log")


class TradeLog:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.resolve_app_path("outputs") / "trade_log.jsonl"

    def append(self, record: dict) -> None:
        record = {"at": datetime.now(timezone.utc).isoformat(), **record}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError as exc:
            logger.warning("Could not append trade log: %s", exc)

    def tail(self, limit: int = 50) -> list[dict]:
        try:
            if not self.path.exists():
                return []
            lines = self.path.read_text().splitlines()[-limit:]
            return [json.loads(line) for line in lines if line.strip()]
        except (json.JSONDecodeError, OSError):
            return []
