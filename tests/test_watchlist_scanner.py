"""Tests for the watchlist store and scanner (no real HTTP)."""

import math
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from stock_cycle_tracker.models import Config, OHLCV
from stock_cycle_tracker.watchlist.scanner import ScanRow, ScanService
from stock_cycle_tracker.watchlist.store import DEFAULT_WATCHLIST, WatchlistStore, filter_universe, normalize_symbol

# ── store ──────────────────────────────────────────────────────────────


def test_normalize_symbol_shapes():
    assert normalize_symbol(" $aapl ") == "AAPL"
    assert normalize_symbol("nvda") == "NVDA"
    assert normalize_symbol("BRK.B") == "BRK.B"
    assert normalize_symbol("BTC-USD") == ""  # not a ticker shape
    assert normalize_symbol("") == ""


def test_watchlist_roundtrip_and_dedup(tmp_path):
    store = WatchlistStore(tmp_path / "watchlist.json")
    saved = store.save([" $spy ", "spy", "aapl", "not a ticker!"])
    assert saved == ["SPY", "AAPL"]
    assert store.load() == ["SPY", "AAPL"]


def test_watchlist_reset(tmp_path):
    store = WatchlistStore(tmp_path / "watchlist.json")
    store.save(["MSFT"])
    assert store.reset() == DEFAULT_WATCHLIST


def test_watchlist_corrupt_file_falls_back(tmp_path):
    path = tmp_path / "watchlist.json"
    path.write_text("{broken")
    assert WatchlistStore(path).load() == DEFAULT_WATCHLIST


def test_filter_universe_price_gate_and_always_keep():
    rows = [
        {"symbol": "AAA", "last_price": 0.4},
        {"symbol": "BBB", "last_price": 50.0},
        {"symbol": "CCC", "last_price": None},  # data gap → kept
    ]
    kept, filtered = filter_universe(rows, min_price=1.0, always_keep={"AAA"})
    assert [r["symbol"] for r in kept] == ["AAA", "BBB", "CCC"]  # AAA kept by explicit watch
    assert filtered == []


# ── scanner ────────────────────────────────────────────────────────────


def _candles(bars: int = 320, drift: float = 0.05) -> list[OHLCV]:
    start = datetime(2026, 1, 1)
    out = []
    for i in range(bars):
        p = 100 + i * drift + math.sin(i / 20) * 1.5
        out.append(OHLCV(timestamp=start + timedelta(days=i), open=p, high=p + 0.8, low=p - 0.8, close=p, volume=1000))
    return out


class _FakeHours:
    """Closed market, fixed session end — deterministic cache keys."""

    def phase(self, moment=None):
        return {"phase": "closed", "next_event": "open", "next_event_at": "2026-01-02T14:30:00"}

    def effective_data_end(self, moment=None):
        return datetime(2026, 1, 30)


class _FakeAnalysisService:
    """Stands in for AnalysisService; counts constructions per symbol."""

    constructions: list[str] = []
    fail_symbols: set[str] = set()

    def __init__(self, config, data_loader=None, data_end=None, use_cache=False):
        self.config = config
        _FakeAnalysisService.constructions.append(config.symbol)

    async def run_analysis(self):
        from stock_cycle_tracker.models import AnalysisResult

        symbol = self.config.symbol
        if symbol in _FakeAnalysisService.fail_symbols:
            raise RuntimeError(f"no bars for {symbol}")
        data = _candles(drift=0.06 if symbol == "AAPL" else -0.06)
        result = AnalysisResult.model_construct()
        result.raw_data = data
        result.legs = []
        result.pivots = []
        result.forming_leg = None
        result.decision_brief = None
        # Minimal duck-typed object the scanner reads
        return _FakeResult(symbol, data)


class _FakeResult:
    def __init__(self, symbol, data):
        from stock_cycle_tracker.models import (
            DecisionBrief, DecisionInvalidation, SwingLeg,
        )
        self.raw_data = data
        self.legs = []
        self.pivots = []
        up = symbol == "AAPL"
        self.forming_leg = SwingLeg(
            leg_id=1, start_pivot_id=1, end_pivot_id=2,
            direction="up" if up else "down",
            start_timestamp=data[0].timestamp, end_timestamp=data[-1].timestamp,
            start_price=100, end_price=110 if up else 95,
            absolute_change=10, percent_change=10.0 if up else -5.0,
            duration_seconds=86400, duration_minutes=1440, duration_bars=len(data),
        )
        score = 60.0 if up else -35.0
        self.decision_brief = DecisionBrief(
            action="strong_invest" if up else "divest",
            composite_score=score,
            conviction=0.42,
            quality="moderate",
            summary=f"{symbol} test brief",
            invalidations=[DecisionInvalidation(price=90.0, kind="structure_break", flips_toward="divest", rationale="test")],
        )


def _scanner(tmp_path):
    service = ScanService(store=WatchlistStore(tmp_path / "wl.json"), hours=_FakeHours())
    return service


@pytest.mark.asyncio
async def test_scan_ranks_by_absolute_score_and_shapes_rows(tmp_path):
    service = _scanner(tmp_path)
    service.store.save(["MSFT", "AAPL"])
    _FakeAnalysisService.constructions.clear()

    with patch("stock_cycle_tracker.watchlist.scanner.AnalysisService", _FakeAnalysisService):
        result = await service.scan(Config(timeframe="1d", use_atr_filter=False))

    payload = result.to_dict()
    assert payload["market_phase"] == "closed"
    assert payload["rows"][0]["symbol"] == "AAPL"  # |60| > |-35|
    assert payload["rows"][0]["action"] == "strong_invest"
    assert payload["rows"][0]["top_invalidation_price"] == 90.0
    assert payload["rows"][1]["symbol"] == "MSFT"
    assert payload["rows"][1]["forming_pct"] == -5.0
    assert set(_FakeAnalysisService.constructions) == {"AAPL", "MSFT"}


@pytest.mark.asyncio
async def test_scan_row_cache_makes_warm_rescan_free(tmp_path):
    service = _scanner(tmp_path)
    service.store.save(["AAPL"])
    _FakeAnalysisService.constructions.clear()

    with patch("stock_cycle_tracker.watchlist.scanner.AnalysisService", _FakeAnalysisService):
        await service.scan(Config(timeframe="1d", use_atr_filter=False))
        cold = len(_FakeAnalysisService.constructions)
        await service.scan(Config(timeframe="1d", use_atr_filter=False))
        warm = len(_FakeAnalysisService.constructions) - cold

    assert cold == 1
    assert warm == 0  # closed market + same session end → cached rows


@pytest.mark.asyncio
async def test_scan_isolates_per_symbol_errors(tmp_path):
    service = _scanner(tmp_path)
    service.store.save(["BAD", "GOOD"])
    _FakeAnalysisService.constructions.clear()
    _FakeAnalysisService.fail_symbols = {"BAD"}

    with patch("stock_cycle_tracker.watchlist.scanner.AnalysisService", _FakeAnalysisService):
        result = await service.scan(Config(timeframe="1d", use_atr_filter=False))

    errors = [r for r in result.rows if r.error]
    good = [r for r in result.rows if not r.error]
    assert len(errors) == 1 and errors[0].symbol == "BAD"
    assert len(good) == 1 and good[0].symbol == "GOOD"
    _FakeAnalysisService.fail_symbols = set()


def test_scan_config_is_lighter_than_base(tmp_path):
    service = _scanner(tmp_path)
    base = Config(
        timeframe="1d",
        decision_walk_forward_checkpoints=12,
        enable_gold_correlation_analysis=True,
        enable_nasdaq_correlation_analysis=True,
    )
    light = service._scan_config(base)
    assert light.decision_walk_forward_checkpoints == 4
    assert light.enable_gold_correlation_analysis is False
    assert light.enable_nasdaq_correlation_analysis is False
    assert light.enable_pattern_recognition is True       # engines stay on
    assert light.enable_decision_memory is True


@pytest.mark.asyncio
async def test_scan_caps_watchlist_size(tmp_path):
    service = _scanner(tmp_path)
    service.store.save([f"S{i}" for i in range(30)])  # over MAX_SCAN_SYMBOLS
    _FakeAnalysisService.constructions.clear()
    with patch("stock_cycle_tracker.watchlist.scanner.AnalysisService", _FakeAnalysisService):
        result = await service.scan(Config(timeframe="1d", use_atr_filter=False))
    assert len(_FakeAnalysisService.constructions) == 20
    assert len(result.rows) == 20
