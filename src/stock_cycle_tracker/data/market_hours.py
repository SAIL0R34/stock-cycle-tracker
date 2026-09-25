"""US equity market-hours awareness.

Two layers:
1. **Alpaca calendar/clock** (when API keys are present) — authoritative:
   sessions, holidays, half-days, and the live clock phase.
2. **Local approximation** (no keys) — weekday + 09:30–16:00 America/New_York
   with DST handled by ``zoneinfo``. Holidays are unknown in this mode and
   reported as such.

The service caches the calendar daily to ``data/cache/market_calendar.json``
so repeated runs (and watchlist scans) do not spend requests on it.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_cycle_tracker.data.alpaca_client import AlpacaHTTPClient
from stock_cycle_tracker.settings import settings

logger = logging.getLogger("stock_cycle_tracker.data.market_hours")

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


class MarketHoursService:
    """Session-aware clock for US equities with daily calendar caching."""

    def __init__(self, client: AlpacaHTTPClient | None = None, cache_dir: Path | None = None):
        self.client = client or AlpacaHTTPClient()
        self.cache_path = (cache_dir or settings.cache_path) / "market_calendar.json"

    # ── calendar (cached per day) ───────────────────────────────────

    def _load_cached(self, today: str) -> list[dict] | None:
        try:
            if self.cache_path.exists():
                payload = json.loads(self.cache_path.read_text())
                if payload.get("fetched_on") == today:
                    return payload.get("sessions")
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def calendar(self, weeks_ahead: int = 2) -> list[dict]:
        """Trading sessions (date/open/close ET) from 4 weeks back to N ahead."""
        today = date.today()
        fetched_on = today.isoformat()
        cached = self._load_cached(fetched_on)
        if cached is not None:
            return cached

        sessions: list[dict] = []
        if self.client.has_credentials:
            try:
                start = (today - timedelta(days=60)).isoformat()
                end = (today + timedelta(weeks=weeks_ahead)).isoformat()
                sessions = self.client.get_calendar(start, end)
            except Exception as exc:  # noqa: BLE001 - fall back to approximation
                logger.warning("Alpaca calendar unavailable, approximating: %s", exc)

        payload = {"fetched_on": fetched_on, "source": "alpaca" if sessions else "approx", "sessions": sessions}
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(payload))
        except OSError as exc:
            logger.warning("Could not cache market calendar: %s", exc)
        return sessions

    def _session_for(self, day: date) -> dict | None:
        key = day.isoformat()
        for row in self.calendar():
            if row.get("date") == key:
                return row
        return None

    # ── phase ───────────────────────────────────────────────────────

    def _et(self, moment: datetime | None = None) -> datetime:
        moment = moment or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(ET)

    def phase(self, moment: datetime | None = None) -> dict:
        """Classify the moment: {'phase': pre|open|post|closed, 'next_event': str,
        'next_event_at': iso|None, 'source': alpaca|approx, 'note': str}."""
        now_et = self._et(moment)
        day = now_et.date()
        clock = now_et.time()

        # Fast path: live Alpaca clock when credentials exist.
        if self.client.has_credentials:
            try:
                raw = self.client.get_clock()
                return {
                    "phase": "open" if raw.get("is_open") else "closed",
                    "next_event_at": raw.get("next_open") if not raw.get("is_open") else raw.get("next_close"),
                    "next_event": "open" if not raw.get("is_open") else "close",
                    "timestamp": raw.get("timestamp"),
                    "source": "alpaca",
                    "note": "",
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("Alpaca clock unavailable, approximating: %s", exc)

        # Approximation (weekday + session window, holidays unknown).
        weekday = day.weekday() < 5
        is_session_day = weekday  # holiday-agnostic
        if is_session_day:
            if clock < MARKET_OPEN:
                return self._phase_row("pre", day, "open", self._at(day, MARKET_OPEN))
            if clock >= MARKET_CLOSE:
                return self._phase_row("post", day, "open", self._next_session_open(day))
            return self._phase_row("open", day, "close", self._at(day, MARKET_CLOSE))
        return self._phase_row("closed", day, "open", self._next_session_open(day))

    @staticmethod
    def _phase_row(phase: str, day: date, event: str, at: datetime) -> dict:
        return {
            "phase": phase,
            "next_event": event,
            "next_event_at": at.isoformat(),
            "timestamp": None,
            "source": "approx",
            "note": "approximation: market holidays not known without API credentials",
        }

    def _at(self, day: date, when: time) -> datetime:
        return datetime.combine(day, when, tzinfo=ET)

    def _next_session_open(self, after_day: date) -> datetime:
        for offset in range(1, 10):
            candidate = after_day + timedelta(days=offset)
            if candidate.weekday() < 5:
                return self._at(candidate, MARKET_OPEN)
        return self._at(after_day + timedelta(days=7), MARKET_OPEN)

    # ── data-range helpers ──────────────────────────────────────────

    def last_session_close(self, moment: datetime | None = None) -> datetime:
        """The most recent regular-session close at or before `moment` (UTC)."""
        now_et = self._et(moment)
        day = now_et.date()
        for back in range(0, 10):
            candidate = day - timedelta(days=back)
            row = self._session_for(candidate) if self.client.has_credentials else None
            if row and row.get("close"):
                close_dt = datetime.fromisoformat(f"{row['date']}T{row['close']}:00").replace(tzinfo=ET)
                if close_dt <= now_et:
                    return close_dt.astimezone(UTC).replace(tzinfo=None)
            # approx mode: prior weekday
            if not self.client.has_credentials and candidate.weekday() < 5:
                close_dt = self._at(candidate, MARKET_CLOSE)
                if close_dt <= now_et:
                    return close_dt.astimezone(UTC).replace(tzinfo=None)
        return now_et.astimezone(UTC).replace(tzinfo=None)

    def effective_data_end(self, moment: datetime | None = None) -> datetime:
        """Where a data fetch should logically end: `now` while the market is
        open, otherwise the last session close. Prevents overnight cache
        invalidation churn when the loader compares freshness against now()."""
        info = self.phase(moment)
        if info["phase"] == "open":
            return datetime.utcnow()
        return self.last_session_close(moment)
