# Stock Cycle Tracker

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)]
[![React 19](https://img.shields.io/badge/frontend-React%2019-61dafb.svg)](https://react.dev)

US-equity sibling of the swing-cycle engine: detect noise-filtered swing pivots on any
ticker, measure the legs between them, and condense everything — structure, patterns,
regime, benchmark context — into an **auditable invest/divest decision** that is logged,
graded against what actually happened, and re-weighted by its own track record. Powered by
**Alpaca** market data with an optional **paper-trading** bridge. Session-aware: it knows
when the market is closed and never pretends overnight gaps are continuity.

> Every number on the dashboard traces back to the math that produced it, and every term
> has a plain-English "?" explainer on hover.

## Highlights

- **Watchlist scanner** — rank your tickers by decision score/action/conviction; click
  through to the full per-symbol dashboard. Session-aware caching keeps rescans cheap.
- **Market-hours awareness** — Alpaca calendar + clock (daily-cached), pre/open/post/closed
  phases, staleness labeling, and grading horizons counted in *candles* so weekends
  consume zero horizon.
- **Reversal-threshold ZigZag** — pivots confirmed only after an ATR-adaptive reversal;
  true swing extremes, alternation by construction, no lookahead. The unfinished move is
  shown honestly as a dashed *forming leg*.
- **Auditable decision engine** — seven weighted evidence sources, chop and quality gates,
  invalidation levels ("what would flip this call"), and a no-lookahead walk-forward replay.
- **Learning loop with receipts** — every call is logged, graded one-shot against the
  realized outcome (structure breaks count as flips), and feeds back: evidence weights
  earn/lose trust (capped ×0.5–×1.5), conviction is calibrated by the alignment rate.
- **Alpaca integration** — bars via raw REST with a shared 180 req/min token bucket,
  split-adjusted, regular-hours-only by default (IEX free feed). Paper trading is
  explicit-confirm-only and the paper host is hardcoded — live trading is structurally
  impossible in this codebase.
- **Conversational agent** — reads the screen, re-runs, edits the watchlist, previews and
  places *paper* orders; anything state-changing requires confirmation.
- **Familiar charting** — Bollinger, SMA, EMA, VWAP, Donchian with editable periods,
  remembered per browser.

## Quick start

```bash
git clone https://github.com/SAIL0R34/stock-cycle-tracker.git
cd stock-cycle-tracker

python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install "fastapi>=0.110" "uvicorn>=0.29" pytest pytest-asyncio

./serve.sh        # builds the frontend on first run, serves on :8011
```

Market data needs Alpaca keys (free paper keys work): copy `.env.example` to `.env` and
fill in `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`. Without keys the app still runs
against cached bars, and the Coinbase source remains available for crypto symbols.

## Configuration

| Variable | Purpose |
| --- | --- |
| `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` | Alpaca data + paper account (paper keys are free) |
| `ALPACA_DATA_FEED` | `iex` (free, default) or `sip` (paid) |
| `SCT_LLM_BASE_URL` / `SCT_LLM_MODEL` | Local OpenAI-compatible gateway for the agent and AI briefs |
| `SCT_HOST` / `SCT_PORT` | Web app bind (default `127.0.0.1:8011`) |

## Architecture

```
stock_cycle_tracker/
├── data/          # Alpaca client/fetcher, market hours, Coinbase fallback, caching
├── pivots/        # ZigZag / fractal detection, ATR + confirmation filters
├── analytics/     # Legs, stats, structures, patterns v2, decision engine + memory
├── watchlist/     # Watchlist store + scanner
├── trading/       # Paper trading: broker seam, risk gate, JSONL trade log
├── web/           # FastAPI server, agent chat, LLM client
└── export/        # CSV / JSON writers
frontend/          # React 19 + Vite dashboard
```

See [METHODOLOGY.md](METHODOLOGY.md) for the full methodology. `pytest` runs 133 tests.

## Disclaimer

Research and analytics tool — **not financial advice**. Paper trading is simulation.
The decision engine makes statistical observations about historical price action with no
guarantee of future behavior; markets can and do invalidate every model.

## License

MIT — see [LICENSE](LICENSE).
