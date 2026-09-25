"""Live quote stream: SSE delivery of watchlist price updates.

While the market is open, a background poller (Alpaca snapshots when
credentialed, otherwise the keyless Yahoo daily-close fallback) pushes
per-symbol quotes to the browser over Server-Sent Events — the same
transport the agent activity feed already uses. The poll cadence follows
the request budget: 10s snapshots (Alpaca) or 60s (Yahoo fallback), and
the stream goes quiet when the market is closed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from stock_cycle_tracker.data.alpaca_client import AlpacaHTTPClient
from stock_cycle_tracker.data.market_hours import MarketHoursService
from stock_cycle_tracker.watchlist.scanner import fetch_yahoo_movers
from stock_cycle_tracker.watchlist.store import WatchlistStore

logger = logging.getLogger("stock_cycle_tracker.web.live")

SNAPSHOT_INTERVAL = 10.0   # Alpaca snapshots (credentialed, batched)
YAHOO_INTERVAL = 60.0      # keyless fallback (cached server-side 5 min)


class LiveQuotePoller:
    """One shared poller; SSE handlers subscribe to its asyncio queue."""

    def __init__(self):
        self.client = AlpacaHTTPClient()
        self.hours = MarketHoursService()
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._last: dict[str, dict[str, Any]] = {}

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
        if not self._subscribers and self._task:
            self._task.cancel()
            self._task = None

    async def _poll_loop(self) -> None:
        while True:
            try:
                phase = self.hours.phase()["phase"]
                if phase == "open":
                    quotes = await self._collect()
                    for quote in quotes:
                        self._broadcast(quote)
                    await asyncio.sleep(SNAPSHOT_INTERVAL if self.client.has_credentials else YAHOO_INTERVAL)
                else:
                    # Market closed: nothing streams, check again in a minute.
                    await asyncio.sleep(60.0)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 - a bad poll never kills the stream
                logger.debug("live poll hiccup: %s", exc)
                await asyncio.sleep(15.0)

    async def _collect(self) -> list[dict[str, Any]]:
        symbols = WatchlistStore().load()[:12]
        if not symbols:
            return []
        if self.client.has_credentials:
            try:
                snapshots = await asyncio.to_thread(self.client.get_snapshots, symbols)
                out = []
                for symbol, snap in snapshots.items():
                    trade = (snap or {}).get("latestTrade") or {}
                    price = trade.get("p")
                    if price:
                        out.append({
                            "symbol": symbol,
                            "price": float(price),
                            "at": trade.get("t"),
                            "source": "alpaca",
                        })
                return out
            except Exception:  # noqa: BLE001 - fall through to Yahoo
                pass
        rows = await asyncio.to_thread(fetch_yahoo_movers, symbols)
        now = datetime.utcnow().isoformat()
        return [
            {"symbol": r["symbol"], "price": r["last"], "change_pct": r["change_pct"],
             "at": now, "source": "yahoo"}
            for r in rows
        ]

    def _broadcast(self, quote: dict[str, Any]) -> None:
        self._last[quote["symbol"]] = quote
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(quote)
            except asyncio.QueueFull:
                pass  # slow consumer drops frames; next tick re-syncs


POLLER = LiveQuotePoller()
