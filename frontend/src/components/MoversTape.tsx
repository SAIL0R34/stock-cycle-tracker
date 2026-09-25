import type { ScanRow } from '../api/client';
import HelpDot from './HelpDot';

/**
 * Ticker-tape of watchlist movers: strongest gainers first, then the
 * laggards, scrolling continuously under the header. Fed by the latest
 * scan (each row's forming-leg % — the move currently in progress).
 * Click a ticker to open its dashboard. Pauses on hover.
 */
export default function MoversTape({
  rows, onSelect,
}: {
  rows: ScanRow[] | null;
  onSelect: (symbol: string) => void;
}) {
  const clean = (rows || []).filter(r => !r.error && r.forming_pct != null);
  const byMove = [...clean].sort((a, b) => (b.forming_pct ?? 0) - (a.forming_pct ?? 0));
  const gainers = byMove.slice(0, 4);
  const losers = byMove.slice(-4).reverse().filter(r => (r.forming_pct ?? 0) < 0);
  const movers = [...gainers, ...losers];

  if (!movers.length) {
    return (
      <div className="tape-strip">
        <span className="muted" style={{ fontSize: '0.7rem' }}>
          Movers tape — run a scan to see the watchlist's strongest and weakest moves
        </span>
      </div>
    );
  }

  const items = movers.map(row => {
    const pct = row.forming_pct ?? 0;
    const up = pct >= 0;
    return (
      <button
        key={row.symbol}
        className={`tape-item ${up ? 'up' : 'down'}`}
        onClick={() => onSelect(row.symbol)}
        title={`${row.symbol}: forming move ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% · decision ${row.action.replace('_', ' ')} (${row.composite_score >= 0 ? '+' : ''}${row.composite_score.toFixed(0)}) — click to open`}
      >
        <strong>{row.symbol}</strong>
        <span className={up ? 'dir-up' : 'dir-down'}>
          {up ? '▲' : '▼'} {pct.toFixed(2)}%
        </span>
      </button>
    );
  });

  return (
    <div className="tape-strip">
      <span className="tape-label">
        Movers
        <HelpDot>
          The watchlist's strongest moves first, then the weakest — each % is the swing
          currently forming on that symbol, from the latest scan. This is market-wide
          context, separate from whichever symbol the dashboard is analyzing. Click a
          ticker to open it.
        </HelpDot>
      </span>
      <div className="tape-viewport">
        <div className="tape-track">
          {items}
          {items /* duplicated for a seamless loop */}
        </div>
      </div>
    </div>
  );
}
