// Subtitle source picker — Phase 2.
//
// Two factory functions share a single module so the pure helpers (visible
// for vitest) live next to the DOM glue that consumes them. Per CLAUDE.md
// the helpers are tested in isolation; mounted DOM behaviour is verified
// manually end-to-end.
//
//   mountCornerBadge(container, getSongId) — splash TV
//   mountSmartPicker(container, getSongId, opts) — phone now-playing-bar
//
// Both factories return { el, update(data), destroy() }. ``update(data)``
// accepts a ``now_playing``-shaped payload (the same one the rest of the
// pilot UI consumes) — never a separate fetch. ``destroy()`` always closes
// any open popover synchronously *before* removing socket listeners or the
// DOM element, so dangling focus/dismiss handlers don't fire on a removed
// node.
//
// Render contract: every dynamic-text surface goes through ``setText`` /
// ``setAttr`` so an ``error_message`` like ``<script>alert(1)</script>`` is
// rendered as literal text. ``innerHTML`` is never used.

// ---------------------------------------------------------------------------
// Pure helpers (named exports — vitest covers these)
// ---------------------------------------------------------------------------

const ALARM_SOURCES_DEFAULT = new Set([
  'lrclib',
  'lrclib-sync',
  'genius-sync',
  'spotify-sync',
  'tekstowo-sync',
  'AI',
  'youtube-vtt',
]);

/**
 * @param {Array<{source:string,status:string,state?:string}>} sources
 * @returns {{ready:number,total:number,label:string,severity:'green'|'amber'|'red'}}
 *
 * "Ready count" excludes ``off`` (UI toggle) and ``user`` (user-authored,
 * not orchestrated). Severity ladder (per Phase 2 D#47, T3=B): green when
 * 4+ sources are ready, amber for 1-3, red for 0.
 */
export function computeReadySummary(sources) {
  let ready = 0;
  let total = 0;
  for (const s of sources || []) {
    if (!s || !ALARM_SOURCES_DEFAULT.has(s.source)) continue;
    total += 1;
    if (s.status === 'ready' || s.state === 'success') ready += 1;
  }
  let severity = 'red';
  if (ready >= 4) severity = 'green';
  else if (ready >= 1) severity = 'amber';
  return { ready, total, label: `${ready}/${total}`, severity };
}

/**
 * Resolve a row in the popover to one of: 'active' | 'enabled' | 'disabled-running'
 * | 'disabled-rate-limited' | 'disabled-error' | 'disabled-na'.
 *
 * Job state wins over capability status — when the orchestrator reports a
 * row is running or rate-limited, the picker reflects that even if the
 * variant file is on disk from a previous run.
 *
 * @param {{source:string,status:string,state?:string|null,error_code?:string|null,
 *   error_message?:string|null,next_retry_at?:string|null}} source
 * @param {string|null} activeSource
 */
export function deriveOptionState(source, activeSource) {
  if (!source) return { state: 'disabled-na', tooltip: '' };
  const state = source.state;
  const status = source.status;
  if (state === 'running' || state === 'queued') {
    return { state: 'disabled-running', tooltip: 'Pobieranie…' };
  }
  if (state === 'rate_limited') {
    return {
      state: 'disabled-rate-limited',
      tooltip: source.error_message || 'Ograniczenie szybkości',
      nextRetryAt: source.next_retry_at,
    };
  }
  if (state === 'failed') {
    return {
      state: 'disabled-error',
      tooltip: source.error_message || source.error_code || 'Błąd',
    };
  }
  if (source.source === activeSource) {
    return { state: 'active', tooltip: '' };
  }
  if (status === 'ready' || status === 'download') {
    return { state: 'enabled', tooltip: '' };
  }
  if (status === 'downloading') {
    return { state: 'disabled-running', tooltip: 'Pobieranie…' };
  }
  return { state: 'disabled-na', tooltip: 'Niedostępne' };
}

/**
 * Render the rate-limit countdown for a row tooltip ("Dostępne za 47m").
 * Returns '' when ``nextRetryAt`` is in the past, missing, or unparseable.
 */
export function computeCountdown(nowMs, nextRetryAt) {
  if (!nextRetryAt) return '';
  const t = Date.parse(nextRetryAt);
  if (Number.isNaN(t)) return '';
  const ms = t - nowMs;
  if (ms <= 0) return '';
  const totalSec = Math.round(ms / 1000);
  if (totalSec >= 3600) {
    const m = Math.round(totalSec / 60);
    return `Dostępne za ${m}m`;
  }
  if (totalSec >= 60) {
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return s > 0 ? `Dostępne za ${m}m ${s}s` : `Dostępne za ${m}m`;
  }
  return `Dostępne za ${totalSec}s`;
}

/**
 * Stable canonical order so the picker reads consistently across surfaces.
 * Unknown sources (forward-compat: a future source the picker hasn't been
 * taught) sink to the bottom rather than disappear.
 */
export function sortSourcesCanonically(sources, canonicalOrder) {
  const order = Array.from(canonicalOrder || []);
  const idx = new Map(order.map((s, i) => [s, i]));
  return Array.from(sources || []).slice().sort((a, b) => {
    const ai = idx.has(a.source) ? idx.get(a.source) : Number.POSITIVE_INFINITY;
    const bi = idx.has(b.source) ? idx.get(b.source) : Number.POSITIVE_INFINITY;
    if (ai !== bi) return ai - bi;
    return String(a.source).localeCompare(String(b.source));
  });
}

/**
 * Map ``(active_source, sources)`` to the splash corner badge view-model.
 *
 * The badge has four states (D#23): ``ready`` (active source is success),
 * ``downloading`` (active source running), ``error`` (active source failed
 * or no active source resolves to a real row), ``pending`` (no active
 * source yet — pre-playback / fresh mount).
 *
 * On the error state the source-name slot is set to an em-dash and the
 * status slot carries the alarm copy (D#27) — keeps slot semantics
 * consistent across all four states.
 */
export function deriveCornerBadgeState(activeSource, sources) {
  if (!activeSource) {
    return { glyph: '○', cssClass: 'pending', label: '—', status: 'czekaj…' };
  }
  const row = (sources || []).find((s) => s && s.source === activeSource);
  if (!row) {
    return { glyph: '✕', cssClass: 'error', label: '—', status: 'brak napisów' };
  }
  const label = row.label || activeSource;
  const state = row.state;
  const status = row.status;
  if (state === 'failed' || state === 'rate_limited') {
    return { glyph: '✕', cssClass: 'error', label: '—', status: 'brak napisów' };
  }
  if (state === 'running' || state === 'queued' || status === 'downloading') {
    return { glyph: '⟳', cssClass: 'downloading', label, status: 'POBIERANIE…' };
  }
  if (state === 'success' || status === 'ready') {
    return { glyph: '●', cssClass: 'ready', label, status: 'OK' };
  }
  // Capability fallback: source exists in the list but isn't ``ready`` or
  // active-running. Treat as pending — the orchestrator hasn't acted yet.
  return { glyph: '○', cssClass: 'pending', label, status: 'czekaj…' };
}

/**
 * Stable signature for the re-render guard (D#28). Composes
 * ``${active_source}|${ready_count}|${sources.map(s => s.source+':'+s.state).sort().join(',')}``
 * so a background source finishing flips the summary pill even when the
 * active row is unchanged.
 */
export function computePickerSig(activeSource, sources) {
  const summary = computeReadySummary(sources);
  const parts = (sources || [])
    .map((s) => `${s.source}:${s.state || s.status || 'na'}`)
    .slice()
    .sort();
  return `${activeSource || ''}|${summary.ready}/${summary.total}|${parts.join(',')}`;
}

// ---------------------------------------------------------------------------
// DOM helpers (private)
// ---------------------------------------------------------------------------

function el(tag, props) {
  const node = document.createElement(tag);
  if (props) {
    if (props.className) node.className = props.className;
    if (props.text != null) node.textContent = String(props.text);
    if (props.attrs) {
      for (const [k, v] of Object.entries(props.attrs)) {
        if (v == null) continue;
        node.setAttribute(k, String(v));
      }
    }
  }
  return node;
}

function setText(node, value) {
  // textContent never parses HTML — XSS-safe by construction.
  node.textContent = value == null ? '' : String(value);
}

function setAttr(node, key, value) {
  if (value == null) node.removeAttribute(key);
  else node.setAttribute(key, String(value));
}

// ---------------------------------------------------------------------------
// Corner badge factory (splash TV)
// ---------------------------------------------------------------------------

/**
 * Mount the splash corner badge.
 *
 * @param {HTMLElement} container — element to render the badge into.
 * @param {() => (number|null)} getSongId — current song id; events for
 *   other songs are filtered out.
 * @param {{socket?: any}} [opts] — caller may pass an explicit socket
 *   instance (splash.js owns its own ``socket``; the global ``window.socket``
 *   is the pilot/admin pages).
 */
export function mountCornerBadge(container, getSongId, opts) {
  const socket = (opts && opts.socket) || (typeof window !== 'undefined' ? window.socket : null);
  const root = el('div', {
    className: 'pk-source-badge',
    attrs: { role: 'status', 'aria-live': 'polite' },
  });
  const glyph = el('span', { className: 'pk-glyph', attrs: { 'aria-hidden': 'true' } });
  const labelEl = el('span', { className: 'pk-source-name' });
  const sep = el('span', {
    className: 'pk-sep',
    text: '·',
    attrs: { 'aria-hidden': 'true' },
  });
  const statusEl = el('span', { className: 'pk-status' });
  root.append(glyph, labelEl, sep, statusEl);
  container.appendChild(root);

  let last = null;
  let lastSig = '';

  function render(activeSource, sources) {
    const view = deriveCornerBadgeState(activeSource, sources);
    const sig = `${view.cssClass}|${view.glyph}|${view.label}|${view.status}`;
    if (sig === lastSig) return;
    lastSig = sig;
    root.dataset.state = view.cssClass;
    setText(glyph, view.glyph);
    setText(labelEl, view.label);
    setText(statusEl, view.status);
    last = view;
  }

  function update(data) {
    if (!data) return;
    const sources = Array.isArray(data.subtitle_sources) ? data.subtitle_sources : [];
    const active = data.subtitle_source_override || data.now_playing_lyrics_source || null;
    render(active, sources);
  }

  function onJobUpdate(payload) {
    if (!payload) return;
    const sid = getSongId && getSongId();
    if (sid != null && payload.song_id != null && payload.song_id !== sid) return;
    if (!last) return;
    // The badge only cares about the currently active source.
    const activeSource = last.cssClass === 'pending' ? null : last.activeSource;
    if (activeSource && payload.source && payload.source !== activeSource) return;
    // Synthesize an updated row list with the new state for the active
    // source. The next ``update(data)`` from the now_playing poll will
    // reconcile to the canonical shape.
    if (window.__pkLastNowPlaying) update(window.__pkLastNowPlaying);
  }

  if (socket) socket.on('subtitle_job_update', onJobUpdate);

  return {
    el: root,
    update,
    destroy() {
      if (socket) socket.off('subtitle_job_update', onJobUpdate);
      if (root.parentNode) root.parentNode.removeChild(root);
    },
  };
}

// ---------------------------------------------------------------------------
// Smart picker factory (phone now-playing-bar)
// ---------------------------------------------------------------------------

const ALARM_DURATION_MS = 3000;

/**
 * Mount the smart popover picker into the now-playing-bar slot.
 *
 * @param {HTMLElement} container
 * @param {() => (number|null)} getSongId
 * @param {{
 *   canonicalOrder?: Iterable<string>,
 *   onSelect?: (source: string) => Promise<{ok:boolean,error?:string}>
 *   showNotification?: (msg: string, category?: string) => void
 * }} [opts]
 */
export function mountSmartPicker(container, getSongId, opts) {
  const options = opts || {};
  const canonicalOrder = options.canonicalOrder || [
    'off', 'user', 'lrclib', 'lrclib-sync', 'genius-sync',
    'spotify-sync', 'tekstowo-sync', 'AI', 'youtube-vtt',
  ];
  const onSelect = options.onSelect || (() => Promise.resolve({ ok: true }));
  const notify = options.showNotification ||
    (typeof window !== 'undefined' && window.showNotification) ||
    (() => {});
  const socket = options.socket || (typeof window !== 'undefined' ? window.socket : null);

  const root = el('div', { className: 'pk-smart-picker', attrs: { 'data-state': 'closed' } });
  const trigger = el('button', {
    className: 'pk-trigger',
    attrs: {
      type: 'button',
      'aria-haspopup': 'listbox',
      'aria-expanded': 'false',
      'aria-label': 'Źródło napisów',
    },
  });
  const triggerGlyph = el('span', { className: 'pk-glyph', attrs: { 'aria-hidden': 'true' } });
  const triggerLabel = el('span', { className: 'pk-active-label' });
  const triggerSummary = el('span', { className: 'pk-summary', attrs: { 'aria-hidden': 'true' } });
  const triggerArrow = el('span', { className: 'pk-arrow', text: '▼', attrs: { 'aria-hidden': 'true' } });
  trigger.append(triggerGlyph, triggerLabel, triggerSummary, triggerArrow);
  const popover = el('div', {
    className: 'pk-popover',
    attrs: { role: 'listbox', tabindex: '-1', hidden: '' },
  });
  root.append(trigger, popover);
  container.appendChild(root);

  let lastData = null;
  let lastSig = '';
  let isOpen = false;
  let alarmTimer = null;
  let prevActiveState = null;
  let pendingSelection = null;
  let popoverFlipUp = false;

  function activeSourceFor(data) {
    return (data && (data.subtitle_source_override || data.now_playing_lyrics_source)) || null;
  }

  function renderTrigger(active, sources) {
    const row = sources.find((s) => s.source === active);
    const summary = computeReadySummary(sources);
    setText(triggerSummary, summary.label);
    triggerSummary.dataset.severity = summary.severity;
    if (row) {
      const view = deriveCornerBadgeState(active, sources);
      setText(triggerGlyph, view.glyph);
      triggerGlyph.dataset.state = view.cssClass;
      setText(triggerLabel, view.label === '—' ? row.label : view.label);
    } else {
      setText(triggerGlyph, '○');
      triggerGlyph.dataset.state = 'pending';
      setText(triggerLabel, '—');
    }
  }

  function renderRows(active, sources) {
    // Wipe and rebuild — cheap because <=9 rows. textContent setters mean
    // no XSS exposure even if a server-side label ever leaks an <script>.
    while (popover.firstChild) popover.removeChild(popover.firstChild);
    const sorted = sortSourcesCanonically(sources, canonicalOrder);
    const now = Date.now();
    for (const s of sorted) {
      const opt = deriveOptionState(s, active);
      const row = el('button', {
        className: 'pk-row',
        attrs: {
          type: 'button',
          role: 'option',
          'data-source': s.source,
          'data-state': opt.state,
          'aria-selected': opt.state === 'active' ? 'true' : 'false',
        },
      });
      if (opt.state.startsWith('disabled-')) {
        row.disabled = true;
        row.tabIndex = -1;
      }
      const rowGlyph = el('span', {
        className: 'pk-row-glyph',
        attrs: { 'aria-hidden': 'true' },
        text: glyphForOption(opt.state),
      });
      const rowLabel = el('span', { className: 'pk-row-label', text: s.label || s.source });
      const rowStatus = el('span', { className: 'pk-row-status' });
      const suffix = statusSuffix(opt, now);
      setText(rowStatus, suffix);
      if (opt.tooltip) {
        setAttr(row, 'title', opt.tooltip);
      }
      row.append(rowGlyph, rowLabel, rowStatus);
      popover.appendChild(row);
    }
  }

  function glyphForOption(state) {
    switch (state) {
      case 'active': return '●';
      case 'enabled': return '○';
      case 'disabled-running': return '⟳';
      case 'disabled-rate-limited': return '⏳';
      case 'disabled-error': return '✕';
      case 'disabled-na': return '·';
      default: return ' ';
    }
  }

  function statusSuffix(opt, nowMs) {
    switch (opt.state) {
      case 'active': return 'WYBRANE';
      case 'enabled': return '';
      case 'disabled-running': return 'POBIERANIE';
      case 'disabled-rate-limited': {
        const c = computeCountdown(nowMs, opt.nextRetryAt);
        return c || 'CHWILĘ';
      }
      case 'disabled-error': return 'BŁĄD';
      case 'disabled-na': return 'N/D';
      default: return '';
    }
  }

  function placePopover() {
    // D#29 + D#30: defer measurement until visible. If the slot itself is
    // hidden via [hidden], offsetParent is null — bail and try again on
    // the next ``update(data)``.
    if (root.offsetParent === null) {
      popoverFlipUp = false;
      return;
    }
    const triggerRect = trigger.getBoundingClientRect();
    const viewportH = window.innerHeight || document.documentElement.clientHeight;
    const spaceBelow = viewportH - triggerRect.bottom - 16;
    const spaceAbove = triggerRect.top - 16;
    const desired = 320;
    let flipUp = false;
    let maxH = desired;
    if (spaceBelow >= desired) {
      flipUp = false;
      maxH = desired;
    } else if (spaceAbove >= desired) {
      flipUp = true;
      maxH = desired;
    } else if (spaceAbove > spaceBelow) {
      flipUp = true;
      maxH = Math.max(160, spaceAbove);
    } else {
      flipUp = false;
      maxH = Math.max(160, spaceBelow);
    }
    popoverFlipUp = flipUp;
    root.dataset.flip = flipUp ? 'up' : 'down';
    popover.style.maxHeight = `${maxH}px`;
  }

  function open() {
    if (isOpen) return;
    isOpen = true;
    popover.hidden = false;
    setAttr(trigger, 'aria-expanded', 'true');
    root.dataset.state = 'open';
    placePopover();
    // Focus first enabled row for keyboard users.
    const firstEnabled = popover.querySelector('.pk-row:not([disabled])');
    if (firstEnabled) firstEnabled.focus();
    document.addEventListener('mousedown', onOutsideClick, true);
    document.addEventListener('keydown', onKeyDown, true);
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    popover.hidden = true;
    setAttr(trigger, 'aria-expanded', 'false');
    root.dataset.state = 'closed';
    document.removeEventListener('mousedown', onOutsideClick, true);
    document.removeEventListener('keydown', onKeyDown, true);
  }

  function onOutsideClick(e) {
    if (!root.contains(e.target)) close();
  }

  function onKeyDown(e) {
    if (!isOpen) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
      trigger.focus();
      return;
    }
    if (e.key === 'Tab') {
      close();
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const rows = Array.from(popover.querySelectorAll('.pk-row:not([disabled])'));
      if (!rows.length) return;
      const i = rows.indexOf(document.activeElement);
      const next = e.key === 'ArrowDown'
        ? rows[(i + 1 + rows.length) % rows.length]
        : rows[(i - 1 + rows.length) % rows.length];
      next.focus();
      return;
    }
    if ((e.key === 'Enter' || e.key === ' ') && document.activeElement && document.activeElement.classList.contains('pk-row')) {
      e.preventDefault();
      handleRowClick(document.activeElement);
    }
  }

  trigger.addEventListener('click', () => {
    if (isOpen) close();
    else open();
  });

  popover.addEventListener('click', (e) => {
    const row = e.target.closest('.pk-row');
    if (!row || row.disabled) return;
    handleRowClick(row);
  });

  function handleRowClick(row) {
    const source = row.dataset.source;
    if (!source || pendingSelection) return;
    pendingSelection = source;
    // Optimistic spinner glyph on the tapped row + disable the trigger
    // until the POST resolves (D#25). The row resolves either way once
    // the next ``update(data)`` lands from the now_playing poll.
    const glyphEl = row.querySelector('.pk-row-glyph');
    if (glyphEl) setText(glyphEl, '⟳');
    row.dataset.state = 'pending';
    trigger.disabled = true;
    Promise.resolve(onSelect(source))
      .then((res) => {
        if (res && res.ok === false) {
          notify(res.error || 'Nie udało się przełączyć źródła', 'is-error');
          // Re-render from cached data to restore the row.
          if (lastData) renderAll(lastData, /*forceRows*/ true);
        } else {
          close();
        }
      })
      .catch((err) => {
        notify((err && err.message) || 'Błąd przełączania źródła', 'is-error');
        if (lastData) renderAll(lastData, /*forceRows*/ true);
      })
      .finally(() => {
        pendingSelection = null;
        trigger.disabled = false;
      });
  }

  function maybeAlarm(active, sources) {
    if (!active) return;
    const row = sources.find((s) => s.source === active);
    if (!row) return;
    const view = deriveCornerBadgeState(active, sources);
    const isAlarming = view.cssClass === 'error' || view.cssClass === 'downloading';
    const wasAlarming = prevActiveState === 'error' || prevActiveState === 'downloading';
    if (isAlarming && !wasAlarming) {
      container.classList.add('is-alarmed');
      if (alarmTimer) clearTimeout(alarmTimer);
      alarmTimer = setTimeout(() => {
        container.classList.remove('is-alarmed');
        alarmTimer = null;
      }, ALARM_DURATION_MS);
    }
    prevActiveState = view.cssClass;
  }

  function renderAll(data, forceRows) {
    const sources = Array.isArray(data.subtitle_sources) ? data.subtitle_sources : [];
    const active = activeSourceFor(data);
    const sig = computePickerSig(active, sources);
    if (!forceRows && sig === lastSig) return;
    lastSig = sig;
    renderTrigger(active, sources);
    renderRows(active, sources);
    if (isOpen) placePopover();
    maybeAlarm(active, sources);
  }

  function update(data) {
    if (!data) return;
    lastData = data;
    if (typeof window !== 'undefined') window.__pkLastNowPlaying = data;
    // Close popover on song change so we don't render stale rows for the
    // wrong song (D#24, D#39).
    if (data.now_playing_song_id != null) {
      const sid = getSongId && getSongId();
      if (sid != null && data.now_playing_song_id !== sid && isOpen) {
        close();
      }
    }
    renderAll(data, /*forceRows*/ false);
  }

  function onJobUpdate(payload) {
    if (!payload) return;
    const sid = getSongId && getSongId();
    if (sid != null && payload.song_id != null && payload.song_id !== sid) return;
    // Force a re-render off the cached now_playing payload so the picker
    // reflects job state transitions immediately. The subsequent poll
    // will reconcile if anything else changed.
    if (lastData) {
      // Update the in-memory copy so the next render picks up the new state.
      const sources = Array.isArray(lastData.subtitle_sources) ? lastData.subtitle_sources : [];
      const idx = sources.findIndex((s) => s.source === payload.source);
      if (idx >= 0) {
        sources[idx] = {
          ...sources[idx],
          state: payload.state,
          status: payload.status || sources[idx].status,
          tier: payload.tier ?? sources[idx].tier,
          error_code: payload.error_code,
          error_message: payload.error_message,
        };
      }
      renderAll(lastData, /*forceRows*/ true);
    }
  }

  if (socket) socket.on('subtitle_job_update', onJobUpdate);

  return {
    el: root,
    update,
    destroy() {
      // D#39: close popover synchronously *before* removing listeners or
      // the DOM node so dangling outside-click / keydown handlers don't
      // fire on a removed tree.
      close();
      if (alarmTimer) {
        clearTimeout(alarmTimer);
        alarmTimer = null;
      }
      container.classList.remove('is-alarmed');
      if (socket) socket.off('subtitle_job_update', onJobUpdate);
      if (root.parentNode) root.parentNode.removeChild(root);
    },
  };
}

// ---------------------------------------------------------------------------
// Global namespace bridge (classic-script consumers)
// ---------------------------------------------------------------------------

if (typeof window !== 'undefined') {
  window.PK = window.PK || {};
  window.PK.SubtitleSourcePicker = {
    mountCornerBadge,
    mountSmartPicker,
    computeReadySummary,
    deriveOptionState,
    computeCountdown,
    sortSourcesCanonically,
    deriveCornerBadgeState,
    computePickerSig,
  };
}
