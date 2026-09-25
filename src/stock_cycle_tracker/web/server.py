"""FastAPI server: analysis API + agent pane endpoints + React static files.

Run with:  python -m stock_cycle_tracker.web.server   (or via ./serve.sh)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stock_cycle_tracker.analytics.intelligence import build_market_intelligence
from stock_cycle_tracker.models import PivotMethod, Timeframe
from stock_cycle_tracker.settings import settings
from stock_cycle_tracker.web import agent_chat, agent_db
from stock_cycle_tracker.web.llm import LLMClient
from stock_cycle_tracker.web.serializers import (
    MAX_CANDLES_DEFAULT,
    build_insight_context,
    serialize_result,
)
from stock_cycle_tracker.web.state import STATE
from datetime import datetime

from stock_cycle_tracker.trading.service import PaperTradingService
from stock_cycle_tracker.web.secrets_store import secrets_store
from stock_cycle_tracker.watchlist.scanner import ScanService, fetch_yahoo_movers
from stock_cycle_tracker.watchlist.store import WatchlistStore

SCANNER = ScanService()
MAX_TAPE_SYMBOLS = 12
PAPER = PaperTradingService()

logger = logging.getLogger("stock_cycle_tracker.web")

app = FastAPI(title="Stock Cycle Tracker", version="2.0")
llm = LLMClient()


# ---------------------------------------------------------------------------
# Analysis API
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    lookback: Optional[str] = None
    config: Optional[dict[str, Any]] = None  # partial Config overrides


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/market-hours")
async def market_hours():
    """Session phase (pre/open/post/closed), next event, and effective data end."""
    from stock_cycle_tracker.data.market_hours import MarketHoursService

    service = MarketHoursService()
    info = service.phase()
    info["effective_data_end"] = service.effective_data_end().isoformat()
    return info


@app.get("/api/options")
async def options():
    return {
        "timeframes": [t.value for t in Timeframe],
        "pivot_methods": [m.value for m in PivotMethod],
        "sources": settings.data.supported_sources,
        "symbols": WatchlistStore().load(),
    }


# ---------------------------------------------------------------------------
# Watchlist + scanner
# ---------------------------------------------------------------------------


@app.get("/api/watchlist")
async def get_watchlist():
    return {"symbols": WatchlistStore().load()}


class WatchlistUpdate(BaseModel):
    symbols: list[str]


@app.put("/api/watchlist")
async def put_watchlist(req: WatchlistUpdate):
    saved = WatchlistStore().save(req.symbols)
    return {"symbols": saved}


@app.post("/api/watchlist/reset")
async def reset_watchlist():
    return {"symbols": WatchlistStore().reset()}


@app.post("/api/scan")
async def run_scan():
    """Scan the watchlist with the current config (decision engine per symbol)."""
    try:
        result = await asyncio.to_thread(lambda: asyncio.run(SCANNER.scan(STATE.config)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Scan failed: {exc}") from exc
    return result.to_dict()


@app.get("/api/movers")
async def movers():
    """Keyless 1-day movers for the tape (Yahoo daily closes, cached 5 min).
    Used when no scan exists yet so the carousel is never empty."""
    from stock_cycle_tracker.data.market_hours import MarketHoursService

    symbols = WatchlistStore().load()[:MAX_TAPE_SYMBOLS]
    rows = await asyncio.to_thread(fetch_yahoo_movers, symbols)
    return {
        "rows": rows,
        "fetched_at": datetime.utcnow().isoformat(),
        "market_phase": MarketHoursService().phase()["phase"],
    }


@app.get("/api/scan/status")
async def scan_status():
    if SCANNER.last_result is None:
        return {"has_result": False}
    return {"has_result": True, **SCANNER.last_result.to_dict()}


@app.get("/api/config")
async def get_config():
    return STATE.config.model_dump(mode="json")


@app.put("/api/config")
async def put_config(fields: dict[str, Any]):
    try:
        config = STATE.update_config(fields)
    except Exception as exc:  # noqa: BLE001 - pydantic/value errors -> 422
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return config.model_dump(mode="json")


@app.get("/api/state")
async def get_state():
    meta = STATE.result.metadata.model_dump(mode="json") if STATE.result else None
    return {
        "config": STATE.config.model_dump(mode="json"),
        "has_result": STATE.result is not None,
        "metadata": meta,
        "last_run_at": STATE.last_run_at,
        "last_files": STATE.last_files,
    }


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, max_candles: int = Query(MAX_CANDLES_DEFAULT, ge=200, le=50000)):
    if req.config:
        try:
            STATE.update_config(req.config)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    label = f"{req.symbol or STATE.config.symbol} {req.timeframe or STATE.config.timeframe.value}"
    agent_db.emit_event("started", f"Analysis run: {label}", task_id="analysis")
    try:
        result = await asyncio.to_thread(
            STATE.run_analysis_sync, req.symbol, req.timeframe, req.lookback
        )
    except Exception as exc:  # noqa: BLE001
        agent_db.emit_event("blocked", f"Analysis failed: {exc}", task_id="analysis")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc
    agent_db.emit_event(
        "completed",
        f"Analysis done: {result.metadata.total_pivots} pivots, {result.metadata.total_legs} legs",
        task_id="analysis",
    )
    return serialize_result(result, max_candles=max_candles)


@app.get("/api/result")
async def get_result(max_candles: int = Query(MAX_CANDLES_DEFAULT, ge=200, le=50000)):
    if STATE.result is None:
        raise HTTPException(status_code=404, detail="No analysis has been run yet.")
    return serialize_result(STATE.result, max_candles=max_candles)


@app.post("/api/export")
async def export():
    try:
        files = await asyncio.to_thread(STATE.export_current)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"files": files}


@app.get("/api/export/download")
async def download(path: str):
    """Serve one previously exported file (guarded to the outputs dir)."""
    outputs = settings.output_path.resolve()
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(outputs)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Not an exported file.") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(candidate, filename=candidate.name)


# ---------------------------------------------------------------------------
# One-shot AI insight (port of the Streamlit Qwen panel)
# ---------------------------------------------------------------------------

_INSIGHT_SYSTEM = (
    "You are integrated inside a BTC swing-cycle analysis dashboard. "
    "The user can already see the chart, KPIs, pattern panels, and optional overlays. "
    "Use only the supplied dashboard context. Do not invent prices, dates, or signals. "
    "Treat market_intelligence as the primary evidence brief. Separate observed swing structure "
    "from historical pattern evidence, explicitly mention signal conflicts, and calibrate claims "
    "to the supplied quality label. "
    "For structure discoveries, preserve detector status exactly; confidence is evidence strength, "
    "not outcome probability, and invalidation_price is only a detector invalidation level. "
    "Be concise, calm, and useful. This is not financial advice. "
    "Return compact markdown with these four headings: "
    "**Current read**, **What matters**, **Watch next**, **Caveats**. "
    "Use 1-2 short bullets under each heading. Prefer plain language over trading jargon."
)


@app.post("/api/insight")
async def insight():
    if STATE.result is None:
        raise HTTPException(status_code=400, detail="Run an analysis first.")
    context = build_insight_context(STATE.result, STATE.config)
    messages = [
        {"role": "system", "content": _INSIGHT_SYSTEM},
        {
            "role": "user",
            "content": "Here is the current dashboard state as JSON:\n\n"
            + json.dumps(context, separators=(",", ":"), default=str),
        },
    ]
    try:
        text = await asyncio.to_thread(llm.chat, messages, 0.25, 420)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"insight": text}


# ---------------------------------------------------------------------------
# Grounded AI decision brief (deterministic scorecard + LLM narrative)
# ---------------------------------------------------------------------------

_DECISION_SYSTEM = (
    "You are the decision narrator inside a BTC swing-cycle dashboard. "
    "You are handed a deterministic, auditable decision scorecard produced by "
    "the engine: an action band, a composite score, conviction, weighted "
    "evidence contributions, invalidation levels, and a walk-forward replay. "
    "Your job is to explain it — never to change it, and never to invent "
    "numbers, levels, or evidence that is not in the JSON. "
    "Cite contributions by source and respect signal conflicts explicitly. "
    "Treat walk-forward band stats as the honesty check: mention how the "
    "action bands actually performed historically if provided. "
    "A track_record may be provided: the engine's graded decision history. "
    "Acknowledge it explicitly — the engine alignment rate with its sample "
    "counts and live-vs-replay split, per-source learned weight multipliers, "
    "invalidated past decisions, and that conviction is calibrated by this "
    "record. Small samples mean weak evidence; never present a learned "
    "multiplier as a guarantee, and call out when the record is thin. "
    "Be direct about uncertainty and about what would flip the call. "
    "This is not financial advice; do not give personalized advice. "
    "Return compact markdown with these five headings: "
    "**Decision**, **Strongest evidence**, **Track record**, **What would flip it**, "
    "**Caveats**. "
    "Use 1-3 short bullets per heading. Plain language over jargon."
)


@app.post("/api/decision/brief")
async def decision_brief():
    """LLM narrative grounded in the deterministic decision scorecard."""
    if STATE.result is None:
        raise HTTPException(status_code=400, detail="Run an analysis first.")
    if STATE.result.decision_brief is None:
        raise HTTPException(status_code=400, detail="Decision engine is disabled — enable it in the sidebar and re-run.")
    context = {
        "decision": STATE.result.decision_brief.model_dump(mode="json"),
        "metadata": STATE.result.metadata.model_dump(mode="json"),
        "market_intelligence": build_market_intelligence(STATE.result),
    }
    messages = [
        {"role": "system", "content": _DECISION_SYSTEM},
        {
            "role": "user",
            "content": "Decision scorecard and market context as JSON:\n\n"
            + json.dumps(context, separators=(",", ":"), default=str),
        },
    ]
    try:
        text = await asyncio.to_thread(llm.chat, messages, 0.2, 480)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"brief": text}


# ---------------------------------------------------------------------------
# Agent pane: status + SSE events (Live tab)
# ---------------------------------------------------------------------------

@app.get("/api/agent/status")
async def agent_status():
    with agent_db.connect() as conn:
        active = conn.execute(
            "SELECT * FROM agent_events WHERE event_type IN ('started','completed','blocked') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        events = conn.execute("SELECT * FROM agent_events ORDER BY id DESC LIMIT 50").fetchall()
        history = conn.execute(
            "SELECT * FROM agent_events WHERE event_type IN ('completed','blocked') ORDER BY id DESC LIMIT 10"
        ).fetchall()

    is_active = bool(active and active["event_type"] == "started")
    active_task = None
    if is_active and active:
        active_task = {"task_id": active["task_id"], "started_at": active["created_at"]}
    return {
        "active": is_active,
        "active_task": active_task,
        "recent_events": [dict(e) for e in events],
        "history": [dict(h) for h in history],
    }


@app.get("/api/agent/events/stream")
async def agent_events_stream(request: Request):
    """SSE endpoint — streams new agent events in real time."""

    async def event_generator():
        last_id = 0
        with agent_db.connect() as conn:
            row = conn.execute("SELECT MAX(id) AS m FROM agent_events").fetchone()
            last_id = row["m"] or 0
        while True:
            if await request.is_disconnected():
                break
            with agent_db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM agent_events WHERE id > ? ORDER BY id ASC", (last_id,)
                ).fetchall()
            for row in rows:
                last_id = row["id"]
                yield f"data: {json.dumps(dict(row))}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Agent pane: conversational chat
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str = "user"
    content: str


class AgentChatRequest(BaseModel):
    messages: list[ChatMessage]
    # When set, execute this previously-proposed write tool, then continue.
    confirm: Optional[dict] = None


def _persist_chat_message(role: str, content: str) -> None:
    if not content:
        return
    with agent_db.connect() as conn:
        conn.execute(
            "INSERT INTO agent_chat_messages (role, content) VALUES (?, ?)",
            (role, content),
        )


@app.get("/api/agent/chat/history")
async def agent_chat_history(limit: int = Query(200, ge=1, le=1000)):
    with agent_db.connect() as conn:
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM agent_chat_messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    messages = [dict(r) for r in reversed(rows)]
    return {"messages": messages, "count": len(messages)}


@app.delete("/api/agent/chat/history")
async def clear_agent_chat_history():
    with agent_db.connect() as conn:
        conn.execute("DELETE FROM agent_chat_messages")
    return {"status": "cleared"}


@app.post("/api/agent/chat")
async def agent_chat_endpoint(req: AgentChatRequest):
    """One turn of the conversational agent. Returns {"type": "message", ...}
    or {"type": "confirm", "pending": {...}} for a write awaiting approval."""
    if not req.messages:
        raise HTTPException(status_code=400, detail="At least one message is required")

    ctx = agent_chat.ChatContext(llm=llm, state=STATE)
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    # Persist the new user turn (a confirm continuation carries no new user msg).
    if req.confirm is None and messages and messages[-1]["role"] == "user":
        _persist_chat_message("user", messages[-1]["content"])

    # Offload the blocking LLM/tool loop so the event loop isn't starved.
    result = await asyncio.to_thread(agent_chat.run_chat, messages, req.confirm, ctx)

    if result.get("type") == "message":
        _persist_chat_message("assistant", result.get("content", ""))
    return result


# ---------------------------------------------------------------------------
# Settings (in-app credential entry — no .env editing required)
# ---------------------------------------------------------------------------


@app.get("/api/settings")
async def get_settings():
    """Client-safe settings view: configured flags + masked hints only."""
    return secrets_store.status()


class SettingsUpdate(BaseModel):
    alpaca_key_id: str = ""
    alpaca_secret_key: str = ""
    alpaca_data_feed: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    clear_alpaca: bool = False


@app.post("/api/settings")
async def post_settings(req: SettingsUpdate):
    """Store non-empty fields locally (outputs/app_secrets.json, gitignored).
    Credentials resolve on the next request — no restart needed."""
    if req.clear_alpaca:
        secrets_store.clear({"alpaca_api_key_id", "alpaca_api_secret_key", "alpaca_data_feed"})
    secrets_store.update(
        {
            "alpaca_api_key_id": req.alpaca_key_id,
            "alpaca_api_secret_key": req.alpaca_secret_key,
            "alpaca_data_feed": req.alpaca_data_feed,
            "llm_base_url": req.llm_base_url,
            "llm_model": req.llm_model,
        }
    )
    return secrets_store.status()


@app.post("/api/settings/test")
async def test_settings():
    """Verify the stored Alpaca paper credentials with one account call."""
    from stock_cycle_tracker.data.alpaca_client import AlpacaHTTPClient

    client = AlpacaHTTPClient()
    if not client.has_credentials:
        return {"ok": False, "detail": "No Alpaca keys configured yet."}
    try:
        account = await asyncio.to_thread(client.get_account)
        return {
            "ok": True,
            "detail": (
                f"Connected to paper account · cash ${float(account.get('cash', 0)):,.2f}"
            ),
        }
    except Exception as exc:  # noqa: BLE001 - client scrubs secrets already
        return {"ok": False, "detail": str(exc)}


# ---------------------------------------------------------------------------
# Paper trading (preview -> explicit confirm -> Alpaca paper order)
# ---------------------------------------------------------------------------


@app.get("/api/trading/status")
async def trading_status():
    return PAPER.status(STATE.config)


class TradingPreviewRequest(BaseModel):
    symbol: str
    side: str  # buy | sell


@app.post("/api/trading/preview")
async def trading_preview(req: TradingPreviewRequest):
    if req.side not in {"buy", "sell"}:
        raise HTTPException(status_code=422, detail="side must be buy or sell")
    return PAPER.preview(STATE.config, STATE, req.symbol, req.side)


class TradingConfirmRequest(BaseModel):
    confirmation_id: str


@app.post("/api/trading/confirm")
async def trading_confirm(req: TradingConfirmRequest):
    result = await asyncio.to_thread(PAPER.confirm, STATE.config, req.confirmation_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or result)
    return result


@app.post("/api/trading/cancel")
async def trading_cancel(req: TradingConfirmRequest):
    return PAPER.cancel(req.confirmation_id)


class ChartOrderPreviewRequest(BaseModel):
    symbol: str
    side: str
    stop_price: float
    entry_price: float | None = None
    qty: int | None = None


@app.post("/api/trading/preview-order")
async def trading_preview_order(req: ChartOrderPreviewRequest):
    """Chart-placed bracket preview (limit/market entry + attached stop)."""
    if req.side not in {"buy", "sell"}:
        raise HTTPException(status_code=422, detail="side must be buy or sell")
    return await asyncio.to_thread(
        PAPER.preview_order, STATE.config, STATE,
        req.symbol, req.side, req.stop_price, req.entry_price, req.qty,
    )


@app.get("/api/trading/orders")
async def trading_orders():
    """Open paper orders + positions rendered as chart lines."""
    return await asyncio.to_thread(PAPER.open_lines, STATE.config)


@app.get("/api/trading/log")
async def trading_log(limit: int = Query(50, ge=1, le=500)):
    return {"events": PAPER.log.tail(limit)}


# ---------------------------------------------------------------------------
# Serve React frontend static files in production (MUST be last)
# ---------------------------------------------------------------------------

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        """Serve the SPA — index.html for all non-API routes."""
        index = FRONTEND_DIST / "index.html"
        candidate = (FRONTEND_DIST / full_path).resolve()
        try:
            candidate.relative_to(FRONTEND_DIST.resolve())
        except ValueError:
            return FileResponse(index)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def main() -> None:
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    # Bind localhost by default; set SCT_HOST=0.0.0.0 (and SCT_PORT) to
    # expose the dashboard on the LAN.
    uvicorn.run(
        app,
        host=os.environ.get("SCT_HOST", "127.0.0.1"),
        port=int(os.environ.get("SCT_PORT", "8011")),
    )


if __name__ == "__main__":
    main()

