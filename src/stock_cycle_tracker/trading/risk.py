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


def suggest_qty(price: float, cash: float, max_position_pct: float) -> int:
    """Whole shares for the position-% budget (floor, at least 0)."""
    if price <= 0 or cash <= 0:
        return 0
    return int(cash * max_position_pct / 100.0 / price)


def check_bracket(
    symbol: str,
    side: str,
    stop_price: float,
    entry_price: Optional[float],
    brief: dict[str, Any],
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    limits: RiskLimits | None = None,
    qty: Optional[float] = None,
    take_profit_price: Optional[float] = None,
) -> RiskDecision:
    """Gate for chart-placed bracket orders (entry + attached stop).

    ``entry_price`` None means market entry; notional then sizes off the
    brief's last_price. Adds stop-specific sanity on top of the standard
    decision vetoes: the stop must sit on the losing side of the entry,
    far enough away to not be noise.
    """
    limits = limits or RiskLimits()
    symbol = symbol.upper()
    refusals: list[str] = []
    notes: list[str] = []

    refusals.extend(_decision_vetoes(brief, limits))
    action = (brief or {}).get("action", "")
    if side == "buy" and action not in _INVEST_ACTIONS:
        refusals.append(f"a buy must be backed by an invest action (got '{action}')")
    if side == "sell" and action not in _DIVEST_ACTIONS:
        refusals.append(f"a sell must be backed by a divest action (got '{action}')")

    last_price = float(brief.get("last_price") or 0.0) if brief else 0.0
    effective_entry = entry_price if entry_price else last_price
    if effective_entry <= 0:
        refusals.append("no entry price (clicked price or last price) available")

    cash = float(account.get("cash") or 0.0)
    if cash <= 0:
        refusals.append("no cash available in the paper account")

    held = {str(p.get("symbol", "")).upper() for p in positions}
    if limits.block_add_to_loser and side == "buy" and symbol in held:
        refusals.append("position already open in this symbol — adding blocked (no averaging)")

    # Stop sanity.
    if stop_price <= 0:
        refusals.append("stop price must be positive")
    elif effective_entry > 0:
        if side == "buy" and stop_price >= effective_entry:
            refusals.append(f"stop ${stop_price:.2f} must be BELOW the entry for a buy")
        if side == "sell" and stop_price <= effective_entry:
            refusals.append(f"stop ${stop_price:.2f} must be ABOVE the entry for a sell")
        distance_pct = abs(stop_price - effective_entry) / effective_entry * 100
        if 0 < distance_pct < 0.1:
            refusals.append(f"stop is only {distance_pct:.3f}% away — too tight to survive noise")
        if distance_pct > 0:
            notes.append(f"stop {distance_pct:.2f}% from entry")

    # Sizing / notional.
    if qty is None or qty <= 0:
        qty = suggest_qty(effective_entry, cash, limits.max_position_pct)
        if qty < 1:
            refusals.append(
                f"budget ${cash * limits.max_position_pct / 100:.2f} is under one share at ${effective_entry:.2f}"
            )
            return RiskDecision(allowed=False, qty=0, refusals=refusals, notes=notes)
        notes.append(f"sized {qty} × ${effective_entry:.2f} ({limits.max_position_pct}% of cash)")
    if effective_entry > 0 and qty * effective_entry > cash * limits.max_position_pct / 100.0 * 1.01:
        refusals.append(
            f"notional ${qty * effective_entry:.2f} exceeds the {limits.max_position_pct}% position cap"
        )

    risk_dollars = abs(stop_price - effective_entry) * qty if (stop_price > 0 and effective_entry > 0) else 0.0
    if risk_dollars > 0:
        pct_of_cash = risk_dollars / cash * 100 if cash else 0.0
        notes.append(f"risk ${risk_dollars:.2f} ({pct_of_cash:.2f}% of cash) if stopped")

    # Take-profit sanity + R-multiple (reward ÷ risk).
    if take_profit_price and effective_entry > 0:
        if side == "buy" and take_profit_price <= effective_entry:
            refusals.append(f"take-profit ${take_profit_price:.2f} must be ABOVE the entry for a buy")
        if side == "sell" and take_profit_price >= effective_entry:
            refusals.append(f"take-profit ${take_profit_price:.2f} must be BELOW the entry for a sell")
        stop_distance = abs(effective_entry - stop_price)
        if stop_distance > 0:
            notes.append(f"take-profit {abs(take_profit_price - effective_entry) / stop_distance:.2f}R (reward ÷ risk)")

    if refusals:
        return RiskDecision(allowed=False, qty=0, refusals=refusals, notes=notes)
    return RiskDecision(allowed=True, qty=float(qty), refusals=refusals, notes=notes)
