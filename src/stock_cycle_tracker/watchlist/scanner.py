"""Watchlist scanner: decision-engine snapshot across many tickers.

Runs the *light* pipeline per symbol (structural decision scorecard with a
reduced walk-forward replay, correlations off) and ranks the watchlist by
|composite score|. Design notes:

* **Sequential, staggered** — symbols run one at a time with a small delay.
  The decision memory is a JSON file; serialising runs removes every
  read-modify-write race. Cold scans of 8 liquid names take ~10s; warm
  scans (row cache + bar cache) return in well under a second.
* **Row cache** keyed by (symbol, timeframe, lookback, last candle, session
  data-end): while the market is closed, rescans are free; while open, a
  row refreshes when a newer candle exists.
* **Decision memory stays ON** — logging is deduplicated by symbol +
  candle timestamp, so repeated scans never double-count, and every
  watchlist name accumulates its own graded track record.
* **Errors are per-symbol**: one bad ticker yields an error row, never a
  failed scan.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from stock_cycle_tracker.data.market_hours import MarketHoursService
from stock_cycle_tracker.models import Config
from stock_cycle_tracker.services.analysis_service import AnalysisService
from stock_cycle_tracker.watchlist.store import WatchlistStore, filter_universe

logger = logging.getLogger("stock_cycle_tracker.watchlist.scanner")

STAGGER_SECONDS = 0.25
MAX_SCAN_SYMBOLS = 20


@dataclass
class ScanRow:
    symbol: str
    last_price: Optional[float] = None
    action: str = "error"
    composite_score: float = 0.0
    conviction: float = 0.0
    quality: str = "low"
    forming_pct: Optional[float] = None
    top_invalidation_price: Optional[float] = None
    top_invalidation_flips: Optional[str] = None
    summary: str = ""
    last_candle: Optional[str] = None
    stale: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "last_price": self.last_price,
            "action": self.action,
            "composite_score": self.composite_score,
            "conviction": self.conviction,
            "quality": self.quality,
            "forming_pct": self.forming_pct,
            "top_invalidation_price": self.top_invalidation_price,
            "top_invalidation_flips": self.top_invalidation_flips,
            "summary": self.summary,
            "last_candle": self.last_candle,
            "stale": self.stale,
            "error": self.error,
        }


@dataclass
class ScanResult:
    rows: list[ScanRow] = field(default_factory=list)
    filtered: list[dict] = field(default_factory=list)
    ran_at: Optional[str] = None
    market_phase: str = "unknown"
    duration_seconds: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        ranked = sorted(self.rows, key=lambda r: abs(r.composite_score), reverse=True)
        return {
            "rows": [r.to_dict() for r in ranked],
            "filtered": self.filtered,
            "ran_at": self.ran_at,
            "market_phase": self.market_phase,
            "duration_seconds": round(self.duration_seconds, 2),
            "note": self.note,
        }


class ScanService:
    """Stateful scanner (one per app process; holds the row cache)."""

    def __init__(self, store: WatchlistStore | None = None, hours: MarketHoursService | None = None):
        self.store = store or WatchlistStore()
        self.hours = hours or MarketHoursService()
        self._row_cache: dict[tuple, dict] = {}
        self.last_result: ScanResult | None = None

    def _scan_config(self, base: Config) -> Config:
        """Lighter pipeline for scanning: fewer replay checkpoints, no
        cross-asset fetches. Pattern/decision engines stay on."""
        overrides = base.model_dump()
        overrides.update(
            {
                "decision_walk_forward_checkpoints": min(4, base.decision_walk_forward_checkpoints),
                "enable_gold_correlation_analysis": False,
                "enable_nasdaq_correlation_analysis": False,
                "enable_oil_correlation_analysis": False,
                "save_chart": False,
                "save_csv": False,
            }
        )
        return Config(**overrides)

    async def scan(self, base_config: Config) -> ScanResult:
        started = time.monotonic()
        phase_info = self.hours.phase()
        data_end = self.hours.effective_data_end()

        symbols = self.store.load()[:MAX_SCAN_SYMBOLS]
        result = ScanResult(market_phase=phase_info["phase"])

        for symbol in symbols:
            row = await self._scan_symbol(symbol, base_config, data_end)
            result.rows.append(row)
            await asyncio.sleep(STAGGER_SECONDS)

        kept, filtered = filter_universe(
            [r.to_dict() for r in result.rows],
            min_price=1.0,
            always_keep=set(symbols),
        )
        # Keep original ScanRow objects for kept rows; filtered go to the side list.
        kept_symbols = {r["symbol"] for r in kept}
        result.rows = [r for r in result.rows if r.symbol in kept_symbols]
        result.filtered = filtered

        result.duration_seconds = time.monotonic() - started
        result.ran_at = datetime.utcnow().isoformat()
        result.note = (
            f"{len(result.rows)} symbols scanned in {result.duration_seconds:.1f}s "
            f"(market {result.market_phase}). Ranked by |score|; click a row for the full dashboard."
        )
        self.last_result = result
        return result

    def _row_cache_key(self, symbol: str, config: Config, data_end: datetime) -> tuple:
        """Rows are reusable until a new candle could exist: within a session
        the key rotates every timeframe interval; across a closed session it
        is stable, making rescans free while the market is shut."""
        phase = self.hours.phase()["phase"]
        if phase == "open":
            interval = {
                "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800,
            }.get(config.timeframe.value, 300)
            bucket = int(time.time() // interval)
        else:
            bucket = "closed"
        return (symbol, config.timeframe.value, config.lookback_period, data_end.isoformat(), bucket)

    async def _scan_symbol(
        self,
        symbol: str,
        base_config: Config,
        data_end: datetime,
    ) -> ScanRow:
        config = self._scan_config(base_config)
        config = config.model_copy(update={"symbol": symbol})
        cache_key = self._row_cache_key(symbol, base_config, data_end)
        cached = self._row_cache.get(cache_key)
        if cached is not None:
            return ScanRow(**cached)

        service = AnalysisService(config, data_end=data_end, use_cache=True)
        try:
            result = await service.run_analysis()
        except Exception as exc:  # noqa: BLE001 - per-symbol isolation
            logger.warning("Scan failed for %s: %s", symbol, exc)
            return ScanRow(symbol=symbol, error=str(exc)[:200])

        brief = result.decision_brief
        row = ScanRow(symbol=symbol)
        if result.forming_leg:
            row.last_price = result.forming_leg.end_price
            row.forming_pct = round(result.forming_leg.percent_change, 2)
        elif result.legs:
            row.last_price = result.legs[-1].end_price
        row.last_candle = result.raw_data[-1].timestamp.isoformat() if result.raw_data else None
        row.stale = bool(
            row.last_candle
            and result.raw_data
            and result.raw_data[-1].timestamp < data_end
        )
        if brief:
            row.action = brief.action
            row.composite_score = brief.composite_score
            row.conviction = brief.conviction
            row.quality = brief.quality
            row.summary = brief.summary
            if brief.invalidations:
                nearest = min(brief.invalidations, key=lambda i: abs(i.price - (row.last_price or i.price)))
                row.top_invalidation_price = nearest.price
                row.top_invalidation_flips = nearest.flips_toward
        self._row_cache[cache_key] = row.to_dict()
        # Bound the cache: keep the newest ~200 entries.
        if len(self._row_cache) > 200:
            self._row_cache = dict(list(self._row_cache.items())[-200:])
        return row
