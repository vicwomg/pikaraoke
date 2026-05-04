import { describe, it, expect } from 'vitest';
import {
  computeReadySummary,
  deriveOptionState,
  computeCountdown,
  sortSourcesCanonically,
  deriveCornerBadgeState,
  computePickerSig,
} from '../../pikaraoke/static/js/subtitle-source-picker.js';

const CANONICAL = [
  'off', 'user', 'lrclib', 'lrclib-sync', 'genius-sync',
  'spotify-sync', 'tekstowo-sync', 'AI', 'youtube-vtt',
];

// Convenience: build a source row with the shape the picker consumes.
function row(source, status, opts = {}) {
  return {
    source,
    label: source,
    status,
    state: opts.state ?? null,
    tier: opts.tier ?? null,
    error_code: opts.error_code ?? null,
    error_message: opts.error_message ?? null,
    next_retry_at: opts.next_retry_at ?? null,
  };
}

describe('computeReadySummary', () => {
  it('returns 0/0 for an empty list', () => {
    expect(computeReadySummary([])).toEqual({
      ready: 0, total: 0, label: '0/0', severity: 'red',
    });
  });

  it('counts only orchestrated sources (excludes off + user)', () => {
    const sources = [
      row('off', 'ready'),
      row('user', 'ready'),
      row('lrclib', 'ready'),
      row('lrclib-sync', 'ready'),
      row('AI', 'na'),
    ];
    const s = computeReadySummary(sources);
    expect(s.total).toBe(3);
    expect(s.ready).toBe(2);
    expect(s.label).toBe('2/3');
    expect(s.severity).toBe('amber');
  });

  it('green severity at 4+ ready', () => {
    const sources = [
      row('lrclib', 'ready'),
      row('lrclib-sync', 'ready'),
      row('genius-sync', 'ready'),
      row('AI', 'ready'),
      row('youtube-vtt', 'na'),
    ];
    expect(computeReadySummary(sources).severity).toBe('green');
  });

  it('red severity when zero ready', () => {
    const sources = [row('lrclib', 'na'), row('AI', 'na')];
    expect(computeReadySummary(sources).severity).toBe('red');
  });

  it('treats state="success" as ready even when status is stale', () => {
    const sources = [row('lrclib', 'download', { state: 'success' })];
    expect(computeReadySummary(sources).ready).toBe(1);
  });
});

describe('deriveOptionState', () => {
  it('returns active when source matches activeSource', () => {
    const s = row('lrclib', 'ready');
    expect(deriveOptionState(s, 'lrclib').state).toBe('active');
  });

  it('reports running for queued and running job states', () => {
    expect(deriveOptionState(row('AI', 'na', { state: 'queued' }), null).state)
      .toBe('disabled-running');
    expect(deriveOptionState(row('AI', 'na', { state: 'running' }), null).state)
      .toBe('disabled-running');
  });

  it('reports rate-limited with countdown reference', () => {
    const out = deriveOptionState(
      row('genius-sync', 'na', {
        state: 'rate_limited',
        next_retry_at: '2026-05-04T11:00:00Z',
        error_message: 'limit',
      }),
      null,
    );
    expect(out.state).toBe('disabled-rate-limited');
    expect(out.nextRetryAt).toBe('2026-05-04T11:00:00Z');
  });

  it('reports failed with error tooltip', () => {
    const out = deriveOptionState(
      row('AI', 'na', { state: 'failed', error_message: 'boom' }),
      null,
    );
    expect(out.state).toBe('disabled-error');
    expect(out.tooltip).toBe('boom');
  });

  it('falls back to capability status when no job state', () => {
    expect(deriveOptionState(row('lrclib-sync', 'ready'), 'lrclib').state).toBe('enabled');
    expect(deriveOptionState(row('lrclib-sync', 'download'), null).state).toBe('enabled');
    expect(deriveOptionState(row('lrclib-sync', 'downloading'), null).state)
      .toBe('disabled-running');
    expect(deriveOptionState(row('AI', 'na'), null).state).toBe('disabled-na');
  });

  it('returns disabled-na for missing source', () => {
    expect(deriveOptionState(null, null).state).toBe('disabled-na');
  });

  it('job state wins over active match (active+failed → error)', () => {
    // Defensive: if the active source is the one that just failed, render
    // the failure rather than the "active checkmark" so the operator knows
    // their pin no longer works.
    const s = row('AI', 'na', { state: 'failed', error_message: 'boom' });
    expect(deriveOptionState(s, 'AI').state).toBe('disabled-error');
  });
});

describe('computeCountdown', () => {
  const now = Date.parse('2026-05-04T10:00:00Z');

  it('returns empty string for null/missing input', () => {
    expect(computeCountdown(now, null)).toBe('');
    expect(computeCountdown(now, '')).toBe('');
    expect(computeCountdown(now, undefined)).toBe('');
  });

  it('returns empty string when retry time is in the past', () => {
    expect(computeCountdown(now, '2026-05-04T09:59:00Z')).toBe('');
  });

  it('returns empty string for unparseable input', () => {
    expect(computeCountdown(now, 'not-a-date')).toBe('');
  });

  it('formats sub-minute countdown in seconds', () => {
    expect(computeCountdown(now, '2026-05-04T10:00:30Z')).toBe('Dostępne za 30s');
  });

  it('formats m+s when < 1h', () => {
    expect(computeCountdown(now, '2026-05-04T10:01:30Z')).toBe('Dostępne za 1m 30s');
  });

  it('formats minutes alone when no seconds remainder', () => {
    expect(computeCountdown(now, '2026-05-04T10:05:00Z')).toBe('Dostępne za 5m');
  });

  it('rounds down to minutes once >= 1h', () => {
    expect(computeCountdown(now, '2026-05-04T10:47:00Z')).toBe('Dostępne za 47m');
    expect(computeCountdown(now, '2026-05-04T11:30:00Z')).toBe('Dostępne za 90m');
  });
});

describe('sortSourcesCanonically', () => {
  it('orders by canonical position', () => {
    const out = sortSourcesCanonically(
      [row('AI', 'na'), row('lrclib', 'ready'), row('off', 'ready')],
      CANONICAL,
    );
    expect(out.map((s) => s.source)).toEqual(['off', 'lrclib', 'AI']);
  });

  it('puts unknown sources at the end (forward-compat)', () => {
    const out = sortSourcesCanonically(
      [row('lrclib', 'ready'), row('future', 'na'), row('AI', 'na')],
      CANONICAL,
    );
    expect(out.map((s) => s.source)).toEqual(['lrclib', 'AI', 'future']);
  });

  it('does not mutate the input array', () => {
    const input = [row('AI', 'na'), row('lrclib', 'ready')];
    const original = input.slice();
    sortSourcesCanonically(input, CANONICAL);
    expect(input).toEqual(original);
  });
});

describe('deriveCornerBadgeState', () => {
  it('pending state when no active source', () => {
    expect(deriveCornerBadgeState(null, [])).toEqual({
      glyph: '○', cssClass: 'pending', label: '—', status: 'czekaj…',
    });
  });

  it('ready state when active source is success', () => {
    const sources = [row('lrclib-sync', 'ready', { state: 'success' })];
    sources[0].label = 'LRCLib + sync';
    expect(deriveCornerBadgeState('lrclib-sync', sources)).toEqual({
      glyph: '●', cssClass: 'ready', label: 'LRCLib + sync', status: 'OK',
    });
  });

  it('downloading state when active source is running', () => {
    const sources = [
      { ...row('AI', 'downloading', { state: 'running' }), label: 'AI' },
    ];
    expect(deriveCornerBadgeState('AI', sources)).toEqual({
      glyph: '⟳', cssClass: 'downloading', label: 'AI', status: 'POBIERANIE…',
    });
  });

  it('error state when active source is failed (label collapses to em-dash)', () => {
    const sources = [{ ...row('AI', 'na', { state: 'failed' }), label: 'AI' }];
    expect(deriveCornerBadgeState('AI', sources)).toEqual({
      glyph: '✕', cssClass: 'error', label: '—', status: 'brak napisów',
    });
  });

  it('error state when active source is rate_limited', () => {
    const sources = [
      { ...row('genius-sync', 'na', { state: 'rate_limited' }), label: 'Genius + sync' },
    ];
    expect(deriveCornerBadgeState('genius-sync', sources).cssClass).toBe('error');
  });

  it('error state when activeSource is set but not in sources list', () => {
    expect(deriveCornerBadgeState('phantom', []).cssClass).toBe('error');
  });

  it('pending state when active source has no resolved state and is not ready', () => {
    const sources = [{ ...row('lrclib', 'download'), label: 'LRCLib' }];
    expect(deriveCornerBadgeState('lrclib', sources).cssClass).toBe('pending');
  });
});

describe('computePickerSig', () => {
  it('changes when ready_count flips', () => {
    const a = [row('lrclib', 'na'), row('AI', 'ready', { state: 'success' })];
    const b = [row('lrclib', 'ready', { state: 'success' }), row('AI', 'ready', { state: 'success' })];
    expect(computePickerSig('lrclib', a)).not.toBe(computePickerSig('lrclib', b));
  });

  it('changes when active source flips', () => {
    const sources = [row('lrclib', 'ready'), row('AI', 'na')];
    expect(computePickerSig('lrclib', sources)).not.toBe(computePickerSig('AI', sources));
  });

  it('is stable for the same input regardless of source order', () => {
    const a = [row('lrclib', 'ready'), row('AI', 'na')];
    const b = [row('AI', 'na'), row('lrclib', 'ready')];
    expect(computePickerSig('lrclib', a)).toBe(computePickerSig('lrclib', b));
  });

  it('changes when a single per-source state transitions', () => {
    const before = [row('AI', 'na', { state: 'queued' })];
    const after = [row('AI', 'downloading', { state: 'running' })];
    expect(computePickerSig(null, before)).not.toBe(computePickerSig(null, after));
  });
});

describe('XSS regression — text fields are inert', () => {
  // The picker passes label / error_message through textContent, but the
  // helpers themselves don't render. Verify the helpers preserve the
  // payload exactly so the route layer's literal-text contract holds end
  // to end.
  it('preserves <script> in error_message via deriveOptionState tooltip', () => {
    const payload = '<script>alert(1)</script>';
    const out = deriveOptionState(
      row('AI', 'na', { state: 'failed', error_message: payload }),
      null,
    );
    expect(out.tooltip).toBe(payload);
  });

  it('preserves <script> in label via deriveCornerBadgeState', () => {
    const payload = '<img src=x onerror=alert(1)>';
    const sources = [{ ...row('AI', 'ready', { state: 'success' }), label: payload }];
    expect(deriveCornerBadgeState('AI', sources).label).toBe(payload);
  });
});
