"""Tests for the Alpaca HTTP client and fetcher (all HTTP mocked)."""

import io
import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from stock_cycle_tracker.data.alpaca_client import (
    AlpacaHTTPClient,
    RateBudgetExceeded,
    TokenBucket,
)
from stock_cycle_tracker.data.alpaca_fetcher import AlpacaFetcher
from stock_cycle_tracker.models import Timeframe


class _FakeResponse:
    def __init__(self, payload: dict | list, status: int = 200, headers: dict | None = None):
        self._buf = io.BytesIO(json.dumps(payload).encode())
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _client():
    return AlpacaHTTPClient(api_key_id="TESTKEY", api_secret_key="TESTSECRET")


def test_credentials_detected_from_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "abc")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "def")
    assert AlpacaHTTPClient().has_credentials


def test_get_bars_paginates_via_next_page_token():
    client = _client()
    pages = [
        {"bars": [{"t": 1700000000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}], "next_page_token": "p2"},
        {"bars": [{"t": 1700000060, "o": 1.5, "h": 2.5, "l": 1, "c": 2, "v": 11}], "next_page_token": None},
    ]
    with patch.object(client, "_request", side_effect=pages) as req:
        bars = client.get_bars("AAPL", "1Min", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert len(bars) == 2
    assert req.call_count == 2


def test_get_snapshots_batches_symbols():
    client = _client()
    payload = {"AAPL": {"latestTrade": {"p": 100}}}
    with patch.object(client, "_request", return_value=payload) as req:
        out = client.get_snapshots(["AAPL", "MSFT"])
    assert "AAPL" in out
    assert req.call_args.kwargs["params"]["symbols"] == "AAPL,MSFT"


def test_error_messages_scrub_auth_material():
    """A 403 whose body echoes the keys must not leak them via RuntimeError."""
    import urllib.error

    client = _client()

    def boom(*a, **k):
        raise urllib.error.HTTPError(
            "url", 403, "Forbidden", hdrs=None,
            fp=io.BytesIO(b"forbidden TESTKEY/TESTSECRET"),
        )

    with patch("stock_cycle_tracker.data.alpaca_client.urlopen", side_effect=boom):
        with pytest.raises(RuntimeError) as excinfo:
            client.get_clock()
    assert "TESTKEY" not in str(excinfo.value)
    assert "TESTSECRET" not in str(excinfo.value)


def test_429_retries_once_then_succeeds():
    import urllib.error

    client = _client()
    calls = {"n": 0}

    def flaky(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests",
                hdrs={"Retry-After": "0"}, fp=io.BytesIO(b"{}"),
            )
        return _FakeResponse({"is_open": True})

    with patch("stock_cycle_tracker.data.alpaca_client.urlopen", side_effect=flaky):
        assert client.get_clock()["is_open"] is True
    assert calls["n"] == 2


def test_token_bucket_blocks_when_exhausted():
    bucket = TokenBucket(per_minute=2)
    bucket.acquire()
    bucket.acquire()
    with pytest.raises(RateBudgetExceeded):
        bucket.acquire(timeout=0.05)


def test_paper_host_frozen():
    import stock_cycle_tracker.data.alpaca_client as mod
    assert "paper-api" in mod.TRADING_HOST
    assert "api.alpaca.markets" not in mod.TRADING_HOST or "paper" in mod.TRADING_HOST


def _fetcher():
    return AlpacaFetcher(client=_client())


@pytest.mark.asyncio
async def test_fetcher_requires_credentials():
    fetcher = AlpacaFetcher(client=AlpacaHTTPClient(api_key_id="", api_secret_key=""))
    with pytest.raises(RuntimeError, match="ALPACA_API_KEY_ID"):
        await fetcher.fetch_ohlcv("AAPL", Timeframe.ONE_DAY, datetime(2026, 1, 1), datetime(2026, 2, 1))


@pytest.mark.asyncio
async def test_fetcher_maps_bars_and_respects_limit():
    fetcher = _fetcher()
    # Five consecutive WEEKDAYS (Jan 2, 5, 6, 7, 8 2026) at 17:00 UTC = noon ET,
    # so the regular-hours filter keeps all of them.
    days = [datetime(2026, 1, 2, 17, 0, tzinfo=UTC) + __import__("datetime").timedelta(days=d) for d in (0, 3, 4, 5, 6)]
    bars = {"bars": [
        {"t": int(d.timestamp()), "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100.5 + i, "v": 1000}
        for i, d in enumerate(days)
    ], "next_page_token": None}
    with patch.object(fetcher.client, "get_bars", return_value=bars["bars"]) as gb:
        out = await fetcher.fetch_ohlcv("AAPL", Timeframe.ONE_DAY, datetime(2026, 1, 1), datetime(2026, 1, 10), limit=3)
    assert len(out) == 3
    assert [c.close for c in out] == [102.5, 103.5, 104.5]
    assert gb.call_args[0][1] == "1Day"  # native timeframe mapping


@pytest.mark.asyncio
async def test_fetcher_resamples_3m_from_1min():
    fetcher = _fetcher()
    base = int(datetime(2026, 1, 7, 17, 0, tzinfo=UTC).timestamp())
    minute_bars = [
        {"t": base + i * 60, "o": 100, "h": 101, "l": 99, "c": 100, "v": 10}
        for i in range(6)
    ]
    with patch.object(fetcher.client, "get_bars", return_value=minute_bars) as gb:
        out = await fetcher.fetch_ohlcv("AAPL", Timeframe.THREE_MINUTE, datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert gb.call_args[0][1] == "1Min"
    assert len(out) == 2  # 6 one-minute bars -> 2 three-minute bars
    assert out[0].volume == 30


@pytest.mark.asyncio
async def test_fetcher_filters_pre_and_post_market():
    fetcher = _fetcher()
    # 09:15 ET (pre), 12:00 ET (session), 16:30 ET (post) on a Wednesday.
    # 2026-01-07 is a Wednesday; UTC stamps: 14:15, 17:00, 21:30
    bars = [
        {"t": int(datetime(2026, 1, 7, 14, 15, tzinfo=UTC).timestamp()), "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
        {"t": int(datetime(2026, 1, 7, 17, 0, tzinfo=UTC).timestamp()), "o": 2, "h": 2, "l": 2, "c": 2, "v": 2},
        {"t": int(datetime(2026, 1, 7, 21, 30, tzinfo=UTC).timestamp()), "o": 3, "h": 3, "l": 3, "c": 3, "v": 3},
    ]
    with patch.object(fetcher.client, "get_bars", return_value=bars):
        out = await fetcher.fetch_ohlcv("AAPL", Timeframe.FIVE_MINUTE, datetime(2026, 1, 7), datetime(2026, 1, 8))
    assert len(out) == 1  # only the 12:00 ET bar survives
    assert out[0].close == 2


@pytest.mark.asyncio
async def test_fetcher_tolerates_regular_hours_disabled():
    fetcher = AlpacaFetcher(client=_client(), regular_hours_only=False)
    bars = [
        {"t": int(datetime(2026, 1, 7, 21, 30, tzinfo=UTC).timestamp()), "o": 3, "h": 3, "l": 3, "c": 3, "v": 3},
    ]
    with patch.object(fetcher.client, "get_bars", return_value=bars):
        out = await fetcher.fetch_ohlcv("AAPL", Timeframe.FIVE_MINUTE, datetime(2026, 1, 7), datetime(2026, 1, 8))
    assert len(out) == 1


def test_compare_feeds_divergence_math():
    client = _client()
    base = 1767283200
    iex = {"bars": [{"t": base + i * 3600, "c": 100 + i} for i in range(10)]}
    sip = {"bars": [{"t": base + i * 3600, "c": (100 + i) * 1.001} for i in range(10)]}  # +0.1%
    with patch.object(client, "_request", side_effect=[iex, sip]):
        out = client.compare_feeds("AAPL", "1Hour", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert out["shared_bars"] == 10
    assert 0.09 < out["mean_close_divergence_pct"] < 0.11
    assert out["verdict"] == "minor"


def test_compare_feeds_reports_sip_unavailable():
    client = _client()
    iex = {"bars": [{"t": 1767283200, "c": 100}]}
    with patch.object(client, "_request", side_effect=[iex, RuntimeError("SIP not subscribed")]):
        out = client.compare_feeds("AAPL", "1Hour", datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert out["sip"]["error"] and "SIP" in out["sip"]["error"]
    assert "note" in out


@pytest.mark.asyncio
async def test_live_poller_broadcast_and_budget():
    """The SSE poller: open market collects and broadcasts quotes."""
    from unittest.mock import patch

    from stock_cycle_tracker.web.live_stream import LiveQuotePoller

    poller = LiveQuotePoller()
    queue = poller.subscribe()

    class _Hours:
        def phase(self, moment=None):
            return {"phase": "open"}

    poller.hours = _Hours()
    poller.client = AlpacaHTTPClient(api_key_id="", api_secret_key="")  # keyless → Yahoo path
    with patch("stock_cycle_tracker.web.live_stream.fetch_yahoo_movers",
               return_value=[{"symbol": "AAPL", "change_pct": 1.0, "last": 123.45}]):
        quotes = await poller._collect()
    assert quotes and quotes[0]["symbol"] == "AAPL" and quotes[0]["price"] == 123.45

    poller._broadcast(quotes[0])
    assert queue.get_nowait()["symbol"] == "AAPL"
    poller.unsubscribe(queue)
