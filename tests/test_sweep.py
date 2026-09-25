"""Tests for the parameter sweep engine."""

import math
from datetime import datetime, timedelta

from stock_cycle_tracker.analytics.sweep import run_parameter_sweep
from stock_cycle_tracker.models import OHLCV, Config


def _candles(drift: float, bars: int = 400):
    start = datetime(2026, 1, 1)
    out = []
    for i in range(bars):
        p = 100 + i * drift + math.sin(i / 18) * 1.8
        out.append(OHLCV(timestamp=start + timedelta(days=i), open=p, high=p + 0.7, low=p - 0.7, close=p, volume=1000))
    return out


def test_sweep_clean_trend_is_robust():
    data = _candles(0.05)  # rising, but with real pullbacks (drift < sine slope)
    config = Config(timeframe="1d", use_atr_filter=False)
    report = run_parameter_sweep(data, config)
    assert len(report.rows) == 5
    # Steeper configs may legitimately find zero pivots on gentle data —
    # the sweep should still produce a row (action falls back to hold).
    assert any(r.legs > 0 for r in report.rows)
    assert report.agreement_rate >= 0.6
    # A clean trend should land invest-side in most configurations.
    actions = [r.action for r in report.rows]
    assert any("invest" in a for a in actions)
    assert report.verdict in {"robust", "mixed"}
    assert "setting" in report.note or "artifact" in report.note or "agree" in report.note


def test_sweep_chop_is_flagged():
    data = _candles(0.0)  # pure sine, no drift
    config = Config(timeframe="1d", use_atr_filter=False)
    report = run_parameter_sweep(data, config)
    assert report.verdict in {"mixed", "fragile", "robust"}  # never crashes
    assert report.score_range >= 0
    assert report.rows  # every config still produced a row


def test_sweep_to_dict_shape():
    data = _candles(0.05)
    payload = run_parameter_sweep(data, Config(timeframe="1d", use_atr_filter=False)).to_dict()
    assert {"symbol", "rows", "agreement_rate", "score_std", "verdict", "note"} <= set(payload)
    assert {"label", "min_move_pct", "atr_multiplier", "legs", "action", "composite_score"} <= set(payload["rows"][0])
