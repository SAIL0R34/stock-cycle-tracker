import { useEffect, useState } from 'react';
import api from '../api/client';
import { apiError } from '../lib/apiError';
import HelpDot from './HelpDot';

interface SettingsStatus {
  alpaca: { configured: boolean; key_id_hint: string | null; feed: string };
  llm: { base_url: string; model: string };
}

/**
 * Full-screen settings overlay: enter Alpaca (and LLM) credentials in the
 * app instead of editing .env. Secrets are stored in a gitignored local
 * file, applied on the next request without a restart, and never sent back
 * to the browser — only masked hints.
 */
export default function SettingsOverlay({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<SettingsStatus | null>(null);
  const [keyId, setKeyId] = useState('');
  const [secret, setSecret] = useState('');
  const [feed, setFeed] = useState('iex');
  const [llmUrl, setLlmUrl] = useState('');
  const [llmModel, setLlmModel] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [tone, setTone] = useState<'ok' | 'err'>('ok');

  async function load() {
    try {
      const res = await api.get<SettingsStatus>('/settings');
      setStatus(res.data);
      setFeed(res.data.alpaca.feed || 'iex');
      setLlmUrl(res.data.llm.base_url || '');
      setLlmModel(res.data.llm.model || '');
    } catch { /* status chips simply stay hidden */ }
  }

  useEffect(() => { load(); }, []);

  function flash(text: string, kind: 'ok' | 'err' = 'ok') {
    setMessage(text);
    setTone(kind);
  }

  async function save(thenTest: boolean) {
    if (busy) return;
    setBusy(true);
    setMessage('');
    try {
      const res = await api.post<SettingsStatus>('/settings', {
        alpaca_key_id: keyId,
        alpaca_secret_key: secret,
        alpaca_data_feed: feed,
        llm_base_url: llmUrl,
        llm_model: llmModel,
      });
      setStatus(res.data);
      setKeyId('');
      setSecret('');
      flash('Saved. Credentials apply immediately — no restart needed.');
      if (thenTest) await test();
    } catch (err) {
      flash(apiError(err, 'Could not save settings.'), 'err');
    } finally {
      setBusy(false);
    }
  }

  async function clearAlpaca() {
    if (busy) return;
    setBusy(true);
    try {
      const res = await api.post<SettingsStatus>('/settings', { clear_alpaca: true });
      setStatus(res.data);
      flash('Stored Alpaca keys cleared.');
    } catch (err) {
      flash(apiError(err, 'Could not clear keys.'), 'err');
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    if (busy) return;
    setBusy(true);
    try {
      const res = await api.post<{ ok: boolean; detail: string }>('/settings/test');
      flash(res.data.detail, res.data.ok ? 'ok' : 'err');
    } catch (err) {
      flash(apiError(err, 'Test failed.'), 'err');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="settings-modal">
        <div className="card-head">
          <h3 style={{ margin: 0 }}>
            Settings
            <HelpDot>Credentials entered here are stored locally (gitignored) and picked up on the next request. They never leave this machine except as authenticated calls to Alpaca / your LLM gateway.</HelpDot>
          </h3>
          <button className="btn btn-secondary" style={{ fontSize: '0.75rem' }} onClick={onClose}>✕ Close</button>
        </div>

        {message && (
          <div className={tone === 'ok' ? 'error-banner ok-banner' : 'error-banner'} style={{ marginBottom: '0.7rem' }}>
            {message}
          </div>
        )}

        {/* Alpaca */}
        <div className="settings-section">
          <div className="settings-section-title">
            Alpaca
            <span className={`badge ${status?.alpaca.configured ? 'badge-up' : ''}`}>
              {status?.alpaca.configured ? `configured ${status.alpaca.key_id_hint ?? ''}` : 'not configured'}
            </span>
            {status?.alpaca.configured && (
              <button className="strip-btn danger" style={{ marginLeft: 'auto' }} disabled={busy}
                onClick={clearAlpaca} title="Remove stored Alpaca keys">clear</button>
            )}
          </div>
          <p className="muted" style={{ fontSize: '0.72rem', margin: '0.25rem 0 0.6rem' }}>
            Free <strong>paper</strong> keys from alpaca.markets → Generate API Keys (Paper Only).
            Needed for market data and paper trading. Get an account at <code>app.alpaca.markets</code>.
          </p>
          <div className="field">
            <label>API key ID</label>
            <input className="input" type="text" autoComplete="off" placeholder={status?.alpaca.key_id_hint ? `saved · ${status.alpaca.key_id_hint}` : 'PK...'}
              value={keyId} onChange={(e) => setKeyId(e.target.value)} />
          </div>
          <div className="field">
            <label>API secret key</label>
            <input className="input" type="password" autoComplete="new-password" placeholder="leave blank to keep the saved one"
              value={secret} onChange={(e) => setSecret(e.target.value)} />
          </div>
          <div className="field">
            <label>Data feed</label>
            <select className="input" value={feed} onChange={(e) => setFeed(e.target.value)}>
              <option value="iex">iex — free</option>
              <option value="sip">sip — paid plan</option>
            </select>
            <div className="field-help">IEX is the free consolidated feed; SIP needs an Alpaca data subscription.</div>
          </div>
        </div>

        {/* LLM */}
        <div className="settings-section">
          <div className="settings-section-title">
            Local LLM gateway
            <span className={`badge ${status?.llm.base_url ? 'badge-up' : ''}`}>
              {status?.llm.base_url ? 'configured' : 'not configured'}
            </span>
          </div>
          <p className="muted" style={{ fontSize: '0.72rem', margin: '0.25rem 0 0.6rem' }}>
            OpenAI-compatible endpoint powering the agent and AI briefs (vLLM, Ollama, llama.cpp…).
          </p>
          <div className="field">
            <label>Base URL</label>
            <input className="input" type="text" placeholder="http://127.0.0.1:8000/v1"
              value={llmUrl} onChange={(e) => setLlmUrl(e.target.value)} />
          </div>
          <div className="field">
            <label>Model</label>
            <input className="input" type="text" placeholder="auto (discovers the served model)"
              value={llmModel} onChange={(e) => setLlmModel(e.target.value)} />
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.9rem', flexWrap: 'wrap' }}>
          <button className="btn btn-primary" disabled={busy} onClick={() => save(true)}>Save & test</button>
          <button className="btn btn-secondary" disabled={busy} onClick={() => save(false)}>Save only</button>
          <button className="btn btn-secondary" disabled={busy} onClick={test}>Test connection</button>
        </div>
        <p className="muted" style={{ fontSize: '0.66rem', marginTop: '0.7rem' }}>
          Stored in <code>outputs/app_secrets.json</code> (gitignored). Blank fields never overwrite saved values.
          Precedence: explicitly set environment variables win over these for Alpaca; the LLM fields here take
          precedence so the latest edit applies instantly.
        </p>
      </div>
    </div>
  );
}
