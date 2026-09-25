/**
 * Trade dock — the TradingView-style order panel that floats on the chart.
 *
 * Opened by clicking the chart in Trade mode (click price becomes the limit
 * entry) or via the ⇅ Trade toggle. Shows entry (limit/market), qty (auto
 * from the position-% cap), a stop price (defaults to the decision engine's
 * invalidation level), a live risk readout, and the preview → confirm flow
 * through the same risk-gated single-use token as the panel below.
 */
import { useState } from 'react';
import type { ChartOrderPreview } from '../api/client';
import { tradingApi } from '../api/client';
import { apiError } from '../lib/apiError';
import HelpDot from './HelpDot';

export interface DockState {
  side: 'buy' | 'sell';
  entryPrice: number | null; // null = market
  stopPrice: number;
  takeProfitPrice: number | null; // null = no TP leg
  qty: number | null;        // null = auto
}

export default function TradeDock({
  symbol, lastPrice, defaultStop, cash, state, onStateChange, onClose, onPlaced,
}: {
  symbol: string;
  lastPrice: number;
  defaultStop: number | null;
  cash: number | null;      // paper cash for the risk readout (null = unknown)
  state: DockState;         // controlled: shared with the chart lines
  onStateChange: (patch: Partial<DockState>) => void;
  onClose: () => void;
  onPlaced: () => void;
}) {
  const { side, entryPrice, stopPrice, qty } = state;
  const takeProfitPrice = state.takeProfitPrice;
  const useLimit = entryPrice !== null;
  const setSide = (v: 'buy' | 'sell') => onStateChange({ side: v });
  const setEntryPrice = (v: number | null) => onStateChange({ entryPrice: v });
  const setStopPrice = (v: number) => onStateChange({ stopPrice: v });
  const setQty = (v: number | null) => onStateChange({ qty: v });
  const setTakeProfitPrice = (v: number | null) => onStateChange({ takeProfitPrice: v });
  const setUseLimit = (limit: boolean) => onStateChange({ entryPrice: limit ? (entryPrice ?? lastPrice) : null });
  const [preview, setPreview] = useState<ChartOrderPreview | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  const effectiveEntry = entryPrice ?? lastPrice;
  const riskDollars = Math.abs(effectiveEntry - stopPrice) * (qty ?? 0);
  const stopPct = Math.abs(effectiveEntry - stopPrice) / effectiveEntry * 100;

  async function doPreview() {
    if (busy) return;
    setBusy(true);
    setMessage('');
    setConfirmId(null);
    try {
      const res = await tradingApi.previewOrder({
        symbol,
        side,
        stop_price: stopPrice,
        entry_price: useLimit ? entryPrice : null,
        qty,
        take_profit_price: takeProfitPrice ?? undefined,
      });
      setPreview(res.data);
      if (res.data.ok && res.data.confirmation_id) {
        setConfirmId(res.data.confirmation_id);
        if (res.data.qty) setQty(res.data.qty);
      }
    } catch (err) {
      setPreview({ ok: false, refusals: [apiError(err, 'Preview failed.')] });
    } finally {
      setBusy(false);
    }
  }

  async function doConfirm() {
    if (!confirmId || busy) return;
    setBusy(true);
    try {
      await tradingApi.confirm(confirmId);
      setMessage(`Bracket order submitted: ${side} ${qty ?? ''} ${symbol} · stop $${stopPrice.toFixed(2)}${takeProfitPrice != null ? ` · target $${takeProfitPrice.toFixed(2)}` : ''}`);
      setConfirmId(null);
      setPreview(null);
      onPlaced();
    } catch (err) {
      setMessage(apiError(err, 'Order failed.'));
      setConfirmId(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="trade-dock">
      <div className="card-head" style={{ marginBottom: '0.4rem' }}>
        <strong style={{ fontSize: '0.85rem' }}>
          {symbol}
          <HelpDot>
            Paper bracket order: an entry (limit at the price you clicked, or market) plus an
            attached stop-loss that Alpaca activates once the entry fills. Nothing is submitted
            until you confirm; the confirmation is single-use and expires in 5 minutes.
          </HelpDot>
        </strong>
        <button className="btn btn-secondary" style={{ fontSize: '0.68rem', padding: '0.2rem 0.5rem' }} onClick={onClose}>✕</button>
      </div>

      {/* side */}
      <div className="trade-dock-row">
        <button
          className={`btn ${side === 'buy' ? 'btn-buy' : 'btn-secondary'}`}
          style={{ flex: 1, fontSize: '0.75rem' }} onClick={() => setSide('buy')}
        >Buy</button>
        <button
          className={`btn ${side === 'sell' ? 'btn-sell' : 'btn-secondary'}`}
          style={{ flex: 1, fontSize: '0.75rem' }} onClick={() => setSide('sell')}
        >Sell</button>
      </div>

      {/* entry type */}
      <div className="trade-dock-row">
        <label className="ind-field" style={{ cursor: 'pointer' }}>
          <input type="radio" checked={useLimit} onChange={() => setUseLimit(true)} /> Limit
        </label>
        <label className="ind-field" style={{ cursor: 'pointer' }}>
          <input type="radio" checked={!useLimit} onChange={() => setUseLimit(false)} /> Market
        </label>
        {useLimit && (
          <input
            className="input" style={{ width: '92px', fontSize: '0.8rem', textAlign: 'right' }}
            type="number" step="0.01" value={entryPrice ?? lastPrice}
            onChange={(e) => setEntryPrice(Number(e.target.value) || lastPrice)}
          />
        )}
      </div>

      {/* stop */}
      <div className="trade-dock-row">
        <span className="ind-field">Stop</span>
        <input
          className="input" style={{ width: '92px', fontSize: '0.8rem', textAlign: 'right', color: 'var(--down)' }}
          type="number" step="0.01" value={stopPrice}
          onChange={(e) => setStopPrice(Number(e.target.value) || stopPrice)}
        />
        {defaultStop != null && (
          <button className="strip-btn" title={`Decision invalidation level: if price crosses this, the engine flips its call`}
            onClick={() => setStopPrice(defaultStop)}>use flip level</button>
        )}
      </div>

      {/* take profit */}
      <div className="trade-dock-row">
        <span className="ind-field" style={{ color: 'var(--up)' }}>Target</span>
        <input
          className="input" style={{ width: '92px', fontSize: '0.8rem', textAlign: 'right', color: 'var(--up)' }}
          type="number" step="0.01" placeholder="none"
          value={takeProfitPrice ?? ''}
          onChange={(e) => setTakeProfitPrice(e.target.value ? Number(e.target.value) : null)}
        />
        {takeProfitPrice != null && entryPrice != null && stopPrice < entryPrice && (
          <span className="muted" style={{ fontSize: '0.64rem' }}>
            {((Math.abs(takeProfitPrice - entryPrice)) / Math.max(entryPrice - stopPrice, 1e-9)).toFixed(1)}R
          </span>
        )}
      </div>

      {/* qty */}
      <div className="trade-dock-row">
        <span className="ind-field">Qty</span>
        <input
          className="input" style={{ width: '92px', fontSize: '0.8rem', textAlign: 'right' }}
          type="number" min="1" placeholder="auto"
          value={qty ?? ''}
          onChange={(e) => setQty(e.target.value ? Number(e.target.value) : null)}
        />
        <span className="muted" style={{ fontSize: '0.66rem' }}>
          {qty == null ? 'auto (position % cap)' : `$${(qty * effectiveEntry).toFixed(0)} notional`}
        </span>
      </div>

      {/* risk readout */}
      <div className="trade-dock-risk">
        <span>risk <strong style={{ color: 'var(--down)' }}>${(Math.abs(effectiveEntry - stopPrice) * (qty ?? 1)).toFixed(0)}</strong></span>
        <span className="muted">stop {stopPct.toFixed(2)}% away</span>
        {cash != null && qty != null && (
          <span className="muted">{(riskDollars / cash * 100).toFixed(2)}% of cash</span>
        )}
      </div>

      {message && <div style={{ fontSize: '0.72rem', color: 'var(--up)', margin: '0.3rem 0' }}>{message}</div>}

      {preview && !confirmId && (
        <div className="error-banner" style={{ margin: '0.35rem 0' }}>
          <strong style={{ fontSize: '0.72rem' }}>Risk gate refused:</strong>
          <ul style={{ margin: '0.2rem 0 0 0.9rem' }}>
            {(preview.refusals || []).map((r, i) => <li key={i} style={{ fontSize: '0.7rem' }}>{r}</li>)}
          </ul>
        </div>
      )}

      {confirmId && (
        <div className="confirm-box" style={{ margin: '0.35rem 0' }}>
          <div style={{ fontSize: '0.75rem' }}>
            <strong>Confirm bracket</strong> — {side} {qty} {symbol}
            {' '}@ {useLimit ? `$${effectiveEntry.toFixed(2)} limit` : 'market'} · stop ${stopPrice.toFixed(2)}
            {takeProfitPrice != null ? ` · target $${takeProfitPrice.toFixed(2)}` : ''}
            {preview?.notes?.length ? <div className="muted" style={{ fontSize: '0.66rem' }}>{preview.notes.join(' · ')}</div> : null}
          </div>
          <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.4rem' }}>
            <button className="btn btn-primary" style={{ fontSize: '0.72rem' }} disabled={busy} onClick={doConfirm}>Confirm</button>
            <button className="btn btn-secondary" style={{ fontSize: '0.72rem' }} onClick={() => { setConfirmId(null); setPreview(null); }}>Cancel</button>
          </div>
        </div>
      )}

      <button className="btn btn-primary btn-block" style={{ fontSize: '0.78rem', marginTop: '0.45rem' }} disabled={busy} onClick={doPreview}>
        {busy ? 'Checking…' : 'Preview bracket order'}
      </button>
    </div>
  );
}
