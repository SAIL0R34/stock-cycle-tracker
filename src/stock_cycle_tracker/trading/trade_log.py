"""Trade log: append-only events in SQLite (outputs/trade_log.db, WAL).

Same append/tail surface as the original JSONL version; events keep a
monotonic id so tails are stable. The JSONL twin is still written for
grep/tail convenience and portability."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from stock_cycle_tracker.settings import settings

logger = logging.getLogger("stock_cycle_tracker.trading.trade_log")


class TradeLog:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.resolve_app_path("outputs") / "trade_log.db"
        self.jsonl_twin = self.path.with_suffix(".jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " at TEXT NOT NULL,"
            " payload TEXT NOT NULL)"
        )
        self._conn.commit()

    def append(self, record: dict) -> None:
        record = {"at": datetime.now(UTC).isoformat(), **record}
        try:
            self._conn.execute(
                "INSERT INTO events (at, payload) VALUES (?, ?)",
                (record["at"], json.dumps(record, default=str)),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            logger.warning("Could not append trade log: %s", exc)
        try:
            with self.jsonl_twin.open("a") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass  # twin is best-effort convenience

    def tail(self, limit: int = 50) -> list[dict]:
        try:
            rows = self._conn.execute(
                "SELECT payload FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [json.loads(row[0]) for row in reversed(rows)]
        except sqlite3.Error:
            return []
