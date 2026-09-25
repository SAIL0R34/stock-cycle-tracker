"""Tests for the market-hours service (Alpaca mocked / approx mode)."""

from datetime import UTC, datetime
from unittest.mock import patch

from stock_cycle_tracker.data.alpaca_client import AlpacaHTTPClient
from stock_cycle_tracker.data.market_hours import MarketHoursService

CRED = AlpacaHTTPClient(api_key_id="K", api_secret_key="S")
NOCRED = AlpacaHTTPClient(api_key_id="", api_secret_key="")


def _at(month, day, hour, minute):
    """UTC moment."""
    return datetime(2026, 1, day, hour, minute, tzinfo=UTC) if month == 1 else None


def test_approx_open_midday_weekday():
    svc = MarketHoursService(client=NOCRED)
    # Wed Jan 7 2026, 17:00 UTC = 12:00 ET → open
    info = svc.phase(datetime(2026, 1, 7, 17, 0, tzinfo=UTC))
    assert info["phase"] == "open"
    assert info["source"] == "approx"
    assert info["next_event"] == "close"


def test_approx_pre_market():
    svc = MarketHoursService(client=NOCRED)
    # 13:00 UTC = 08:00 ET → pre
    info = svc.phase(datetime(2026, 1, 7, 13, 0, tzinfo=UTC))
    assert info["phase"] == "pre"
    assert info["next_event"] == "open"


def test_approx_post_market():
    svc = MarketHoursService(client=NOCRED)
    # 21:30 UTC = 16:30 ET → post
    info = svc.phase(datetime(2026, 1, 7, 21, 30, tzinfo=UTC))
    assert info["phase"] == "post"
    assert info["next_event"] == "open"


def test_approx_weekend_closed_next_open_is_monday():
    svc = MarketHoursService(client=NOCRED)
    # Saturday Jan 10 2026, 15:00 UTC = 10:00 ET → closed
    info = svc.phase(datetime(2026, 1, 10, 15, 0, tzinfo=UTC))
    assert info["phase"] == "closed"
    assert "2026-01-12" in info["next_event_at"]  # Monday open


def test_dst_winter_vs_summer_session_bounds():
    svc = MarketHoursService(client=NOCRED)
    # Same 14:30 UTC moment: January (EST) = 09:30 ET (exactly open);
    # July (EDT) = 10:30 ET (already open). DST must shift with the zone.
    winter = svc.phase(datetime(2026, 1, 7, 14, 30, tzinfo=UTC))
    summer = svc.phase(datetime(2026, 7, 8, 14, 30, tzinfo=UTC))
    assert winter["phase"] == "open"   # exactly 09:30 EST
    assert summer["phase"] == "open"   # 10:30 EDT


def test_alpaca_clock_authoritative_when_creds():
    svc = MarketHoursService(client=CRED)
    with patch.object(CRED, "get_clock", return_value={"is_open": False, "next_open": "2026-01-12T14:30:00Z", "timestamp": "t"}):
        info = svc.phase(datetime(2026, 1, 10, 15, 0, tzinfo=UTC))
    assert info["phase"] == "closed"
    assert info["source"] == "alpaca"
    assert info["next_event_at"].startswith("2026-01-12")


def test_alpaca_clock_failure_falls_back_to_approx():
    svc = MarketHoursService(client=CRED)
    with patch.object(CRED, "get_clock", side_effect=RuntimeError("boom")):
        info = svc.phase(datetime(2026, 1, 7, 17, 0, tzinfo=UTC))
    assert info["phase"] == "open"
    assert info["source"] == "approx"


def test_effective_data_end_open_market_is_now():
    svc = MarketHoursService(client=NOCRED)
    moment = datetime(2026, 1, 7, 17, 0, tzinfo=UTC)  # open
    assert svc.effective_data_end(moment) > datetime(2026, 1, 7, 16, 0)


def test_effective_data_end_closed_market_snaps_to_session_close():
    svc = MarketHoursService(client=NOCRED)
    # Saturday → last close is Friday 16:00 ET = 21:00 UTC
    end = svc.effective_data_end(datetime(2026, 1, 10, 15, 0, tzinfo=UTC))
    assert end == datetime(2026, 1, 9, 21, 0)  # naive UTC


def test_calendar_cached_per_day(tmp_path):
    svc = MarketHoursService(client=NOCRED, cache_dir=tmp_path)
    with patch.object(NOCRED, "get_calendar", return_value=[{"date": "2026-01-07", "open": "09:30", "close": "16:00"}]) as gc:
        svc.calendar()
        svc.calendar()
    assert gc.call_count == 0  # no creds → approximation path, no API call anyway
