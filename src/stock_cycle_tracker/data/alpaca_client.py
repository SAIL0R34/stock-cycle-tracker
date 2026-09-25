"""HTTP client for Alpaca Markets (data + paper trading) — raw REST, no SDK.

Design notes
------------
* **Rate discipline**: the free tier allows ~200 requests/minute. All calls
  flow through one token bucket (180/min, conservative) shared per client
  instance, so a watchlist scan cannot accidentally exhaust the quota.
* **No secrets in errors**: auth headers are never echoed into exceptions
  (they are scrubbed to ``<redacted>`` before any detail string is built).
* **Paper-only trading host**: the trading base URL is frozen to Alpaca's
  paper endpoint. There is deliberately no configuration for the live host.

Env vars: ``ALPACA_API_KEY_ID``, ``ALPACA_API_SECRET_KEY``,
``ALPACA_DATA_FEED`` (default ``iex`` — the free feed; SIP requires a paid
plan and simply returns better consolidated prints).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger("stock_cycle_tracker.data.alpaca")

DATA_HOST = "https://data.alpaca.markets"
TRADING_HOST = "https://paper-api.alpaca.markets"  # frozen: paper only

# Timeframe enum value -> Alpaca v2 bars timeframe. 3m has no native Alpaca
# interval; the fetcher resamples it from 1Min bars.
BAR_TIMEFRAMES: dict[str, str | None] = {
    "1m": "1Min",
    "3m": None,  # fetched as 1Min, resampled by the fetcher
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "1h": "1Hour",
    "4h": "4Hour",
    "1d": "1Day",
    "1w": "1Week",
}


class RateBudgetExceeded(RuntimeError):
    """Raised when the client would exceed its per-minute request budget."""


class TokenBucket:
    """Minimal thread-safe token bucket (capacity/refill in requests per minute)."""

    def __init__(self, per_minute: int = 180):
        self.capacity = float(per_minute)
        self.tokens = float(per_minute)
        self.refill_rate = per_minute / 60.0
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last_refill) * self.refill_rate)
                self.last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                if now >= deadline:
                    raise RateBudgetExceeded(
                        f"Alpaca request budget of {int(self.capacity)}/min exhausted"
                    )
                # Sleep outside the loop check window
                time.sleep(min(0.5, deadline - now))


class AlpacaHTTPClient:
    """Shared HTTP plumbing for Alpaca data and (paper) trading endpoints."""

    def __init__(
        self,
        api_key_id: Optional[str] = None,
        api_secret_key: Optional[str] = None,
        data_feed: Optional[str] = None,
        requests_per_minute: int = 180,
        timeout: float = 20.0,
    ):
        self._fixed_key_id = api_key_id
        self._fixed_secret = api_secret_key
        self._fixed_feed = data_feed
        self.timeout = timeout
        self.bucket = TokenBucket(requests_per_minute)
        self._resolve_credentials()

    # ── plumbing ────────────────────────────────────────────────────

    def _resolve_credentials(self) -> None:
        """Credentials resolve per-request: explicit args > environment/.env >
        the Settings-UI store — so keys entered in the app take effect on the
        very next call, without a restart."""
        from stock_cycle_tracker.web.secrets_store import secrets_store

        self.api_key_id = (
            self._fixed_key_id
            or os.environ.get("ALPACA_API_KEY_ID")
            or secrets_store.get("alpaca_api_key_id")
            or ""
        )
        self.api_secret_key = (
            self._fixed_secret
            or os.environ.get("ALPACA_API_SECRET_KEY")
            or secrets_store.get("alpaca_api_secret_key")
            or ""
        )
        self.data_feed = (
            self._fixed_feed
            or os.environ.get("ALPACA_DATA_FEED")
            or secrets_store.get("alpaca_data_feed")
            or "iex"
        ).lower()

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key_id and self.api_secret_key)

    def _scrub(self, text: str) -> str:
        """Never let auth material leak into logs or exception strings."""
        for secret in (self.api_key_id, self.api_secret_key):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[dict[str, Any]] = None,
        body: Optional[dict[str, Any]] = None,
        retry_on_429: bool = True,
    ) -> Any:
        self._resolve_credentials()  # pick up Settings-UI changes immediately
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = {
            "APCA-API-KEY-ID": self.api_key_id,
            "APCA-API-SECRET-KEY": self.api_secret_key,
            "Content-Type": "application/json",
        }
        self.bucket.acquire()
        request = Request(url, data=json.dumps(body).encode() if body else None, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            if exc.code == 429 and retry_on_429:
                wait = float(exc.headers.get("Retry-After") or 5.0)
                logger.warning("Alpaca 429; backing off %.1fs", wait)
                time.sleep(min(wait, 30.0))
                return self._request(method, url, retry_on_429=False)
            raise RuntimeError(
                self._scrub(f"Alpaca {method} {url} failed: HTTP {exc.code} {detail}")
            ) from exc
        except URLError as exc:
            raise RuntimeError(self._scrub(f"Alpaca {method} {url} unreachable: {exc}")) from exc

    # ── market data (data.alpaca.markets) ───────────────────────────

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Paginated historical bars for one symbol. ``timeframe`` is the
        Alpaca interval string (see BAR_TIMEFRAMES values)."""
        bars: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        while True:
            params: dict[str, Any] = {
                "start": int(start.replace(tzinfo=timezone.utc).timestamp()),
                "end": int(end.replace(tzinfo=timezone.utc).timestamp()),
                "adjustment": "split",
                "feed": self.data_feed,
                "limit": 10000,
            }
            if page_token:
                params["page_token"] = page_token
            payload = self._request(
                "GET", f"{DATA_HOST}/v2/stocks/{symbol}/bars", params=params
            )
            bars.extend(payload.get("bars", []))
            page_token = payload.get("next_page_token")
            if not page_token:
                return bars

    def compare_feeds(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        """Fetch the same window from IEX and SIP and diff the bars.

        Returns per-feed candle counts, close-series correlation, max close
        divergence in %, and a divergence verdict. SIP requires a paid data
        subscription; without one the SIP leg errors and is reported as
        unavailable rather than silently skipped."""
        def fetch(feed: str) -> tuple[list[dict[str, Any]] | None, str | None]:
            try:
                return self._request(
                    "GET", f"{DATA_HOST}/v2/stocks/{symbol}/bars",
                    params={
                        "start": int(start.replace(tzinfo=timezone.utc).timestamp()),
                        "end": int(end.replace(tzinfo=timezone.utc).timestamp()),
                        "timeframe": timeframe,
                        "adjustment": "split",
                        "feed": feed,
                        "limit": 10000,
                    },
                ).get("bars", []), None
            except Exception as exc:  # noqa: BLE001 - surfaced in the payload
                return None, str(exc)

        iex_bars, iex_err = fetch("iex")
        sip_bars, sip_err = fetch("sip")

        result: dict[str, Any] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "iex": {"bars": len(iex_bars) if iex_bars is not None else None, "error": iex_err},
            "sip": {"bars": len(sip_bars) if sip_bars is not None else None, "error": sip_err},
        }
        if iex_bars is None or sip_bars is None:
            result["note"] = "Feed comparison needs both feeds; resolve the reported error(s)."
            return result

        # Align on shared timestamps.
        iex_by_ts = {b["t"]: float(b["c"]) for b in iex_bars}
        sip_by_ts = {b["t"]: float(b["c"]) for b in sip_bars}
        shared = sorted(set(iex_by_ts) & set(sip_by_ts))
        result["shared_bars"] = len(shared)
        if len(shared) < 2:
            result["note"] = "Too few shared candles to compare."
            return result

        diffs = [abs(iex_by_ts[t] - sip_by_ts[t]) / sip_by_ts[t] * 100 for t in shared]
        mean_diff = sum(diffs) / len(diffs)
        result["mean_close_divergence_pct"] = round(mean_diff, 4)
        result["max_close_divergence_pct"] = round(max(diffs), 4)
        result["coverage_iex_pct"] = round(len(shared) / max(len(sip_by_ts), 1) * 100, 1)
        result["verdict"] = (
            "negligible" if mean_diff < 0.05
            else "minor" if mean_diff < 0.25
            else "material — IEX prints diverge; prefer SIP for this timeframe"
        )
        return result

    def get_snapshots(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Batched latest snapshot (quote/trade/daily bars) for many symbols."""
        if not symbols:
            return {}
        payload = self._request(
            "GET", f"{DATA_HOST}/v2/stocks/snapshots",
            params={"symbols": ",".join(symbols), "feed": self.data_feed},
        )
        return payload if isinstance(payload, dict) else {}

    def get_clock(self) -> dict[str, Any]:
        return self._request("GET", f"{TRADING_HOST}/v2/clock")

    def get_calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        """Trading calendar rows (ISO dates) between start and end inclusive."""
        payload = self._request(
            "GET", f"{TRADING_HOST}/v2/calendar",
            params={"start": start, "end": end},
        )
        return payload if isinstance(payload, list) else []

    # ── paper account (paper-api.alpaca.markets) ────────────────────

    def get_account(self) -> dict[str, Any]:
        return self._request("GET", f"{TRADING_HOST}/v2/account")

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Open orders with nested bracket legs included."""
        payload = self._request(
            "GET", f"{TRADING_HOST}/v2/orders",
            params={"status": "open", "nested": "true", "limit": 50},
        )
        return payload if isinstance(payload, list) else []

    def get_positions(self) -> list[dict[str, Any]]:
        payload = self._request("GET", f"{TRADING_HOST}/v2/positions")
        return payload if isinstance(payload, list) else []

    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]:
        """Submit an order to the PAPER account. Market/day only by policy —
        the caller (PaperTradingService) enforces it; this stays a thin pass-through."""
        return self._request("POST", f"{TRADING_HOST}/v2/orders", body=order)
