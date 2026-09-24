import { useCallback, useEffect, useState } from 'react';
import type { TradingPreview, TradingStatus } from '../api/client';
import { tradingApi } from '../api/client';
import { apiError } from '../lib/apiError';
import HelpDot from './HelpDot';

/**
 * Paper-trading bridge: preview a decision as a paper order, then confirm.
 * Nothing reaches Alpaca until Confirm; the whole flow is simulated money.
 */
export default function PaperTradingPanel({ symbol }: { symbol: string }) {
  const [status, setStatus] = useState<TradingStatus | null>(null);
  const [preview, setPreview] = useState<TradingPreview | null>(null);
  const [pending, setPending] = useState<{ id: string; symbol: string; side: string; qty: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  const refresh = useCallback(() => {
    tradingApi.status().then(res => setStatus(res.data)).catch(() => {});
  }, []);

  useEffect(() => { refresh(); setPreview(null); setPending(null); setMessage(''); }, [symbol, refresh]);

  async function doPreview(side: 'buy' | 'sell') {
    if (busy) return;
    setBusy(true);
    setMessage('');
    setPending(null);
    try {
      const res = await tradingApi.preview(symbol, side);
      setPreview(res.data);
      if (res.data.ok && res.data.confirmation_id) {
        setPending({
          id: res.data.confirmation_id,
          symbol: res.data.symbol || symbol,
          side: res.data.side || side,
          qty: res.data.qty || 0,
        });
      }
    } catch (err) {
      setPreview({ ok: false, refusals: [apiError(err, 'Preview failed.')] });
    } finally {
      setBusy(false);
    }
  }

  async function doConfirm() {
    if (!pending || busy) return;
    setBusy(true);
    try {
      await tradingApi.confirm(pending.id);
      setMessage(`Paper ${pending.side} of ${pending.qty} ${pending.symbol} submitted.`);
      setPending(null);
      setPreview(null);
      refresh();
    } catch (err) {
      setMessage(apiError(err, 'Order failed.'));
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  async function doCancel() {
    if (!pending) return;
    await tradingApi.cancel(pending.id).catch(() => {});
    setPending(null);
    setPreview(null);
  }

  const enabled = status?.enabled;

  return (
    <div className="card">
      <div className="card-head">
        <h3 style={{ margin: 0 }}>
          Paper trading
          <HelpDot>
            Mirror this symbol's decision as a SIMULATED order on your Alpaca paper account.
            Preview runs the full risk gate first; nothing is submitted until you confirm,
            and the confirmation is single-use. Real-money trading is not possible here.
          </HelpDot>
        </h3>
        <span className={`badge ${enabled ? 'badge-up' : ''}`}>{enabled ? 'paper · simulated' : 'off'}</span>
      </div>

      {!enabled && (
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '0.3rem 0 0.5rem' }}>
          {status?.broker || 'Disabled.'} Enable with <code>trading_enabled=true</code> and paper API keys in <code>.env</code>.
        </p>
      )}

      {enabled && status?.account && (
        <div className="tile-grid" style={{ marginBottom: '0.5rem' }}>
          <div className="tile">
            <div className="label">Paper cash</div>
            <div className="value">${status.account.cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
            <div className="sub">equity ${status.account.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
          </div>
          <div className="tile">
            <div className="label">Open positions</div>
            <div className="value">{status.positions?.length ?? 0}</div>
            <div className="sub">{(status.positions || []).slice(0, 4).map(p => p.symbol).join(' ') || 'none'}</div>
          </div>
          <div className="tile">
            <div className="label">Order size cap</div>
            <div className="value">{String(status.limits?.max_position_pct ?? 5)}%</div>
            <div className="sub">of cash per order</div>
          </div>
        </div>
      )}

      {enabled && (
        <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn btn-secondary" style={{ fontSize: '0.75rem' }} disabled={busy} onClick={() => doPreview('buy')}>
            Preview paper buy
          </button>
          <button className="btn btn-secondary" style={{ fontSize: '0.75rem' }} disabled={busy} onClick={() => doPreview('sell')}>
            Preview paper sell
          </button>
        </div>
      )}

      {message && <div style={{ fontSize: '0.75rem', color: 'var(--up)', marginTop: '0.4rem' }}>{message}</div>}

      {preview && !pending && (
        <div className="error-banner" style={{ marginTop: '0.5rem' }}>
          <strong>Refused by the risk gate:</strong>
          <ul style={{ margin: '0.3rem 0 0 1rem' }}>
            {(preview.refusals || []).map((r, i) => <li key={i} style={{ fontSize: '0.75rem' }}>{r}</li>)}
          </ul>
        </div>
      )}

      {pending && (
        <div className="confirm-box" style={{ marginTop: '0.5rem' }}>
          <div style={{ fontSize: '0.8rem' }}>
            <strong>Confirm paper {pending.side}</strong> — {pending.qty} × {pending.symbol}
            {preview?.notes?.length ? <div className="muted" style={{ fontSize: '0.7rem' }}>{preview.notes.join(' · ')}</div> : null}
          </div>
          <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.5rem' }}>
            <button className="btn btn-primary" style={{ fontSize: '0.75rem' }} disabled={busy} onClick={doConfirm}>
              Confirm order
            </button>
            <button className="btn btn-secondary" style={{ fontSize: '0.75rem' }} onClick={doCancel}>Cancel</button>
          </div>
          <div className="muted" style={{ fontSize: '0.65rem', marginTop: '0.3rem' }}>
            Single-use confirmation · expires in 5 minutes · simulated fills only
          </div>
        </div>
      )}
    </div>
  );
}
