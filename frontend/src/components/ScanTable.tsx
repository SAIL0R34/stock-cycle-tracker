import { useState } from 'react';
import type { ScanPayload } from '../api/client';
import { scanApi } from '../api/client';
import { apiError } from '../lib/apiError';
import HelpDot from './HelpDot';

const ACTION_TONE: Record<string, string> = {
  strong_invest: '#1e8e4e',
  invest: 'var(--up)',
  hold: 'var(--text-muted)',
  divest: 'var(--down)',
  strong_divest: '#b0342a',
};

function RowBar({ score }: { score: number }) {
  const width = Math.min(100, Math.abs(score) / 100 * 100);
  const positive = score >= 0;
  return (
    <span className="dec-bar">
      <span
        className="dec-fill"
        style={{ width: `${width}%`, background: positive ? 'var(--up)' : 'var(--down)' }}
      />
    </span>
  );
}

export default function ScanTable({
  scan, onScan, onSelect,
}: {
  scan: ScanPayload | null;
  onScan: (s: ScanPayload) => void;
  onSelect: (symbol: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function run() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const res = await scanApi.run();
      onScan(res.data);
    } catch (err) {
      setError(apiError(err, 'Scan failed.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 style={{ margin: 0 }}>
          Watchlist scan
          <HelpDot>
            Every ticker in your watchlist gets the full decision-engine treatment — action,
            score (−100…+100), conviction, and the level that would flip the call — ranked by
            how strong the signal is. Click a row to open that symbol's full dashboard.
          </HelpDot>
        </h3>
        <button className="btn btn-primary" style={{ fontSize: '0.8rem' }} onClick={run} disabled={busy}>
          {busy ? 'Scanning…' : 'Run scan'}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {scan?.note && <div style={{ fontSize: '0.72rem', color: 'var(--text-faint)', marginBottom: '0.5rem' }}>{scan.note}</div>}

      <div style={{ overflowX: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Last</th>
              <th>Action</th>
              <th style={{ width: '22%' }}>Score
                <HelpDot>The same composite the decision engine computes: −100 (dump it) to +100 (back it up).</HelpDot>
              </th>
              <th>Conviction</th>
              <th>Flip level</th>
              <th>Forming</th>
            </tr>
          </thead>
          <tbody>
            {(scan?.rows || []).map(row => (
              <tr key={row.symbol} style={{ cursor: 'pointer' }} onClick={() => !row.error && onSelect(row.symbol)}>
                <td>
                  <strong>{row.symbol}</strong>
                  {row.stale && <span className="badge" title="Last candle is from a previous session" style={{ marginLeft: '0.4rem' }}>stale</span>}
                </td>
                <td>{row.last_price != null ? `$${row.last_price.toFixed(2)}` : '—'}</td>
                <td style={{ color: ACTION_TONE[row.action] || 'var(--text-muted)', fontWeight: 700 }}>
                  {row.error ? <span className="muted" title={row.error}>error</span> : row.action.replace('_', ' ')}
                </td>
                <td>
                  {row.error
                    ? <span className="muted" style={{ fontSize: '0.7rem' }}>{row.error.slice(0, 60)}</span>
                    : <div className="dec-cell">
                        <span style={{ color: row.composite_score >= 0 ? 'var(--up)' : 'var(--down)', fontWeight: 600, minWidth: '3rem', textAlign: 'right' }}>
                          {row.composite_score >= 0 ? '+' : ''}{row.composite_score.toFixed(0)}
                        </span>
                        <RowBar score={row.composite_score} />
                      </div>}
                </td>
                <td>{!row.error && `${(row.conviction * 100).toFixed(0)}%`}</td>
                <td>
                  {row.top_invalidation_price != null
                    ? `$${row.top_invalidation_price.toFixed(0)} → ${row.top_invalidation_flips}`
                    : '—'}
                </td>
                <td className={row.forming_pct == null ? '' : row.forming_pct >= 0 ? 'dir-up' : 'dir-down'}>
                  {row.forming_pct != null ? `${row.forming_pct >= 0 ? '+' : ''}${row.forming_pct.toFixed(2)}%` : '—'}
                </td>
              </tr>
            ))}
            {!scan && (
              <tr><td colSpan={7} className="muted" style={{ textAlign: 'center', padding: '1rem' }}>
                Run a scan to rank your watchlist.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
