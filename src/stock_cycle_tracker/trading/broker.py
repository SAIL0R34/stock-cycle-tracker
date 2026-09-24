"""Broker seam for paper trading (adapted from alpha-brain-core).

``BrokerClient`` is the seam; ``AlpacaPaperBrokerClient`` implements it
against Alpaca's PAPER endpoint; ``DisabledBrokerClient`` is the default
when trading is off or keys are missing. The paper host is hardcoded —
there is deliberately no live-trading path in this codebase.
"""

from __future__ import annotations

import abc
import logging
from typing import Any, Optional

from stock_cycle_tracker.data.alpaca_client import AlpacaHTTPClient

logger = logging.getLogger("stock_cycle_tracker.trading")


class BrokerClient(abc.ABC):
    """Minimal broker seam: market orders, positions, cash."""

    name = "base"

    @abc.abstractmethod
    def place_market_order(
        self, symbol: str, side: str, qty: float, time_in_force: str = "day"
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abc.abstractmethod
    def get_positions(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abc.abstractmethod
    def get_cash(self) -> float:
        raise NotImplementedError

    @abc.abstractmethod
    def describe(self) -> str:
        raise NotImplementedError


class DisabledBrokerClient(BrokerClient):
    """What you get when trading is off or unconfigured — refuses everything."""

    name = "disabled"

    def _refuse(self) -> None:
        raise RuntimeError(
            "Paper trading is disabled. Set trading_enabled=true and provide "
            "ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY (paper keys) in .env."
        )

    def place_market_order(self, symbol, side, qty, time_in_force="day"):
        self._refuse()

    def get_positions(self):
        return []

    def get_cash(self) -> float:
        return 0.0

    def describe(self) -> str:
        return "disabled (trading off or no paper credentials)"


class AlpacaPaperBrokerClient(BrokerClient):
    """Alpaca paper account. Market + day orders only, by policy."""

    name = "alpaca-paper"

    def __init__(self, client: AlpacaHTTPClient | None = None):
        self.client = client or AlpacaHTTPClient()

    @property
    def configured(self) -> bool:
        return self.client.has_credentials

    def place_market_order(self, symbol, side, qty, time_in_force="day") -> dict[str, Any]:
        if side not in {"buy", "sell"}:
            raise ValueError(f"side must be buy or sell, got {side}")
        if qty <= 0:
            raise ValueError("qty must be positive")
        return self.client.submit_order(
            {
                "symbol": symbol.upper(),
                "qty": round(float(qty), 4),
                "side": side,
                "type": "market",
                "time_in_force": time_in_force,
            }
        )

    def get_positions(self) -> list[dict[str, Any]]:
        return self.client.get_positions()

    def get_cash(self) -> float:
        return float(self.client.get_account().get("cash", 0.0))

    def describe(self) -> str:
        return "alpaca paper (simulated fills, no real money)" if self.configured else "alpaca paper (missing credentials)"


def get_broker(enabled: bool, client: AlpacaHTTPClient | None = None) -> BrokerClient:
    """Resolve the active broker: paper when enabled AND credentialed."""
    if not enabled:
        return DisabledBrokerClient()
    paper = AlpacaPaperBrokerClient(client)
    return paper if paper.configured else DisabledBrokerClient()
