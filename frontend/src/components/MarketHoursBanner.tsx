import { useEffect, useState } from 'react';
import type { MarketHours } from '../api/client';
import { marketApi } from '../api/client';
import HelpDot from './HelpDot';

const PHASE_TONE: Record<string, { color: string; label: string }> = {
  open: { color: 'var(--up)', label: 'Market open' },
  pre: { color: 'var(--warning)', label: 'Pre-market' },
  post: { color: 'var(--warning)', label: 'After hours' },
  closed: { color: 'var(--text-muted)', label: 'Market closed' },
};

export default function MarketHoursBanner({ onData }: { onData?: (m: MarketHours) => void }) {
  const [hours, setHours] = useState<MarketHours | null>(null);

  useEffect(() => {
    const load = () => marketApi.hours()
      .then(res => { setHours(res.data); onData?.(res.data); })
      .catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [onData]);

  if (!hours) return null;
  const tone = PHASE_TONE[hours.phase] || PHASE_TONE.closed;
  const next = hours.next_event_at
    ? new Date(hours.next_event_at).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
    : '—';

  return (
    <div className="market-banner" style={{ borderColor: tone.color }}>
      <span className="phase-dot" style={{ background: tone.color }} />
      <strong style={{ color: tone.color }}>{tone.label}</strong>
      <span className="muted">· next {hours.next_event} {next}</span>
      <HelpDot>
        US equity sessions run 9:30–16:00 Eastern Time, weekdays. When the market is closed
        the data stays frozen at the last session close — the forming leg and prices you see
        are from the last trading day, and decision grading counts candles, not calendar days.
        {hours.source === 'approx' ? ' (Approximation mode: holidays unknown until Alpaca keys are configured.)' : ''}
      </HelpDot>
    </div>
  );
}
