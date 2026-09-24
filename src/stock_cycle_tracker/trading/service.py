"""Paper-trading service: preview → explicit confirm → Alpaca paper order.

Flow (nothing touches Alpaca until the final confirmed step):

1. ``preview(symbol, side)`` — reads the current decision brief, runs the
   risk gate against the live paper account, and returns a **single-use
   confirmation token valid 5 minutes**. No order is placed.
2. The UI (or agent pane) shows the preview and asks the user to confirm.
3. ``confirm(confirmation_id)`` — re-runs the risk gate (state may have
   changed) and only then submits the market order to the PAPER account.
   The token is consumed regardless of outcome.

Every attempt lands in the JSONL trade log (``outputs/trade_log.jsonl``),
mirroring alpha-brain-core's logging_obs pattern.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Optional

from stock_cycle_tracker.data.alpaca_client import AlpacaHTTPClient
from stock_cycle_tracker.settings import settings
from stock_cycle_tracker.trading.broker import BrokerClient, DisabledBrokerClient, get_broker
from stock_cycle_tracker.trading.risk import RiskDecision, RiskLimits, pre_trade_check
from stock_cycle_tracker.trading.trade_log import TradeLog

logger = logging.getLogger("stock_cycle_tracker.trading.service")

CONFIRM_TTL_SECONDS = 300  # 5 minutes


class PaperTradingService:
    def __init__(self, broker: BrokerClient | None = None, client: AlpacaHTTPClient | None = None, log: TradeLog | None = None):
        self._client = client or AlpacaHTTPClient()
        self._broker_override = broker
        self.log = log or TradeLog()
        self._pending: dict[str, dict[str, Any]] = {}

    # ── plumbing ─────────────────────────────────────────────────────

    def _broker(self, config) -> BrokerClient:
        if self._broker_override is not None:
            return self._broker_override
        return get_broker(getattr(config, "trading_enabled", False), self._client)

    def _brief_for(self, result) -> Optional[dict[str, Any]]:
        if result is None or result.decision_brief is None:
            return None
        brief = result.decision_brief
        payload = brief.model_dump(mode="json")
        payload["last_price"] = brief.last_price
        return payload

    # ── public API ───────────────────────────────────────────────────

    def status(self, config) -> dict[str, Any]:
        broker = self._broker(config)
        info: dict[str, Any] = {
            "enabled": not isinstance(broker, DisabledBrokerClient),
            "broker": broker.describe(),
            "limits": RiskLimits.from_config(config).__dict__,
        }
        if info["enabled"]:
            try:
                account = self._client.get_account()
                info["account"] = {
                    "cash": float(account.get("cash", 0.0)),
                    "equity": float(account.get("equity", 0.0)),
                    "currency": account.get("currency", "USD"),
                    "paper": account.get("paper", True),
                }
                positions = broker.get_positions()
                info["positions"] = [
                    {"symbol": p.get("symbol"), "qty": float(p.get("qty", 0)),
                     "avg_price": float(p.get("avg_entry_price", 0)),
                     "pnl": float(p.get("unrealized_pl", 0))}
                    for p in positions
                ]
            except Exception as exc:  # noqa: BLE001 - surface in status
                info["error"] = str(exc)
        return info

    def preview(self, config, state, symbol: str, side: str) -> dict[str, Any]:
        symbol = symbol.upper()
        broker = self._broker(config)
        if isinstance(broker, DisabledBrokerClient):
            return {"ok": False, "refusals": [broker.describe()]}

        result = state.result if (state and state.result and state.result.metadata.symbol.upper() == symbol) else None
        brief = self._brief_for(result)
        if brief is None:
            return {"ok": False, "refusals": [f"no current analysis for {symbol} — run it first"]}

        try:
            account = self._client.get_account()
            positions = broker.get_positions()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "refusals": [f"paper account unreachable: {exc}"]}

        decision: RiskDecision = pre_trade_check(
            symbol, side, brief, account, positions, RiskLimits.from_config(config)
        )
        payload = {
            "ok": decision.allowed,
            "symbol": symbol,
            "side": side,
            "qty": decision.qty,
            "refusals": decision.refusals,
            "notes": decision.notes,
            "action": brief.get("action"),
            "conviction": brief.get("conviction"),
            "quality": brief.get("quality"),
            "last_price": brief.get("last_price"),
        }
        if decision.allowed:
            confirmation_id = secrets.token_urlsafe(16)
            self._pending[confirmation_id] = {
                "symbol": symbol,
                "side": side,
                "qty": decision.qty,
                "expires_at": time.time() + CONFIRM_TTL_SECONDS,
            }
            payload["confirmation_id"] = confirmation_id
            payload["expires_in_seconds"] = CONFIRM_TTL_SECONDS
        self.log.append({
            "event": "preview", "symbol": symbol, "side": side,
            "allowed": decision.allowed, "refusals": decision.refusals,
        })
        return payload

    def confirm(self, config, confirmation_id: str) -> dict[str, Any]:
        pending = self._pending.pop(confirmation_id, None)
        if pending is None:
            return {"ok": False, "error": "unknown or already-used confirmation"}
        if time.time() > pending["expires_at"]:
            return {"ok": False, "error": "confirmation expired — preview again"}

        broker = self._broker(config)
        if isinstance(broker, DisabledBrokerClient):
            return {"ok": False, "error": broker.describe()}

        symbol, side, qty = pending["symbol"], pending["side"], pending["qty"]
        try:
            order = broker.place_market_order(symbol, side, qty)
        except Exception as exc:  # noqa: BLE001
            self.log.append({"event": "order_failed", "symbol": symbol, "side": side, "qty": qty, "error": str(exc)})
            return {"ok": False, "error": str(exc)}

        self.log.append({
            "event": "order_submitted", "symbol": symbol, "side": side, "qty": qty,
            "order_id": order.get("id"), "status": order.get("status"),
        })
        return {"ok": True, "order": order}

    def cancel(self, confirmation_id: str) -> dict[str, Any]:
        pending = self._pending.pop(confirmation_id, None)
        if pending:
            self.log.append({"event": "cancelled", "symbol": pending["symbol"], "side": pending["side"]})
        return {"ok": True}
