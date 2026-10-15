/**
 * Dark / light theme.
 *
 * The choice lives on <html data-theme="…"> (the CSS variables in
 * styles/theme.css swap on that attribute) and is persisted to localStorage.
 * Plotly can't read CSS directly, so charts call readChartTheme() whenever
 * they (re)draw and useTheme() to know when a redraw is needed.
 */
import { useSyncExternalStore } from 'react';

export type Theme = 'dark' | 'light';

const THEME_KEY = 'sct-theme-v1';

function readStoredTheme(): Theme {
  try {
    const raw = localStorage.getItem(THEME_KEY);
    if (raw === 'light' || raw === 'dark') return raw;
  } catch { /* storage blocked: fall back to dark */ }
  return 'dark';
}

function currentTheme(): Theme {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
}

export function setTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch { /* storage blocked: the choice just won't survive a reload */ }
}

export function toggleTheme(): void {
  setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
}

/** Call once before the first render so a saved choice is applied on reload. */
export function initTheme(): void {
  document.documentElement.setAttribute('data-theme', readStoredTheme());
}

function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  return () => observer.disconnect();
}

/** The active theme; components re-render when it changes. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, currentTheme, () => 'dark');
}

// ── Plotly colours ─────────────────────────────────────────────────────

export interface ChartTheme {
  text: string;       // axis labels, legend, range selector text
  grid: string;
  zeroline: string;
  panel: string;      // range selector background
  muted: string;      // secondary / de-emphasised lines
  up: string;
  down: string;
  accent: string;     // orange: forming leg, last price, BTC line
  accent2: string;    // blue: Bollinger, entry line, second asset
  pivotHigh: string;
  pivotLow: string;
  sma: string;
  ema: string;
  vwap: string;
  neutral: string;
  outline: string;    // marker outline that must stand out from the background
  activeButton: string;
}

/** Current theme's chart colours, read from the CSS variables (dark values as fallback). */
export function readChartTheme(): ChartTheme {
  const style = getComputedStyle(document.documentElement);
  const get = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  return {
    text: get('--text-secondary', '#b6bfcd'),
    grid: get('--border-subtle', '#232b38'),
    zeroline: get('--text-faint', '#5b6474'),
    panel: get('--bg-secondary', '#12161f'),
    muted: get('--text-muted', '#8791a3'),
    up: get('--up', '#2ecc71'),
    down: get('--down', '#e74c3c'),
    accent: get('--accent-primary', '#f7931a'),
    accent2: get('--accent-secondary', '#3b82f6'),
    pivotHigh: get('--chart-pivot-high', '#f39c12'),
    pivotLow: get('--chart-pivot-low', '#3498db'),
    sma: get('--chart-sma', '#f1c40f'),
    ema: get('--chart-ema', '#9b59b6'),
    vwap: get('--chart-vwap', '#1abc9c'),
    neutral: get('--chart-neutral', '#60a5fa'),
    outline: get('--chart-outline', '#ffffff'),
    activeButton: get('--chart-active', '#f7931a'),
  };
}

/** "#rrggbb" + alpha -> "rgba(r, g, b, alpha)"; any other colour string is returned unchanged. */
export function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(color.trim());
  if (!m) return color;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
