"""Tests for the paper-trading stack: broker seam, risk gate, service flow."""

from datetime import datetime
from unittest.mock import patch

import pytest

from stock_cycle_tracker.models import Config, DecisionBrief
from stock_cycle_tracker.trading.broker import (
    AlpacaPaperBrokerClient,
    DisabledBrokerClient,
    get_broker,
)
from stock_cycle_tracker.trading.risk import RiskLimits, pre_trade_check
from stock_cycle_tracker.trading.service import PaperTradingService
from stock_cycle_tracker.trading.trade_log import TradeLog


def _brief(action="invest", conviction=0.5, quality="moderate", last_price=100.0):
    return {
        "action": action,
        "conviction": conviction,
        "quality": quality,
        "last_price": last_price,
    }


def _account(cash=10000.0, equity=10000.0):
    return {"cash": cash, "equity": equity}


# ── broker seam ────────────────────────────────────────────────────────


def test_disabled_broker_when_off_or_unconfigured():
    assert isinstance(get_broker(enabled=False), DisabledBrokerClient)
    assert isinstance(get_broker(enabled=True, client=_nocred_client()), DisabledBrokerClient)


def test_paper_broker_when_enabled_and_credentialed():
    broker = get_broker(enabled=True, client=_cred_client())
    assert isinstance(broker, AlpacaPaperBrokerClient)


def test_paper_broker_rejects_bad_orders():
    broker = AlpacaPaperBrokerClient(_cred_client())
    with pytest.raises(ValueError):
        broker.place_market_order("AAPL", "buy", 0)
    with pytest.raises(ValueError):
        broker.place_market_order("AAPL", "hodl", 5)


def test_paper_broker_market_day_payload():
    client = _cred_client()
    broker = AlpacaPaperBrokerClient(client)
    with patch.object(client, "submit_order", return_value={"id": "o1", "status": "accepted"}) as so:
        order = broker.place_market_order("aapl", "buy", 10)
    assert order["id"] == "o1"
    body = so.call_args[0][0]
    assert body == {"symbol": "AAPL", "qty": 10.0, "side": "buy", "type": "market", "time_in_force": "day"}


def _cred_client():
    from stock_cycle_tracker.data.alpaca_client import AlpacaHTTPClient
    return AlpacaHTTPClient(api_key_id="K", api_secret_key="S")


def _nocred_client():
    from stock_cycle_tracker.data.alpaca_client import AlpacaHTTPClient
    return AlpacaHTTPClient(api_key_id="", api_secret_key="")


# ── risk gate ──────────────────────────────────────────────────────────


def test_risk_gate_vetoes_low_quality_and_conviction():
    decision = pre_trade_check("AAPL", "buy", _brief(quality="low"), _account(), [])
    assert not decision.allowed
    assert any("quality" in r for r in decision.refusals)

    decision = pre_trade_check("AAPL", "buy", _brief(conviction=0.1), _account(), [])
    assert not decision.allowed
    assert any("conviction" in r for r in decision.refusals)


def test_risk_gate_vetoes_hold_and_wrong_side():
    assert not pre_trade_check("AAPL", "buy", _brief(action="hold"), _account(), []).allowed
    assert not pre_trade_check("AAPL", "buy", _brief(action="divest"), _account(), []).allowed


def test_risk_gate_sizes_to_position_pct():
    decision = pre_trade_check(
        "AAPL", "buy", _brief(last_price=100.0), _account(cash=10000.0), [],
        RiskLimits(max_position_pct=5.0),
    )
    assert decision.allowed and decision.qty == 5.0  # 5% of 10k = $500 = 5 shares


def test_risk_gate_blocks_averaging_and_symbol_cap():
    limits = RiskLimits(max_active_symbols=2, block_add_to_loser=True)
    held = [{"symbol": "AAPL"}]
    assert not pre_trade_check("AAPL", "buy", _brief(), _account(), held, limits).allowed  # averaging
    decision = pre_trade_check("MSFT", "buy", _brief(), _account(), held, limits)
    assert decision.allowed  # second symbol ok
    held2 = held + [{"symbol": "MSFT"}]
    assert not pre_trade_check("NVDA", "buy", _brief(), _account(), held2, limits).allowed  # cap


def test_risk_gate_daily_loss_brake():
    decision = pre_trade_check(
        "AAPL", "buy", _brief(), _account(cash=9000, equity=9000), [],
        RiskLimits(max_daily_loss_pct=3.0), day_start_equity=10000.0,
    )
    assert not decision.allowed
    assert any("loss brake" in r for r in decision.refusals)


# ── service: preview → confirm flow ────────────────────────────────────


class _FakeState:
    def __init__(self, result):
        self.result = result


class _FakeResult:
    def __init__(self, symbol, brief: DecisionBrief):
        from stock_cycle_tracker.models import AnalysisMetadata
        self.metadata = AnalysisMetadata(
            symbol=symbol, timeframe="1d", pivot_method="zigzag",
            start_date=datetime(2026, 1, 1), end_date=datetime(2026, 2, 1),
            total_candles=10, total_pivots=2, total_legs=1, run_timestamp=datetime(2026, 2, 1),
        )
        self.decision_brief = brief


def _service(tmp_path, enabled=True):
    client = _cred_client()
    service = PaperTradingService(
        broker=AlpacaPaperBrokerClient(client) if enabled else DisabledBrokerClient(),
        client=client,
        log=TradeLog(tmp_path / "trade_log.jsonl"),
    )
    return service, client


def _state_with_brief(symbol="AAPL", action="invest", conviction=0.5):
    brief = DecisionBrief(
        action=action, composite_score=30.0, conviction=conviction,
        quality="moderate", summary="test", last_price=100.0,
    )
    return _FakeState(_FakeResult(symbol, brief))


def test_preview_issues_single_use_token_and_places_nothing(tmp_path):
    service, client = _service(tmp_path)
    config = Config(trading_enabled=True)
    with patch.object(client, "get_account", return_value=_account()), \
         patch.object(client, "get_positions", return_value=[]):
        preview = service.preview(config, _state_with_brief(), "AAPL", "buy")
    assert preview["ok"] and preview["qty"] == 5.0
    assert "confirmation_id" in preview

    # confirm consumes the token; second use fails
    with patch.object(client, "get_account", return_value=_account()), \
         patch.object(client, "get_positions", return_value=[]), \
         patch.object(client, "submit_order", return_value={"id": "o1", "status": "accepted"}):
        result = service.confirm(config, preview["confirmation_id"])
    assert result["ok"] and result["order"]["id"] == "o1"
    again = service.confirm(config, preview["confirmation_id"])
    assert not again["ok"] and "already-used" in again["error"]


def test_preview_refusal_gives_no_token(tmp_path):
    service, client = _service(tmp_path)
    preview = service.preview(Config(trading_enabled=True), _state_with_brief(conviction=0.05), "AAPL", "buy")
    with patch.object(client, "get_account", return_value=_account()), \
         patch.object(client, "get_positions", return_value=[]):
        preview = service.preview(Config(trading_enabled=True), _state_with_brief(conviction=0.05), "AAPL", "buy")
    assert not preview["ok"]
    assert "confirmation_id" not in preview


def test_preview_for_symbol_without_analysis_refuses(tmp_path):
    service, _ = _service(tmp_path)
    preview = service.preview(Config(trading_enabled=True), _state_with_brief("AAPL"), "MSFT", "buy")
    assert not preview["ok"]
    assert any("no current analysis" in r for r in preview["refusals"])


def test_confirmation_expires(tmp_path):
    service, client = _service(tmp_path)
    config = Config(trading_enabled=True)
    with patch.object(client, "get_account", return_value=_account()), \
         patch.object(client, "get_positions", return_value=[]):
        preview = service.preview(config, _state_with_brief(), "AAPL", "buy")
    token = preview["confirmation_id"]
    # Force expiry
    service._pending[token]["expires_at"] = 0.0
    result = service.confirm(config, token)
    assert not result["ok"] and "expired" in result["error"]


def test_disabled_service_refuses_preview(tmp_path):
    service, _ = _service(tmp_path, enabled=False)
    preview = service.preview(Config(trading_enabled=False), _state_with_brief(), "AAPL", "buy")
    assert not preview["ok"]


def test_trade_log_records_events(tmp_path):
    service, client = _service(tmp_path)
    config = Config(trading_enabled=True)
    with patch.object(client, "get_account", return_value=_account()), \
         patch.object(client, "get_positions", return_value=[]), \
         patch.object(client, "submit_order", return_value={"id": "o1", "status": "accepted"}):
        preview = service.preview(config, _state_with_brief(), "AAPL", "buy")
        service.confirm(config, preview["confirmation_id"])
    events = service.log.tail()
    kinds = [e["event"] for e in events]
    assert "preview" in kinds and "order_submitted" in kinds


# ── chart bracket orders ───────────────────────────────────────────────


def test_bracket_payload_limit_and_market():
    client = _cred_client()
    broker = AlpacaPaperBrokerClient(client)
    with patch.object(client, "submit_order", return_value={"id": "b1"}) as so:
        broker.place_bracket_order("AAPL", "buy", 5, stop_price=90.0, limit_price=100.0)
    body = so.call_args[0][0]
    assert body["order_class"] == "bracket"
    assert body["type"] == "limit" and body["limit_price"] == 100.0
    assert body["stop_loss"] == {"stop_price": 90.0}

    with patch.object(client, "submit_order", return_value={"id": "b2"}) as so:
        broker.place_bracket_order("AAPL", "buy", 5, stop_price=90.0)  # market entry
    body = so.call_args[0][0]
    assert body["type"] == "market" and "limit_price" not in body


def test_bracket_rejects_bad_stops():
    broker = AlpacaPaperBrokerClient(_cred_client())
    import pytest as _pytest
    with _pytest.raises(ValueError):
        broker.place_bracket_order("AAPL", "buy", 5, stop_price=0)
    with _pytest.raises(ValueError):
        broker.place_bracket_order("AAPL", "side", 5, stop_price=90)


def test_check_bracket_stop_must_be_on_losing_side():
    # buy with stop above entry → refused
    d = pre_trade_check  # noqa: F841 (silence lints in patch diffs)
    from stock_cycle_tracker.trading.risk import check_bracket as cb
    decision = cb("AAPL", "buy", stop_price=110.0, entry_price=100.0,
                  brief=_brief(), account=_account(), positions=[])
    assert not decision.allowed and any("BELOW" in r for r in decision.refusals)

    decision = cb("AAPL", "buy", stop_price=99.9, entry_price=100.0,
                  brief=_brief(), account=_account(), positions=[])
    assert not decision.allowed and any("too tight" in r for r in decision.refusals)

    decision = cb("AAPL", "buy", stop_price=97.0, entry_price=100.0,
                  brief=_brief(), account=_account(), positions=[])
    assert decision.allowed and decision.qty == 5.0
    assert any("risk $" in n for n in decision.notes)


def test_check_bracket_notional_cap():
    from stock_cycle_tracker.trading.risk import check_bracket as cb
    decision = cb("AAPL", "buy", stop_price=97.0, entry_price=100.0,
                  brief=_brief(), account=_account(cash=1000.0), positions=[],
                  qty=20)  # 20 × 100 = $2000 vs $50 cap (5% of 1000)
    assert not decision.allowed and any("position cap" in r for r in decision.refusals)


def test_suggest_qty_whole_shares():
    from stock_cycle_tracker.trading.risk import suggest_qty
    assert suggest_qty(100.0, 10000.0, 5.0) == 5
    assert suggest_qty(33.33, 100.0, 5.0) == 0


def test_preview_order_and_confirm_bracket_branch(tmp_path):
    service, client = _service(tmp_path)
    config = Config(trading_enabled=True)
    state = _state_with_brief("AAPL", action="invest")
    with patch.object(client, "get_account", return_value=_account()), \
         patch.object(client, "get_positions", return_value=[]):
        preview = service.preview_order(config, state, "AAPL", "buy",
                                        stop_price=97.0, entry_price=100.0)
    assert preview["ok"] and preview["kind"] == "bracket" and preview["qty"] == 5.0
    assert preview["entry_type"] == "limit"

    with patch.object(client, "get_account", return_value=_account()), \
         patch.object(client, "get_positions", return_value=[]), \
         patch.object(client, "submit_order", return_value={"id": "br1", "status": "held_for_review"}) as so:
        result = service.confirm(config, preview["confirmation_id"])
    assert result["ok"] and result["order"]["id"] == "br1"
    body = so.call_args[0][0]
    assert body["order_class"] == "bracket" and body["stop_loss"]["stop_price"] == 97.0


def test_open_lines_maps_bracket_orders(tmp_path):
    service, client = _service(tmp_path)
    config = Config(trading_enabled=True)
    orders = [{
        "id": "p1", "symbol": "AAPL", "side": "buy", "qty": "5",
        "order_class": "bracket", "type": "limit", "limit_price": "100.0", "status": "held",
        "legs": [{"id": "l1", "stop_price": "97.0"}],
    }]
    positions = [{"symbol": "MSFT", "qty": "3", "avg_entry_price": "420.5", "unrealized_pl": "12.3"}]
    with patch.object(client, "get_open_orders", return_value=orders), \
         patch.object(client, "get_positions", return_value=positions):
        payload = service.open_lines(config)
    kinds = [(row["kind"], row.get("price")) for row in payload["lines"]]
    assert ("entry", 100.0) in kinds and ("stop", 97.0) in kinds
    assert payload["positions"][0]["symbol"] == "MSFT"
    assert payload["positions"][0]["price"] == 420.5


def test_disabled_open_lines_empty(tmp_path):
    service, _ = _service(tmp_path, enabled=False)
    assert service.open_lines(Config(trading_enabled=False))["lines"] == []


def test_agent_place_chart_order_tool_gates_and_executes(tmp_path, monkeypatch):
    """The agent tool refuses on risk-gate failures and submits on success
    (it already runs post-user-consent; the gate is the remaining guard)."""
    from stock_cycle_tracker.web import agent_chat

    service, client = _service(tmp_path)
    config = Config(trading_enabled=True)
    state = _state_with_brief("AAPL", action="invest")
    ctx = agent_chat.ChatContext(llm=None, state=type("S", (), {"config": config, "result": state.result})())

    import unittest.mock as mock

    class _ServerStub:
        PAPER = service

    with mock.patch("stock_cycle_tracker.web.server.PAPER", service):
        # 1) wrong side for the decision → refused
        out = agent_chat._tool_place_chart_order(
            {"symbol": "aapl", "side": "sell", "stop_price": 110}, ctx,
        )
        assert not out.get("ok") and "Risk gate" in out.get("error", "")

        # 2) valid bracket → submitted through the token flow
        with patch.object(client, "get_account", return_value=_account()), \
             patch.object(client, "get_positions", return_value=[]), \
             patch.object(client, "submit_order", return_value={"id": "ag1", "status": "held"}):
            out = agent_chat._tool_place_chart_order(
                {"symbol": "aapl", "side": "buy", "stop_price": 97, "limit_price": 100}, ctx,
            )
        assert out.get("ok") and out["order_id"] == "ag1"
        assert "Paper bracket submitted" in out["detail"]

        # 3) 'flip' stop resolves from the decision invalidation
        from stock_cycle_tracker.models import DecisionInvalidation
        state.result.decision_brief.invalidations = [
            DecisionInvalidation(price=91.0, kind="structure_break", flips_toward="divest", rationale="t")
        ]
        with patch.object(client, "get_account", return_value=_account()), \
             patch.object(client, "get_positions", return_value=[]), \
             patch.object(client, "submit_order", return_value={"id": "ag2"}) as so:
            out = agent_chat._tool_place_chart_order(
                {"symbol": "AAPL", "side": "buy", "stop_price": "flip"}, ctx,
            )
        assert out.get("ok")
        assert so.call_args[0][0]["stop_loss"]["stop_price"] == 91.0


def test_bracket_payload_includes_take_profit_leg():
    client = _cred_client()
    broker = AlpacaPaperBrokerClient(client)
    with patch.object(client, "submit_order", return_value={"id": "oco1"}) as so:
        broker.place_bracket_order("AAPL", "buy", 5, stop_price=95.0,
                                    limit_price=100.0, take_profit_price=110.0)
    body = so.call_args[0][0]
    assert body["take_profit"] == {"limit_price": 110.0}
    assert body["stop_loss"] == {"stop_price": 95.0}


def test_check_bracket_take_profit_side_and_r():
    from stock_cycle_tracker.trading.risk import check_bracket as cb
    # TP below entry on a buy → refused
    d = cb("AAPL", "buy", stop_price=95.0, entry_price=100.0, take_profit_price=98.0,
           brief=_brief(), account=_account(), positions=[])
    assert not d.allowed and any("ABOVE" in r for r in d.refusals)

    # Valid TP → R-multiple in notes (10 / 5 = 2R)
    d = cb("AAPL", "buy", stop_price=95.0, entry_price=100.0, take_profit_price=110.0,
           brief=_brief(), account=_account(), positions=[])
    assert d.allowed
    assert any("2.00R" in n for n in d.notes)


def test_preview_order_carries_take_profit_to_broker(tmp_path):
    service, client = _service(tmp_path)
    config = Config(trading_enabled=True)
    state = _state_with_brief("AAPL", action="invest")
    with patch.object(client, "get_account", return_value=_account()), \
         patch.object(client, "get_positions", return_value=[]), \
         patch.object(client, "submit_order", return_value={"id": "oco2"}) as so:
        preview = service.preview_order(config, state, "AAPL", "buy",
                                        stop_price=95.0, entry_price=100.0,
                                        take_profit_price=110.0)
        assert preview["ok"] and preview["take_profit_price"] == 110.0
        result = service.confirm(config, preview["confirmation_id"])
    assert result["ok"]
    body = so.call_args[0][0]
    assert body["take_profit"] == {"limit_price": 110.0}
