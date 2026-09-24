import { useEffect, useState } from 'react';
import { watchlistApi } from '../api/client';
import { apiError } from '../lib/apiError';
import HelpDot from './HelpDot';

export default function WatchlistEditor({ onChanged }: { onChanged?: () => void }) {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [input, setInput] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const res = await watchlistApi.get();
      setSymbols(res.data.symbols);
    } catch { /* sidebar-level failure is non-fatal */ }
  }

  useEffect(() => { load(); }, []);

  async function add() {
    const raw = input.trim();
    if (!raw || busy) return;
    setBusy(true);
    setError('');
    try {
      const res = await watchlistApi.put([...symbols, raw]);
      setSymbols(res.data.symbols);
      setInput('');
      onChanged?.();
    } catch (err) {
      setError(apiError(err, 'Could not update the watchlist.'));
    } finally {
      setBusy(false);
    }
  }

  async function remove(symbol: string) {
    if (busy) return;
    setBusy(true);
    try {
      const res = await watchlistApi.put(symbols.filter(s => s !== symbol));
      setSymbols(res.data.symbols);
      onChanged?.();
    } catch (err) {
      setError(apiError(err, 'Could not update the watchlist.'));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    try {
      const res = await watchlistApi.reset();
      setSymbols(res.data.symbols);
      onChanged?.();
    } catch (err) {
      setError(apiError(err, 'Reset failed.'));
    }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 style={{ margin: 0 }}>
          Watchlist
          <HelpDot>The tickers the scanner ranks. Add anything US-listed; liquid names work best on the free IEX data feed.</HelpDot>
        </h3>
        <button className="btn btn-secondary" style={{ fontSize: '0.7rem' }} onClick={reset}>Reset</button>
      </div>
      {error && <div className="error-banner">{error}</div>}
      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
        {symbols.map(s => (
          <span key={s} className="badge" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
            {s}
            <button
              onClick={() => remove(s)}
              style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', padding: 0, fontSize: '0.7rem' }}
              title={`Remove ${s}`}
            >✕</button>
          </span>
        ))}
      </div>
      <div style={{ display: 'flex', gap: '0.4rem' }}>
        <input
          className="input"
          style={{ flex: 1, fontSize: '0.8rem' }}
          placeholder="Add ticker (e.g. AMD)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') add(); }}
        />
        <button className="btn btn-secondary" style={{ fontSize: '0.75rem' }} onClick={add} disabled={busy || !input.trim()}>Add</button>
      </div>
    </div>
  );
}
