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
    seekDragging: false,
    seekDuration: 0,
    seekBufferedDemucs: null,
    seekBufferedFfmpeg: null,
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
    el.fullArtist = el.full.querySelector('[data-pk-artist]');
    el.fullSinger = el.full.querySelector('[data-pk-singer]');
    el.fullPauseIcon = el.full.querySelector('[data-pk-pause-icon]');
    el.fullTranspose = el.full.querySelector('[data-pk-transpose]');
    el.fullVolume = el.full.querySelector('[data-pk-volume]');
    el.volumeTool = el.full.querySelector('[data-pk-volume-tool]');
    el.stemTools = el.full.querySelectorAll('[data-pk-stem-tool]');
    el.vocalSlider = el.full.querySelector('[data-pk-vocal-volume]');
    el.instSlider = el.full.querySelector('[data-pk-inst-volume]');
    el.vocalVal = el.full.querySelector('[data-pk-vocal-val]');
    el.instVal = el.full.querySelector('[data-pk-inst-val]');
    el.subOffsetTool = el.full.querySelector('[data-pk-subtitle-offset-tool]');
    el.subOffsetSlider = el.full.querySelector('[data-pk-subtitle-offset]');
    el.subOffsetVal = el.full.querySelector('[data-pk-subtitle-offset-val]');
    el.seekSection = el.full.querySelector('[data-pk-seek-section]');
    el.seekSlider = el.full.querySelector('[data-pk-seek]');
    el.seekCurrent = el.full.querySelector('[data-pk-seek-current]');
    el.seekDuration = el.full.querySelector('[data-pk-seek-duration]');
    el.processing = el.full.querySelector('[data-pk-processing]');
    el.processingLabel = el.full.querySelector('[data-pk-processing-label]');

    if (!state.isAdmin) {
      el.full.querySelectorAll('[data-admin]').forEach((n) => (n.hidden = true));
      if (el.miniPlayBtn) el.miniPlayBtn.hidden = true;
    }

    bindEvents();
    fetchNowPlaying();

    if (window.socket) {
      window.socket.off('now_playing', onSocketNowPlaying);
      window.socket.on('now_playing', onSocketNowPlaying);

      window.socket.off('stems_ready', onStemsReady);
      window.socket.on('stems_ready', onStemsReady);

      window.socket.off('stem_volume', onStemVolume);
      window.socket.on('stem_volume', onStemVolume);

      window.socket.off('playback_position', onPlaybackPosition);
      window.socket.on('playback_position', onPlaybackPosition);

      window.socket.off('seek', onSeek);
      window.socket.on('seek', onSeek);

      window.socket.off('demucs_progress', onDemucsProgress);
      window.socket.on('demucs_progress', onDemucsProgress);

      window.socket.off('ffmpeg_progress', onFfmpegProgress);
      window.socket.on('ffmpeg_progress', onFfmpegProgress);
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

  // Returns true when the event's song_basename (if any) matches the
  // currently-playing song. Stream-manager emissions don't carry a
  // basename and are treated as implicit-current (always apply).
  // Extensions differ by source (download prewarm .m4a vs lyrics/player
  // .mp4) — same song, so match on the stem only.
  function stripExt(name) {
    if (!name) return '';
    const dot = name.lastIndexOf('.');
    return dot > 0 ? name.slice(0, dot) : name;
  }
  function isForCurrentSong(data) {
    if (!data || !data.song_basename) return true;
    const current = state.data && state.data.now_playing_basename;
    return !!current && stripExt(current) === stripExt(data.song_basename);
  }

  // Swap single-volume → stem sliders the moment Demucs's first usable
  // segment is on disk, without waiting for the next now_playing push.
  // Prewarm emits ``stems_ready`` with ``song_basename`` before the song
  // plays; those are ignored here so the sliders don't flip for an
  // unrelated queued song.
  function onStemsReady(data) {
    if (!isForCurrentSong(data)) return;
    if (el.volumeTool) el.volumeTool.hidden = true;
    el.stemTools.forEach((t) => (t.hidden = false));
    if (el.vocalSlider) el.vocalSlider.disabled = false;
    if (el.instSlider) el.instSlider.disabled = false;
    setProcessingIndicator(null);
  }

  // Show "Separating vocals… N%" chip while Demucs is in flight; hide
  // once processed catches up to total (stems_ready fires right after).
  function setProcessingIndicator(pct) {
    if (!el.processing) return;
    if (pct === null || pct === undefined) {
      el.processing.hidden = true;
      return;
    }
    const clamped = Math.max(0, Math.min(100, Math.round(pct)));
    if (el.processingLabel) {
      el.processingLabel.textContent = `Separating vocals… ${clamped}%`;
    }
    el.processing.hidden = false;
  }

  // Another pilot moved a stem slider — update the non-active slider and its %.
  function onStemVolume(data) {
    if (!data) return;
    if (typeof data.vocal_volume === 'number'
      && el.vocalSlider && document.activeElement !== el.vocalSlider) {
      el.vocalSlider.value = data.vocal_volume;
      if (el.vocalVal) el.vocalVal.textContent = Math.round(data.vocal_volume * 100) + '%';
    }
    if (typeof data.instrumental_volume === 'number'
      && el.instSlider && document.activeElement !== el.instSlider) {
      el.instSlider.value = data.instrumental_volume;
      if (el.instVal) el.instVal.textContent = Math.round(data.instrumental_volume * 100) + '%';
    }
  }

  function onPlaybackPosition(pos) {
    if (state.seekDragging || !el.seekSlider) return;
    el.seekSlider.value = pos;
    if (el.seekCurrent) el.seekCurrent.textContent = fmtTime(pos);
    if (state.seekDuration > 0 && el.mini) {
      const pct = Math.min(100, Math.max(0, (pos / state.seekDuration) * 100));
      el.mini.style.setProperty('--pk-progress', pct + '%');
    }
  }

  function onSeek(pos) {
    if (state.seekDragging || !el.seekSlider) return;
    el.seekSlider.value = pos;
    if (el.seekCurrent) el.seekCurrent.textContent = fmtTime(pos);
  }

  function onDemucsProgress(data) {
    if (!data || typeof data.processed !== 'number' || typeof data.total !== 'number') return;
    if (data.total <= 0) return;
    // Prewarm ticks for a queued (not-yet-playing) song must not steer
    // the seek-bar of whatever is currently playing. isForCurrentSong
    // treats absent song_basename as implicit-current (stream-manager
    // emissions during live play).
    if (!isForCurrentSong(data)) return;
    state.seekBufferedDemucs = data.processed >= data.total - 0.05 ? null : data.processed;
    updateSeekBufferedVisual();
    // Surface the demucs progress as a text chip next to the seek bar so
    // the user has a cue beyond the buffered shading. Hide once finished;
    // stems_ready will confirm and also hide defensively.
    if (data.processed >= data.total - 0.05) setProcessingIndicator(null);
    else setProcessingIndicator((data.processed / data.total) * 100);
  }

  function onFfmpegProgress(data) {
    if (!data || typeof data.processed !== 'number' || typeof data.total !== 'number') return;
    if (data.total <= 0) return;
    state.seekBufferedFfmpeg = data.processed >= data.total - 0.05 ? null : data.processed;
    updateSeekBufferedVisual();
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
      setProcessingIndicator(null);
      if (el.full.classList.contains('is-open')) close();
      return;
    }

    el.mini.hidden = false;
    document.body.classList.add('pk-has-mini-player');

    const cleanTitle = window.stripQuotes ? window.stripQuotes(data.now_playing) : data.now_playing;
    el.miniTitle.textContent = cleanTitle;
    el.miniMeta.innerHTML = data.now_playing_user
      ? `<i class="icon icon-mic-1"></i><span class="pk-mini-singer">${escapeHtml(data.now_playing_user)}</span>`
      : '';
    setPauseIcon(el.miniPauseIcon, data.is_paused);

    el.fullTitle.textContent = cleanTitle;
    if (el.fullArtist) {
      const artist = (data.now_playing_artist || '').trim();
      el.fullArtist.textContent = artist;
      el.fullArtist.hidden = !artist;
    }
    el.fullSinger.textContent = data.now_playing_user || '';
    // fullTranspose is absent when pk_is_transpose_enabled=False (the whole
    // key/pitch tool is {% if %}'d out of base.html).
    if (el.fullTranspose) {
      el.fullTranspose.textContent = formatSemitones(data.now_playing_transpose || 0);
    }
    setPauseIcon(el.fullPauseIcon, data.is_paused);

    // Volume / stem controls: single slider during Demucs warmup, two sliders once stems are audible.
    const stemsReady = !!(data.vocal_removal && data.vocals_url && data.instrumental_url);
    if (el.volumeTool) el.volumeTool.hidden = stemsReady;
    el.stemTools.forEach((t) => (t.hidden = !stemsReady));

    // Subtitle offset slider — only meaningful while ASS lyrics are rendering.
    if (el.subOffsetTool) {
      el.subOffsetTool.hidden = !data.now_playing_subtitle_url;
    }
    if (el.subOffsetSlider && typeof data.subtitle_offset === 'number'
        && document.activeElement !== el.subOffsetSlider) {
      el.subOffsetSlider.value = data.subtitle_offset;
      if (el.subOffsetVal) {
        el.subOffsetVal.textContent = data.subtitle_offset.toFixed(2) + 's';
      }
    }

    if (!stemsReady && data.volume != null && el.fullVolume && document.activeElement !== el.fullVolume) {
      el.fullVolume.value = data.volume;
    }

    if (stemsReady) {
      if (el.vocalSlider) {
        el.vocalSlider.disabled = false;
        if (document.activeElement !== el.vocalSlider && typeof data.vocal_volume === 'number') {
          el.vocalSlider.value = data.vocal_volume;
          if (el.vocalVal) el.vocalVal.textContent = Math.round(data.vocal_volume * 100) + '%';
        }
      }
      if (el.instSlider) {
        el.instSlider.disabled = false;
        if (document.activeElement !== el.instSlider && typeof data.instrumental_volume === 'number') {
          el.instSlider.value = data.instrumental_volume;
          if (el.instVal) el.instVal.textContent = Math.round(data.instrumental_volume * 100) + '%';
        }
      }
    }

    // Seek slider + timecodes
    const dur = Number(data.now_playing_duration) || 0;
    if (el.seekSection && dur > 0) {
      state.seekDuration = dur;
      el.seekSection.hidden = false;
      el.seekSlider.max = dur;
      if (!state.seekDragging) el.seekSlider.value = Number(data.now_playing_position) || 0;
      el.seekCurrent.textContent = fmtTime(Number(data.now_playing_position) || 0);
      el.seekDuration.textContent = fmtTime(dur);

      // Re-derive buffered bounds from now_playing so a fresh page load
      // reflects in-flight processing without waiting for the next progress tick.
      // When vocal_removal is on and stems aren't ready yet, seed the
      // buffered ceiling at 0 instead of null (US-23 P2). null means
      // "unrestricted" — so leaving it null while Demucs is still running
      // would paint the seek bar fully amber until the first tick arrives,
      // which misleads the user into thinking the song is already buffered.
      const stemsLive = !!(data.vocal_removal && data.vocals_url && data.instrumental_url);
      if (data.vocal_removal
        && typeof data.demucs_processed === 'number' && typeof data.demucs_total === 'number'
        && data.demucs_total > 0 && data.demucs_processed < data.demucs_total) {
        state.seekBufferedDemucs = data.demucs_processed;
        setProcessingIndicator((data.demucs_processed / data.demucs_total) * 100);
      } else if (data.vocal_removal && !stemsLive) {
        state.seekBufferedDemucs = 0;
        setProcessingIndicator(0);
      } else {
        state.seekBufferedDemucs = null;
        setProcessingIndicator(null);
      }
      if (typeof data.ffmpeg_processed === 'number' && typeof data.ffmpeg_total === 'number'
        && data.ffmpeg_total > 0 && data.ffmpeg_processed < data.ffmpeg_total) {
        state.seekBufferedFfmpeg = data.ffmpeg_processed;
      } else {
        state.seekBufferedFfmpeg = null;
      }
      updateSeekBufferedVisual();
    } else if (el.seekSection) {
      state.seekDuration = 0;
      state.seekBufferedDemucs = null;
      state.seekBufferedFfmpeg = null;
      el.seekSection.hidden = true;
    }

    // Progress fill on mini-player underline
    if (dur > 0) {
      const pct = Math.min(100, Math.max(0, (data.now_playing_position / dur) * 100));
      el.mini.style.setProperty('--pk-progress', pct + '%');
    }
  }

  function fmtTime(s) {
    s = Math.max(0, Math.floor(s || 0));
    const m = Math.floor(s / 60);
    const ss = s % 60;
    return m + ':' + (ss < 10 ? '0' + ss : ss);
  }

  // Buffered-seek helpers — demucs and ffmpeg each cap how far the user
  // can scrub; the effective limit is the slower of the two.
  function effectiveSeekBuffered() {
    if (state.seekBufferedDemucs === null && state.seekBufferedFfmpeg === null) return null;
    if (state.seekBufferedDemucs === null) return state.seekBufferedFfmpeg;
    if (state.seekBufferedFfmpeg === null) return state.seekBufferedDemucs;
    return Math.min(state.seekBufferedDemucs, state.seekBufferedFfmpeg);
  }

  function updateSeekBufferedVisual() {
    if (!el.seekSlider) return;
    const buffered = effectiveSeekBuffered();
    let pct;
    if (state.seekDuration <= 0) pct = 0;
    else if (buffered === null) pct = 100;
    else pct = Math.max(0, Math.min(100, (buffered / state.seekDuration) * 100));
    el.seekSlider.style.setProperty('--seek-buffered-pct', pct + '%');
  }

  function clampToBuffered(v) {
    const buffered = effectiveSeekBuffered();
    if (buffered !== null && v > buffered) return buffered;
    return v;
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

    // Stem sliders — live over socket (upstream convention)
    if (el.vocalSlider) {
      el.vocalSlider.addEventListener('input', () => {
        const v = parseFloat(el.vocalSlider.value);
        if (el.vocalVal) el.vocalVal.textContent = Math.round(v * 100) + '%';
        if (window.socket) window.socket.emit('vocal_volume', v);
      });
    }
    if (el.instSlider) {
      el.instSlider.addEventListener('input', () => {
        const v = parseFloat(el.instSlider.value);
        if (el.instVal) el.instVal.textContent = Math.round(v * 100) + '%';
        if (window.socket) window.socket.emit('instrumental_volume', v);
      });
    }

    // Subtitle offset — reuse the generic preferences route so the value
    // persists in config.ini and broadcasts via 'preferences_update' to
    // splash, which mutates octopusInstance.timeOffset live.
    if (el.subOffsetSlider) {
      const pushOffset = debounce(() => {
        const v = parseFloat(el.subOffsetSlider.value) || 0;
        fetch('/change_preferences?pref=subtitle_offset&val=' + v);
      }, 150);
      el.subOffsetSlider.addEventListener('input', () => {
        const v = parseFloat(el.subOffsetSlider.value) || 0;
        if (el.subOffsetVal) el.subOffsetVal.textContent = v.toFixed(2) + 's';
        pushOffset();
      });
    }

    // Seek slider — emit 'seek' on change (release). Clamp drag to the
    // buffered upper bound so users can't scrub into unprocessed territory.
    if (el.seekSlider) {
      const startDrag = () => { state.seekDragging = true; };
      el.seekSlider.addEventListener('input', () => {
        state.seekDragging = true;
        const v = clampToBuffered(parseFloat(el.seekSlider.value));
        if (v !== parseFloat(el.seekSlider.value)) el.seekSlider.value = v;
        if (el.seekCurrent) el.seekCurrent.textContent = fmtTime(v);
      });
      el.seekSlider.addEventListener('pointerdown', startDrag);
      el.seekSlider.addEventListener('mousedown', startDrag);
      el.seekSlider.addEventListener('touchstart', startDrag, { passive: true });
      el.seekSlider.addEventListener('change', () => {
        const v = clampToBuffered(parseFloat(el.seekSlider.value));
        el.seekSlider.value = v;
        if (window.socket) window.socket.emit('seek', v);
        state.seekDragging = false;
      });
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
