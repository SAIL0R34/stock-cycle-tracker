import type { MoverRow, ScanRow } from '../api/client';
import HelpDot from './HelpDot';

/**
 * Ticker-tape of watchlist movers: strongest gainers first, then the
 * laggards, scrolling continuously under the header. Fed by the latest
 * scan (each row's forming-leg % — the move currently in progress).
 * Click a ticker to open its dashboard. Pauses on hover.
 */
export default function MoversTape({
  rows, movers: fallbackMovers, onSelect,
}: {
  rows: ScanRow[] | null;
  movers?: MoverRow[] | null;
  onSelect: (symbol: string) => void;
}) {
  // Prefer scan rows (forming swing %); fall back to keyless 1-day movers
  // so the carousel is never empty while waiting for data/keys.
  const scanRows = (rows || []).filter(r => !r.error && r.forming_pct != null);
  let movers: Array<{ symbol: string; pct: number }>;
  if (scanRows.length) {
    const byMove = [...scanRows].sort((a, b) => (b.forming_pct ?? 0) - (a.forming_pct ?? 0));
    const gainers = byMove.slice(0, 4);
    const losers = byMove.slice(-4).reverse().filter(r => (r.forming_pct ?? 0) < 0);
    movers = [...gainers, ...losers].map(r => ({ symbol: r.symbol, pct: r.forming_pct ?? 0 }));
  } else {
    const byMove = [...(fallbackMovers || [])].sort((a, b) => b.change_pct - a.change_pct);
    const gainers = byMove.slice(0, 5);
    const losers = byMove.slice(-5).reverse().filter(r => r.change_pct < 0);
    movers = [...gainers, ...losers].map(r => ({ symbol: r.symbol, pct: r.change_pct }));
  }

  if (!movers.length) {
    return (
      <div className="tape-strip">
        <span className="muted" style={{ fontSize: '0.7rem' }}>
          Movers tape — fetching the watchlist's latest moves…
        </span>
      </div>
    );
  }

  const useScan = scanRows.length > 0;
  const items = movers.map(row => {
    const pct = row.pct;
    const up = pct >= 0;
    return (
      <button
        key={row.symbol}
        className={`tape-item ${up ? 'up' : 'down'}`}
        onClick={() => onSelect(row.symbol)}
        title={`${row.symbol}: ${useScan ? 'forming swing move' : '1-day change'} ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% — click to open`}
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
