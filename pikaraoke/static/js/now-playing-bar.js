/**
 * PiKaraoke now-playing bar — global mini-player + fullscreen expanded player.
 *
 * Rendered once in base.html, populated live from /now_playing and socket
 * "now_playing" events. Survives page navigation (both full reloads and
 * spa-navigation.js content swaps) because the shell lives outside .box.
 *
 * Exposes window.PK.NowPlaying with .init(), .open(), .close().
 */
(function () {
  'use strict';

  window.PK = window.PK || {};
  if (window.PK.NowPlaying) return; // idempotent

  const el = {};
  const state = {
    isAdmin: false,
    data: null,
    transposePending: 0,
    transposeTimer: null,
  };

  function init(opts = {}) {
    state.isAdmin = !!opts.isAdmin;

    el.mini = document.getElementById('pk-mini-player');
    el.full = document.getElementById('pk-player-full');
    if (!el.mini || !el.full) return;

    el.miniTitle = el.mini.querySelector('[data-pk-mini-title]');
    el.miniMeta = el.mini.querySelector('[data-pk-mini-meta]');
    el.miniPauseIcon = el.mini.querySelector('[data-pk-mini-pause-icon]');
    el.miniPlayBtn = el.mini.querySelector('[data-pk-mini-play]');

    el.fullTitle = el.full.querySelector('[data-pk-title]');
    el.fullSinger = el.full.querySelector('[data-pk-singer]');
    el.fullPauseIcon = el.full.querySelector('[data-pk-pause-icon]');
    el.fullTranspose = el.full.querySelector('[data-pk-transpose]');
    el.fullVolume = el.full.querySelector('[data-pk-volume]');

    if (!state.isAdmin) {
      el.full.querySelectorAll('[data-admin]').forEach((n) => (n.hidden = true));
      if (el.miniPlayBtn) el.miniPlayBtn.hidden = true;
    }

    bindEvents();
    fetchNowPlaying();

    if (window.socket) {
      window.socket.off('now_playing', onSocketNowPlaying);
      window.socket.on('now_playing', onSocketNowPlaying);
    }

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') fetchNowPlaying();
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && el.full.classList.contains('is-open')) close();
    });
  }

  function onSocketNowPlaying(data) {
    render(data);
  }

  function fetchNowPlaying() {
    fetch('/now_playing')
      .then((r) => r.text())
      .then((t) => {
        if (!t) return;
        try {
          render(JSON.parse(t));
        } catch (_) {
          /* ignore */
        }
      })
      .catch(() => {});
  }

  function render(data) {
    state.data = data;
    state.transposePending = 0;
    clearTimeout(state.transposeTimer);

    if (!data || !data.now_playing) {
      el.mini.hidden = true;
      document.body.classList.remove('pk-has-mini-player');
      if (el.full.classList.contains('is-open')) close();
      return;
    }

    el.mini.hidden = false;
    document.body.classList.add('pk-has-mini-player');

    el.miniTitle.textContent = data.now_playing;
    el.miniMeta.innerHTML = data.now_playing_user
      ? `<i class="icon icon-mic-1"></i><span class="pk-mini-singer">${escapeHtml(data.now_playing_user)}</span>`
      : '';
    setPauseIcon(el.miniPauseIcon, data.is_paused);

    el.fullTitle.textContent = data.now_playing;
    el.fullSinger.textContent = data.now_playing_user || '';
    el.fullTranspose.textContent = formatSemitones(data.now_playing_transpose || 0);
    setPauseIcon(el.fullPauseIcon, data.is_paused);

    // Volume (one-way bind; don't clobber a slider the user is actively dragging)
    if (data.volume != null && el.fullVolume && document.activeElement !== el.fullVolume) {
      el.fullVolume.value = data.volume;
    }

    // Progress fill on mini-player underline
    if (data.now_playing_duration > 0) {
      const pct = Math.min(100, Math.max(0, (data.now_playing_position / data.now_playing_duration) * 100));
      el.mini.style.setProperty('--pk-progress', pct + '%');
    }
  }

  function setPauseIcon(iconEl, isPaused) {
    if (!iconEl) return;
    iconEl.classList.toggle('icon-play', isPaused);
    iconEl.classList.toggle('icon-pause', !isPaused);
  }

  function bindEvents() {
    el.mini.addEventListener('click', (e) => {
      if (e.target.closest('[data-pk-mini-play]')) return;
      open();
    });

    if (el.miniPlayBtn) {
      el.miniPlayBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (state.isAdmin) fetch('/pause');
      });
    }

    el.full.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-pk-action]');
      if (!btn) return;
      handleAction(btn.dataset.pkAction);
    });

    if (el.fullVolume) {
      el.fullVolume.addEventListener('input', debounce(() => {
        fetch('/volume/' + el.fullVolume.value);
      }, 400));
    }
  }

  async function handleAction(action) {
    if (action === 'close') return close();
    if (action === 'queue') {
      close();
      window.location.href = '/queue';
      return;
    }
    if (!state.isAdmin) return;

    const t = window.translations || {};
    switch (action) {
      case 'pause':
        fetch('/pause');
        return;
      case 'skip':
        if (await PK.dialog.confirm({
          title: t.skipTitle || 'Skip this track?',
          message: t.confirmSkip,
          destructive: true,
          confirmText: t.skipBtn || 'Skip',
          cancelText: t.cancelBtn || 'Cancel',
        })) fetch('/skip');
        return;
      case 'restart':
        if (await PK.dialog.confirm({
          title: t.restartTitle || 'Restart this track?',
          message: t.confirmRestartTrack,
          confirmText: t.restartBtn || 'Restart',
          cancelText: t.cancelBtn || 'Cancel',
        })) fetch('/restart');
        return;
      case 'transpose-up':
        adjustTranspose(+1);
        return;
      case 'transpose-down':
        adjustTranspose(-1);
        return;
    }
  }

  function adjustTranspose(delta) {
    const current = (state.data && state.data.now_playing_transpose) || 0;
    const nextPending = clamp(state.transposePending + delta, -12 - current, 12 - current);
    if (nextPending === state.transposePending) return;
    state.transposePending = nextPending;
    el.fullTranspose.textContent = formatSemitones(current + state.transposePending);

    clearTimeout(state.transposeTimer);
    state.transposeTimer = setTimeout(commitTranspose, 1400);
  }

  async function commitTranspose() {
    if (state.transposePending === 0) return;
    const semitones = state.transposePending;
    state.transposePending = 0;
    const t = window.translations || {};
    const label = (t.semitonesLabel || 'SEMITONE_VALUE semitones')
      .replace('SEMITONE_VALUE', semitones > 0 ? '+' + semitones : String(semitones));
    const msg = (t.confirmTranspose || 'Transpose: SEMITONE_LABEL?').replace('SEMITONE_LABEL', label);
    const ok = await PK.dialog.confirm({
      title: t.transposeTitle || 'Change key?',
      message: msg,
      confirmText: t.applyBtn || 'Apply',
      cancelText: t.cancelBtn || 'Cancel',
    });
    if (ok) {
      fetch('/transpose/' + semitones);
    } else if (state.data) {
      el.fullTranspose.textContent = formatSemitones(state.data.now_playing_transpose || 0);
    }
  }

  function open() {
    if (!state.data || !state.data.now_playing) return;
    el.full.hidden = false;
    requestAnimationFrame(() => el.full.classList.add('is-open'));
  }

  function close() {
    el.full.classList.remove('is-open');
    setTimeout(() => (el.full.hidden = true), 400);
  }

  function formatSemitones(n) {
    const t = window.translations || {};
    const label = t.semitonesLabel || 'SEMITONE_VALUE semitones';
    const v = n > 0 ? '+' + n : String(n);
    return label.replace('SEMITONE_VALUE', v);
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function debounce(fn, ms) {
    let id;
    return function () {
      const args = arguments;
      const ctx = this;
      clearTimeout(id);
      id = setTimeout(() => fn.apply(ctx, args), ms);
    };
  }

  function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
  }

  window.PK.NowPlaying = { init, open, close };
})();
