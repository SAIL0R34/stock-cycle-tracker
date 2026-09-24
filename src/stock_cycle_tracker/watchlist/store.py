"""User watchlist persistence + universe filtering.

The watchlist drives the scanner and the symbol picker. Symbols are
normalised aggressively (case, '$', '.', '-') because people type tickers
in every shape. ``filter_universe`` borrows the liquidity-gate idea from
alpha-brain-core's universe selector, applied to scanner outputs rather
than to the watchlist itself (the user's explicit picks are always kept).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from stock_cycle_tracker.settings import settings

logger = logging.getLogger("stock_cycle_tracker.watchlist")

DEFAULT_WATCHLIST = ["SPY", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]
MAX_WATCHLIST = 40
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


def normalize_symbol(raw: str) -> str:
    """' $aapl ' -> 'AAPL'; returns '' when the input can't be a ticker."""
    candidate = raw.strip().upper().lstrip("$").replace("/", "")
    return candidate if _SYMBOL_RE.match(candidate) else ""


class WatchlistStore:
    """Persisted list of tickers (outputs/watchlist.json)."""

    def __init__(self, path: Path | None = None):
        self.path = path or settings.resolve_app_path("outputs") / "watchlist.json"

    def load(self) -> list[str]:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text())
                symbols = [normalize_symbol(s) for s in data.get("symbols", [])]
                cleaned = [s for s in symbols if s]
                if cleaned:
                    return cleaned
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Ignoring corrupt watchlist (%s): %s", self.path, exc)
        return list(DEFAULT_WATCHLIST)

    def save(self, symbols: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in symbols:
            symbol = normalize_symbol(raw)
            if symbol and symbol not in cleaned:
                cleaned.append(symbol)
        cleaned = cleaned[:MAX_WATCHLIST]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"symbols": cleaned}, indent=2))
        except OSError as exc:
            logger.warning("Could not persist watchlist: %s", exc)
        return cleaned

    def reset(self) -> list[str]:
        return self.save(DEFAULT_WATCHLIST)


def filter_universe(
    rows: list[dict],
    min_price: float = 1.0,
    min_volume: float = 0.0,
    always_keep: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split scan rows into (kept, filtered) by a basic liquidity gate.

    Rows missing price/volume data are kept (the gate is about excluding
    obviously untradeable names, not punishing data gaps). Symbols in
    ``always_keep`` (the user's explicit watchlist) always survive.
    """
    always_keep = always_keep or set()
    kept: list[dict] = []
    filtered: list[dict] = []
    for row in rows:
        symbol = row.get("symbol", "")
        price = row.get("last_price")
        volume = row.get("volume")
        if symbol in always_keep:
            kept.append(row)
            continue
        if price is not None and price < min_price:
            filtered.append({**row, "filter_reason": f"price < ${min_price}"})
            continue
        if volume is not None and volume < min_volume:
            filtered.append({**row, "filter_reason": "volume below floor"})
            continue
        kept.append(row)
    return kept, filtered
