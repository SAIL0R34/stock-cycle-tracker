"""Parameter sweep: decision-engine stability across configurations.

Runs the light pipeline for a grid of pivot-parameter combinations and
reports how much the decision (score, action, conviction) moves between
them. High agreement = a robust read on the data; wide dispersion = the
call is an artifact of one particular setting and should be treated as
fragile. The point is anti-curve-fitting: the sweep *measures* parameter
sensitivity instead of letting anyone pick the flattering configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stock_cycle_tracker.analytics.decision import DecisionInputs, score_decision
from stock_cycle_tracker.analytics.legs import LegBuilder
from stock_cycle_tracker.analytics.stats import calculate_summary_stats
from stock_cycle_tracker.analytics.structures import discover_structures
from stock_cycle_tracker.models import Config, OHLCV
from stock_cycle_tracker.pivots.confirmation import confirm_pivots
from stock_cycle_tracker.pivots.filters import ATRFilter
from stock_cycle_tracker.pivots.zigzag import ZigZagDetector

# Default grid: multipliers around the base min_move_pct and the ATR
# multiplier that most influences the reversal threshold.
DEFAULT_GRID: list[dict[str, float]] = [
    {"min_move_pct": 0.75, "atr_multiplier": 1.0},
    {"min_move_pct": 1.0, "atr_multiplier": 1.25},
    {"min_move_pct": 1.5, "atr_multiplier": 1.5},
    {"min_move_pct": 2.25, "atr_multiplier": 2.0},
    {"min_move_pct": 3.0, "atr_multiplier": 2.5},
]


@dataclass
class SweepRow:
    label: str
    min_move_pct: float
    atr_multiplier: float
    legs: int
    pivots: int
    action: str
    composite_score: float
    conviction: float


@dataclass
class SweepReport:
    symbol: str
    timeframe: str
    rows: list[SweepRow] = field(default_factory=list)
    agreement_rate: float = 0.0     # fraction of configs sharing the modal action band
    score_std: float = 0.0
    score_range: float = 0.0
    verdict: str = "unknown"        # robust | mixed | fragile
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "rows": [row.__dict__ for row in self.rows],
            "agreement_rate": round(self.agreement_rate, 3),
            "score_std": round(self.score_std, 2),
            "score_range": round(self.score_range, 2),
            "verdict": self.verdict,
            "note": self.note,
        }


def _run_config(data: list[OHLCV], config: Config) -> SweepRow:
    detector = ZigZagDetector(
        left_bars=config.left_bars,
        right_bars=config.right_bars,
        min_move_pct=config.min_move_pct,
        use_atr_filter=config.use_atr_filter,
        atr_period=config.atr_period,
        atr_multiplier=config.atr_multiplier,
    )
    pivots = detector.detect_pivots(data, config)
    if config.use_atr_filter:
        pivots = ATRFilter(
            atr_period=config.atr_period, atr_multiplier=config.atr_multiplier
        ).filter(pivots, data)
    pivots = confirm_pivots(pivots, data, config)
    legs = LegBuilder.build_legs_from_pivots(pivots, data)
    structures = discover_structures(data, pivots, config.min_move_pct)
    summary = calculate_summary_stats(legs, data)
    forming = LegBuilder.build_forming_leg(pivots, legs, data)

    brief = score_decision(DecisionInputs(
        pivots=pivots, legs=legs, summary=summary, structures=structures,
        forming_leg=forming,
    ))
    return SweepRow(
        label=f"mm {config.min_move_pct:g}% · atr×{config.atr_multiplier:g}",
        min_move_pct=config.min_move_pct,
        atr_multiplier=config.atr_multiplier,
        legs=len(legs),
        pivots=len(pivots),
        action=brief.action,
        composite_score=brief.composite_score,
        conviction=brief.conviction,
    )


def run_parameter_sweep(
    data: list[OHLCV],
    base_config: Config,
    grid: list[dict[str, float]] | None = None,
) -> SweepReport:
    """Sweep the grid and summarise decision stability."""
    grid = grid or DEFAULT_GRID
    report = SweepReport(symbol=base_config.symbol, timeframe=base_config.timeframe.value)

    for point in grid:
        overrides = base_config.model_dump()
        overrides.update({
            "min_move_pct": float(point.get("min_move_pct", base_config.min_move_pct)),
            "atr_multiplier": float(point.get("atr_multiplier", base_config.atr_multiplier)),
        })
        try:
            report.rows.append(_run_config(data, Config(**overrides)))
        except Exception:  # noqa: BLE001 - one bad config never sinks the sweep
            continue

    if not report.rows:
        report.note = "No configurations produced results."
        return report

    scores = [r.composite_score for r in report.rows]
    mean = sum(scores) / len(scores)
    report.score_std = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5
    report.score_range = max(scores) - min(scores)

    # Agreement: fraction of configs landing in the same direction bucket.
    def bucket(action: str) -> str:
        if "invest" in action:
            return "invest"
        if "divest" in action:
            return "divest"
        return "hold"

    buckets = [bucket(r.action) for r in report.rows]
    modal = max(set(buckets), key=buckets.count)
    report.agreement_rate = buckets.count(modal) / len(buckets)

    if report.agreement_rate >= 0.8 and report.score_std <= 15:
        report.verdict = "robust"
        report.note = (
            f"{report.agreement_rate:.0%} of configurations agree on '{modal}' with a "
            f"score spread of ±{report.score_std:.1f} — the call is not an artifact of one setting."
        )
    elif report.agreement_rate >= 0.6:
        report.verdict = "mixed"
        report.note = (
            f"Configurations disagree ({report.agreement_rate:.0%} '{modal}', spread "
            f"±{report.score_std:.1f}) — treat the current call as setting-sensitive."
        )
    else:
        report.verdict = "fragile"
        report.note = (
            f"Decisions flip across configurations (agreement {report.agreement_rate:.0%}, "
            f"spread ±{report.score_std:.1f}) — the signal is parameter-dependent; trust the structure, not the score."
        )
    return report
