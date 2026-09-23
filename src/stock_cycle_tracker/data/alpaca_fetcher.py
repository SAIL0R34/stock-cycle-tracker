"""Alpaca OHLCV fetcher (equities) built on the shared AlpacaHTTPClient."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from stock_cycle_tracker.data.alpaca_client import BAR_TIMEFRAMES, AlpacaHTTPClient
from stock_cycle_tracker.data.fetchers import BaseFetcher
from stock_cycle_tracker.data.normalization import resample_ohlcv
from stock_cycle_tracker.models import OHLCV, Timeframe

ET = ZoneInfo("America/New_York")
SESSION_OPEN_ET = 9 * 60 + 30  # minutes from midnight
SESSION_CLOSE_ET = 16 * 60


class AlpacaFetcher(BaseFetcher):
    """US equity bars from Alpaca (free IEX feed by default).

    Requires ``ALPACA_API_KEY_ID`` / ``ALPACA_API_SECRET_KEY``. Bars are
    split-adjusted; with ``regular_hours_only`` (default) pre/post-market
    prints are filtered so overnight gaps stay gaps — the swing engine must
    never see fabricated continuity across sessions.
    """

    name = "alpaca"

    def __init__(
        self,
        client: AlpacaHTTPClient | None = None,
        regular_hours_only: bool = True,
        **_factory_kwargs,  # tolerate the factory's api_key/api_secret kwargs
    ):
        self.client = client or AlpacaHTTPClient()
        self.regular_hours_only = regular_hours_only

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        limit: Optional[int] = None,
    ) -> list[OHLCV]:
        if not self.client.has_credentials:
            raise RuntimeError(
                "Alpaca data needs ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY "
                "(paper keys work; put them in .env)"
            )

        native = BAR_TIMEFRAMES.get(timeframe.value)
        fetch_timeframe = native or "1Min"
        raw = self.client.get_bars(symbol, fetch_timeframe, start, end)

        candles = [
            OHLCV(
                timestamp=datetime.fromtimestamp(bar["t"], tz=timezone.utc).replace(tzinfo=None),
                open=float(bar["o"]),
                high=float(bar["h"]),
                low=float(bar["l"]),
                close=float(bar["c"]),
                volume=float(bar.get("v") or 0.0),
            )
            for bar in raw
        ]

        if native is None:  # 3m fetched as 1Min → resample
            candles = resample_ohlcv(candles, target_timeframe=timeframe.value)

        if self.regular_hours_only:
            candles = [c for c in candles if self._in_regular_session(c.timestamp)]

        candles.sort(key=lambda c: c.timestamp)
        if limit:
            candles = candles[-limit:]
        return candles

    @staticmethod
    def _in_regular_session(ts_utc: datetime) -> bool:
        """True when the bar timestamp falls inside 09:30–16:00 ET on a weekday."""
        local = ts_utc.replace(tzinfo=timezone.utc).astimezone(ET)
        if local.weekday() >= 5:
            return False
        minutes = local.hour * 60 + local.minute
        return SESSION_OPEN_ET <= minutes < SESSION_CLOSE_ET

    async def get_available_periods(self, symbol: str) -> list[tuple[datetime, datetime]]:
        """Free-tier bars go back years for liquid tickers; report a generous window."""
        end = datetime.now(timezone.utc).replace(tzinfo=None)
        return [(end - timedelta(days=365 * 5), end)]
