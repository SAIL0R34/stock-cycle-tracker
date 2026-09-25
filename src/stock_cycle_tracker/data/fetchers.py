"""Data fetchers for various cryptocurrency exchanges.

Provides swappable data sources for OHLCV data.
"""

import abc
import json
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stock_cycle_tracker.data.normalization import resample_ohlcv
from stock_cycle_tracker.models import OHLCV, Timeframe


class BaseFetcher(abc.ABC):
    """Abstract base class for data fetchers."""

    name: str = "base"

    @abc.abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> list[OHLCV]:
        """Fetch OHLCV data for a symbol and timeframe.

        Args:
            symbol: Trading pair symbol (e.g., "BTC-USD")
            timeframe: Candle timeframe
            start: Start datetime
            end: End datetime
            limit: Maximum number of candles to fetch

        Returns:
            List of OHLCV candles
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def get_available_periods(self, symbol: str) -> list[tuple[datetime, datetime]]:
        """Get available date ranges for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            List of (start, end) tuples for available data
        """
        raise NotImplementedError


class CoinbaseFetcher(BaseFetcher):
    """Fetcher for Coinbase Pro data."""

    name = "coinbase"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None):
        """Initialize the Coinbase fetcher.

        Args:
            api_key: Coinbase API key (optional)
            api_secret: Coinbase API secret (optional)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.pro.coinbase.com"

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        limit: int | None = None,
    ) -> list[OHLCV]:
        """Fetch OHLCV data from Coinbase Exchange public candles API."""
        fetch_timeframe = self._fetch_timeframe_for(timeframe)
        granularity = self._granularity_for_timeframe(fetch_timeframe)
        max_candles_per_request = min(limit or 299, 299)
        window_seconds = granularity * max_candles_per_request

        start = self._align_timestamp(start, granularity, round_up=False)
        end = self._align_timestamp(end, granularity, round_up=True)

        all_rows: list[list[float]] = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(seconds=window_seconds), end)
            if chunk_end <= cursor:
                break
            params = urlencode(
                {
                    "granularity": granularity,
                    "start": self._format_timestamp(cursor),
                    "end": self._format_timestamp(chunk_end),
                }
            )
            request = Request(
                f"https://api.exchange.coinbase.com/products/{symbol}/candles?{params}",
                headers={"User-Agent": "btc-swing-cycle-tracker/0.1.0"},
            )
            try:
                with urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    "Coinbase candles request failed "
                    f"for {symbol} {timeframe.value} from {self._format_timestamp(cursor)} "
                    f"to {self._format_timestamp(chunk_end)}: HTTP {exc.code} {detail}"
                ) from exc

            if isinstance(payload, dict) and payload.get("message"):
                raise RuntimeError(f"Coinbase API error: {payload['message']}")

            all_rows.extend(payload)

            if chunk_end >= end:
                break
            cursor = chunk_end

        candles = [
            OHLCV(
                timestamp=datetime.fromtimestamp(row[0], tz=UTC).replace(tzinfo=None),
                low=float(row[1]),
                high=float(row[2]),
                open=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in all_rows
        ]

        deduped = {candle.timestamp: candle for candle in candles}
        normalized = sorted(deduped.values(), key=lambda candle: candle.timestamp)
        if fetch_timeframe != timeframe:
            return resample_ohlcv(normalized, timeframe.value)
        return normalized

    async def get_available_periods(self, symbol: str) -> list[tuple[datetime, datetime]]:
        """Get available date ranges for Coinbase data."""
        end = datetime.now()
        start = end - timedelta(days=30)
        return [(start, end)]

    @staticmethod
    def _granularity_for_timeframe(timeframe: Timeframe) -> int:
        """Map app timeframes to Coinbase candle granularities."""
        granularity_map = {
            Timeframe.ONE_MINUTE: 60,
            Timeframe.THREE_MINUTE: 300,
            Timeframe.FIVE_MINUTE: 300,
            Timeframe.FIFTEEN_MINUTE: 900,
            Timeframe.THIRTY_MINUTE: 1800,
            Timeframe.ONE_HOUR: 3600,
            Timeframe.FOUR_HOUR: 3600,
            Timeframe.ONE_DAY: 86400,
            Timeframe.ONE_WEEK: 86400,
        }
        return granularity_map[timeframe]

    @staticmethod
    def _fetch_timeframe_for(timeframe: Timeframe) -> Timeframe:
        """Choose the nearest supported Coinbase timeframe to fetch before resampling."""
        fetch_map = {
            Timeframe.ONE_MINUTE: Timeframe.ONE_MINUTE,
            Timeframe.THREE_MINUTE: Timeframe.FIVE_MINUTE,
            Timeframe.FIVE_MINUTE: Timeframe.FIVE_MINUTE,
            Timeframe.FIFTEEN_MINUTE: Timeframe.FIFTEEN_MINUTE,
            Timeframe.THIRTY_MINUTE: Timeframe.THIRTY_MINUTE,
            Timeframe.ONE_HOUR: Timeframe.ONE_HOUR,
            Timeframe.FOUR_HOUR: Timeframe.ONE_HOUR,
            Timeframe.ONE_DAY: Timeframe.ONE_DAY,
            Timeframe.ONE_WEEK: Timeframe.ONE_DAY,
        }
        return fetch_map[timeframe]

    @staticmethod
    def _align_timestamp(value: datetime, granularity: int, round_up: bool) -> datetime:
        """Snap timestamps to candle boundaries to keep request windows stable."""
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)

        epoch_seconds = int(value.timestamp())
        remainder = epoch_seconds % granularity
        if remainder == 0:
            aligned = epoch_seconds
        elif round_up:
            aligned = epoch_seconds + (granularity - remainder)
        else:
            aligned = epoch_seconds - remainder

        return datetime.fromtimestamp(aligned, tz=UTC).replace(tzinfo=None)

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        """Format timestamps for Coinbase requests without microseconds."""
        return value.replace(tzinfo=UTC, microsecond=0).isoformat().replace("+00:00", "Z")


def get_fetcher(source: str, api_key: str | None = None, api_secret: str | None = None) -> BaseFetcher:
    """Factory function to get a fetcher by name.

    Args:
        source: Data source name (alpaca, coinbase)
        api_key: API key for the source (optional)
        api_secret: API secret for the source (optional)

    Returns:
        Configured fetcher instance

    Raises:
        ValueError: If source is not supported
    """
    # Local import avoids a circular dependency (AlpacaFetcher imports BaseFetcher).
    from stock_cycle_tracker.data.alpaca_fetcher import AlpacaFetcher

    fetchers = {
        "alpaca": AlpacaFetcher,
        "coinbase": CoinbaseFetcher,
    }

    if source not in fetchers:
        raise ValueError(f"Unsupported data source: {source}. Available: {list(fetchers.keys())}")

    return fetchers[source](api_key=api_key, api_secret=api_secret)
