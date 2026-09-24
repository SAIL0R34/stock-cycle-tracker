"""Pre-trade risk gate (limits schema adapted from alpha-brain-core).

Two layers of refusal:
1. **Decision vetoes** — the engine itself must back the trade: quality above
   low, conviction at/above the floor, and an action that means something
   (hold is not a trade).
2. **Account limits** — max position % of cash, max daily loss %, max active
   symbols, no doubling an existing position (paper discipline).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RiskLimits:
    max_position_pct: float = 5.0        # max % of cash committed to one order
    max_daily_loss_pct: float = 3.0      # stop trading after losing this % of day-start equity
    max_active_symbols: int = 8          # cap concurrent paper positions
    min_conviction: float = 0.25         # decision-engine floor
    block_add_to_loser: bool = True      # no averaging into an existing position

    @classmethod
    def from_config(cls, config: Any) -> "RiskLimits":
        return cls(
            max_position_pct=getattr(config, "trading_max_position_pct", 5.0),
            max_daily_loss_pct=getattr(config, "trading_max_daily_loss_pct", 3.0),
            max_active_symbols=getattr(config, "trading_max_active_symbols", 8),
            min_conviction=getattr(config, "trading_min_conviction", 0.25),
        )


@dataclass
class RiskDecision:
    allowed: bool
    qty: float = 0.0
    refusals: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_INVEST_ACTIONS = {"invest", "strong_invest"}
_DIVEST_ACTIONS = {"divest", "strong_divest"}


def _decision_vetoes(brief: dict[str, Any] | None, limits: RiskLimits) -> list[str]:
    if not brief:
        return ["no decision brief for this symbol — run the analysis first"]
    refusals: list[str] = []
    if brief.get("quality") == "low":
        refusals.append("decision quality is low — engine vetoes execution")
    conviction = float(brief.get("conviction") or 0.0)
    if conviction < limits.min_conviction:
        refusals.append(f"conviction {conviction:.2f} below floor {limits.min_conviction:.2f}")
    action = brief.get("action", "hold")
    if action not in _INVEST_ACTIONS | _DIVEST_ACTIONS:
        refusals.append(f"action '{action}' is not tradable")
    return refusals


def pre_trade_check(
    symbol: str,
    side: str,  # buy | sell
    brief: dict[str, Any] | None,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    limits: RiskLimits | None = None,
    day_start_equity: float | None = None,
) -> RiskDecision:
    """Full gate. ``account`` needs cash (+equity when available)."""
    limits = limits or RiskLimits()
    symbol = symbol.upper()
    refusals: list[str] = []
    notes: list[str] = []

    refusals.extend(_decision_vetoes(brief, limits))

    # Side must agree with the decision.
    action = (brief or {}).get("action", "")
    if side == "buy" and action not in _INVEST_ACTIONS:
        refusals.append(f"a buy must be backed by an invest action (got '{action}')")
    if side == "sell" and action not in _DIVEST_ACTIONS:
        refusals.append(f"a sell must be backed by a divest action (got '{action}')")

    cash = float(account.get("cash") or 0.0)
    if cash <= 0:
        refusals.append("no cash available in the paper account")

    # Active symbol cap.
    held = {str(p.get("symbol", "")).upper() for p in positions}
    if side == "buy" and symbol not in held and len(held) >= limits.max_active_symbols:
        refusals.append(f"max active symbols ({limits.max_active_symbols}) reached")

    # No averaging in.
    if limits.block_add_to_loser and side == "buy" and symbol in held:
        refusals.append("position already open in this symbol — adding blocked (no averaging)")

    # Daily loss brake.
    equity = float(account.get("equity") or cash)
    if day_start_equity and day_start_equity > 0:
        drawdown_pct = (day_start_equity - equity) / day_start_equity * 100
        if drawdown_pct >= limits.max_daily_loss_pct:
            refusals.append(
                f"daily loss brake: down {drawdown_pct:.2f}% (limit {limits.max_daily_loss_pct}%)"
            )
        elif drawdown_pct > 0:
            notes.append(f"day P&L: −{drawdown_pct:.2f}%")

    if refusals:
        return RiskDecision(allowed=False, refusals=refusals, notes=notes)

    # Size: min(position pct of cash, all cash), whole shares only.
    price = (brief or {}).get("last_price")
    budget = cash * limits.max_position_pct / 100.0
    if price and float(price) > 0:
        qty = int(budget / float(price))
        if qty < 1:
            refusals.append(f"budget ${budget:.2f} is under one share at ${float(price):.2f}")
            return RiskDecision(allowed=False, refusals=refusals, notes=notes)
        notes.append(f"sized {qty} × ${float(price):.2f} = ${qty * float(price):.2f} ({limits.max_position_pct}% of cash)")
        return RiskDecision(allowed=True, qty=float(qty), notes=notes)

    return RiskDecision(allowed=False, refusals=refusals + ["no price available for sizing"], notes=notes)
