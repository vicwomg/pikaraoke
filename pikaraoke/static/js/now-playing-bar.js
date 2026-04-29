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
    // Lyrics panel
    lyrics: [],
    lyricsUrl: null,
    lyricsActiveIdx: -1,
    lyricsActiveEl: null,
    lyricsUserScrolled: false,
    lyricsScrollTimer: null,
    lyricsAutoScrollUntil: 0,
    lyricsPosition: 0,
    lyricsAbort: null,
    lyricsHidden: false,
    reduceMotion: false,
    rafId: null,
    rafLastTickTs: 0,
    rafLastSocketPos: 0,
    lastFillPct: -1,
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
    el.subSrcTool = el.full.querySelector('[data-pk-subtitle-src-tool]');
    el.subSrcSelect = el.full.querySelector('[data-pk-subtitle-src]');
    el.seekSection = el.full.querySelector('[data-pk-seek-section]');
    el.seekSlider = el.full.querySelector('[data-pk-seek]');
    el.seekCurrent = el.full.querySelector('[data-pk-seek-current]');
    el.seekDuration = el.full.querySelector('[data-pk-seek-duration]');
    el.processing = el.full.querySelector('[data-pk-processing]');
    el.processingLabel = el.full.querySelector('[data-pk-processing-label]');

    el.lyricsPanel = el.full.querySelector('#pk-lyrics-panel');
    el.lyricsScroll = el.full.querySelector('[data-pk-lyrics-scroll]');
    el.lyricsToggleBtn = el.full.querySelector('[data-pk-lyrics-toggle]');
    el.lyricsToggleIcon = el.full.querySelector('[data-pk-lyrics-toggle-icon]');

    // Cache the reduced-motion preference once (avoid creating a fresh
    // MediaQueryList per scroll tick). Listen for changes so the value
    // stays current if the user toggles it mid-session.
    if (window.matchMedia) {
      const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
      state.reduceMotion = mq.matches;
      const onMqChange = (e) => { state.reduceMotion = e.matches; };
      if (mq.addEventListener) mq.addEventListener('change', onMqChange);
      else if (mq.addListener) mq.addListener(onMqChange);
    }

    if (el.lyricsScroll) {
      const evtName = 'onscrollend' in el.lyricsScroll ? 'scrollend' : 'scroll';
      el.lyricsScroll.addEventListener(evtName, noteUserScroll, { passive: true });
      // Tap-to-seek: admin-only delegated click + keyboard handler.
      el.lyricsScroll.addEventListener('click', onLyricLineActivate);
      el.lyricsScroll.addEventListener('keydown', onLyricLineKeydown);
    }
    if (el.lyricsToggleBtn) {
      el.lyricsToggleBtn.addEventListener('click', toggleLyricsCollapsed);
    }

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

      window.socket.off('preferences_update', onPreferencesUpdate);
      window.socket.on('preferences_update', onPreferencesUpdate);
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
    state.lyricsPosition = pos;
    anchorWordFillClock();
    tickLyrics(pos);
  }

  function onSeek(pos) {
    if (state.seekDragging || !el.seekSlider) return;
    el.seekSlider.value = pos;
    if (el.seekCurrent) el.seekCurrent.textContent = fmtTime(pos);
    state.lyricsPosition = pos;
    anchorWordFillClock();
    tickLyrics(pos);
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
      clearLyrics();
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

    // Subtitle source picker (per-song operator override).
    updateSubtitleSourceSelect(data);
    if (el.subOffsetSlider && typeof data.subtitle_offset === 'number'
        && document.activeElement !== el.subOffsetSlider) {
      el.subOffsetSlider.value = data.subtitle_offset;
      if (el.subOffsetVal) {
        el.subOffsetVal.textContent = data.subtitle_offset.toFixed(2) + 's';
      }
    }

    updateLyrics(data);

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

  // Polish UI suffixes for the picker option text. Status enum is shared
  // with the backend payload (karaoke.py: _SUBTITLE_STATUS_*).
  const SUBTITLE_STATUS_SUFFIX = {
    ready:       'GOTOWE',
    download:    'POBIERZ',
    downloading: 'POBIERANIE…',
    na:          'N/D',
  };

  function updateSubtitleSourceSelect(data) {
    if (!el.subSrcTool || !el.subSrcSelect) return;
    const list = Array.isArray(data.subtitle_sources) ? data.subtitle_sources : [];
    if (!list.length || !data.now_playing) {
      el.subSrcTool.hidden = true;
      return;
    }
    el.subSrcTool.hidden = false;

    // ``auto`` (the absence of an override) is the implicit default. The
    // operator picks an explicit source to pin; clearing the pin happens
    // server-side when a stale variant disappears (B4).
    const selected = data.subtitle_source_override || '';
    // Re-render only when the option set actually changed — preserves
    // focus + scroll state on rapid polls. Cheap signature: source+status.
    const sig = list.map((s) => s.source + ':' + s.status).join('|') + '/' + selected;
    if (el.subSrcSelect.dataset.pkSig === sig) return;
    el.subSrcSelect.dataset.pkSig = sig;

    el.subSrcSelect.innerHTML = '';
    for (const s of list) {
      const opt = document.createElement('option');
      opt.value = s.source;
      const suffix = SUBTITLE_STATUS_SUFFIX[s.status] || s.status;
      opt.textContent = `${s.label} — ${suffix}`;
      // Block na/downloading from being picked. ``ready`` and
      // ``download`` are both selectable (download triggers a fetch).
      if (s.status === 'na' || s.status === 'downloading') {
        opt.disabled = true;
      }
      if (s.source === selected) {
        opt.selected = true;
      }
      el.subSrcSelect.appendChild(opt);
    }
    // ``downloading`` for the currently-selected source: keep it selected
    // visually so the operator sees their pick reflected even though the
    // option is disabled.
    if (selected && !el.subSrcSelect.value) {
      el.subSrcSelect.value = selected;
    }
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

    // Subtitle source picker — POST the choice and rely on the next
    // /now_playing poll to resync. Optimistic UI: mark the selected option
    // as ``…`` while the request is in flight so the operator sees their
    // click landed, then disable the select for ~1s to absorb double-clicks.
    if (el.subSrcSelect) {
      el.subSrcSelect.addEventListener('change', () => {
        const source = el.subSrcSelect.value;
        const songId = state.data && state.data.now_playing_song_id;
        if (!source || songId == null) return;
        const wasDisabled = el.subSrcSelect.disabled;
        el.subSrcSelect.disabled = true;
        // Bust the signature so the next poll always re-renders (in case the
        // server's status enum lands the same key we picked).
        delete el.subSrcSelect.dataset.pkSig;
        fetch('/subtitle_source', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ song_id: songId, source }),
        })
          .catch(() => {})
          .finally(() => {
            el.subSrcSelect.disabled = wasDisabled;
            fetchNowPlaying();
          });
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
    requestAnimationFrame(() => {
      el.full.classList.add('is-open');
      // Re-center the active line on open: the panel was hidden, so any
      // prior scrollIntoView calls were no-ops on a 0-height container.
      requestAnimationFrame(() => {
        state.lyricsUserScrolled = false;
        scrollActiveIntoView();
      });
    });
  }

  function close() {
    el.full.classList.remove('is-open');
    setTimeout(() => (el.full.hidden = true), 400);
    clearTimeout(state.lyricsScrollTimer);
    state.lyricsUserScrolled = false;
    stopWordFillRaf();
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

  // ===================== Lyrics panel =====================

  function lyricsParseModule() {
    return (window.PK && window.PK.LyricsParse) || null;
  }

  // render() writes state.data first, then calls sub-updates. updateLyrics
  // reads data.* directly (not state.data) so it sees this song's payload
  // even on the first call. tickLyrics reads state.data?.subtitle_offset,
  // which is fresh by the time updateLyrics runs.
  function updateLyrics(data) {
    if (!el.lyricsPanel || !el.lyricsScroll) return;

    if (data && data.subtitle_source_override === 'off') {
      clearLyrics();
      return;
    }

    const url = data && data.now_playing_subtitle_url;
    if (!url) {
      clearLyrics();
      return;
    }

    if (url === state.lyricsUrl) {
      // Same URL — pause/resume rAF if is_paused flipped, otherwise nothing.
      syncWordFillRaf();
      return;
    }

    // (A) Capture URL synchronously and shrink the cover immediately so the
    // user sees the panel placeholder before the fetch returns.
    state.lyricsUrl = url;
    el.full.classList.add('has-lyrics');
    showLyricsPanel();

    if (state.lyricsAbort) state.lyricsAbort.abort();
    const ac = new AbortController();
    state.lyricsAbort = ac;

    // Seed lyricsPosition from the now_playing payload so a mid-song open
    // (e.g., visibility change) doesn't tick from t=0.
    const seedPos = (data && typeof data.now_playing_position === 'number')
      ? data.now_playing_position : state.lyricsPosition;
    state.lyricsPosition = seedPos;

    fetch(url, { signal: ac.signal })
      .then((r) => (r.ok ? r.text() : ''))
      .then((text) => {
        // (B) Bail if a newer fetch has taken over while we were awaiting.
        if (state.lyricsUrl !== url) return;
        const mod = lyricsParseModule();
        const lines = mod ? mod.parseAss(text) : [];
        if (!lines.length) {
          clearLyrics();
          return;
        }
        state.lyrics = lines;
        buildLyricsDom(lines);
        // Cold start: if the first line begins ~immediately, light it now
        // so the user isn't staring at an unlit panel until the first
        // playback_position event arrives.
        if (state.lyricsPosition <= 0 && lines[0].start <= 0.5) {
          setActiveLine(0);
        } else {
          tickLyrics(state.lyricsPosition);
        }
      })
      .catch((err) => {
        // (C) AbortError is the expected unwind on song-change — never
        // call clearLyrics() on it, or a fast skip would wipe the next
        // song's panel mid-build.
        if (err && err.name === 'AbortError') return;
        clearLyrics();
      });
  }

  function buildLyricsDom(lines) {
    if (!el.lyricsScroll) return;
    el.lyricsScroll.textContent = '';
    const frag = document.createDocumentFragment();
    for (let i = 0; i < lines.length; i++) {
      const p = document.createElement('p');
      p.className = 'pk-lyric-line';
      p.dataset.pkLyricIdx = String(i);
      p.textContent = lines[i].text;
      if (state.isAdmin) {
        p.setAttribute('role', 'button');
        p.tabIndex = 0;
        p.classList.add('is-tappable');
      }
      frag.appendChild(p);
    }
    el.lyricsScroll.appendChild(frag);
    state.lyricsActiveIdx = -1;
    state.lyricsActiveEl = null;
  }

  function tickLyrics(pos) {
    if (!state.lyrics.length) return;
    const offset = (state.data && typeof state.data.subtitle_offset === 'number')
      ? state.data.subtitle_offset : 0;
    const t = (pos || 0) + offset;
    const mod = lyricsParseModule();
    const idx = mod ? mod.findActiveLineIdx(state.lyrics, t) : -1;
    setActiveLine(idx);
    paintWordFill(t);
    syncWordFillRaf();
  }

  function setActiveLine(idx) {
    if (idx === state.lyricsActiveIdx) return;
    if (state.lyricsActiveEl) {
      state.lyricsActiveEl.classList.remove('is-active', 'has-words');
      state.lyricsActiveEl.style.removeProperty('--pk-fill-pct');
    }
    state.lastFillPct = -1;
    state.lyricsActiveIdx = idx;
    if (idx < 0 || !el.lyricsScroll) {
      state.lyricsActiveEl = null;
      return;
    }
    const node = el.lyricsScroll.querySelector(
      '.pk-lyric-line[data-pk-lyric-idx="' + idx + '"]'
    );
    state.lyricsActiveEl = node || null;
    if (node) {
      node.classList.add('is-active');
      const line = state.lyrics[idx];
      if (line && line.words && line.words.length) {
        node.classList.add('has-words');
      }
      scrollActiveIntoView();
    }
  }

  function scrollActiveIntoView() {
    if (state.lyricsUserScrolled) return;
    if (!state.lyricsActiveEl || !el.lyricsScroll) return;
    if (el.lyricsPanel && el.lyricsPanel.hidden) return;
    state.lyricsAutoScrollUntil = (typeof performance !== 'undefined'
      ? performance.now() : Date.now()) + 800;
    state.lyricsActiveEl.scrollIntoView({
      behavior: state.reduceMotion ? 'auto' : 'smooth',
      block: 'center',
    });
  }

  function noteUserScroll() {
    const now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    if (now < state.lyricsAutoScrollUntil) return;
    state.lyricsUserScrolled = true;
    clearTimeout(state.lyricsScrollTimer);
    state.lyricsScrollTimer = setTimeout(() => {
      state.lyricsUserScrolled = false;
    }, 3000);
  }

  function clearLyrics() {
    if (state.lyricsAbort) {
      state.lyricsAbort.abort();
      state.lyricsAbort = null;
    }
    state.lyrics = [];
    state.lyricsUrl = null;
    state.lyricsActiveIdx = -1;
    state.lyricsActiveEl = null;
    state.lyricsHidden = false;
    state.lyricsPosition = 0;
    if (el.lyricsScroll) el.lyricsScroll.textContent = '';
    if (el.lyricsPanel) el.lyricsPanel.hidden = true;
    if (el.full) el.full.classList.remove('has-lyrics', 'has-lyrics-collapsed');
    setLyricsToggleIcon(false);
    stopWordFillRaf();
    clearTimeout(state.lyricsScrollTimer);
    state.lyricsUserScrolled = false;
  }

  function showLyricsPanel() {
    if (!el.lyricsPanel) return;
    el.lyricsPanel.hidden = false;
  }

  function toggleLyricsCollapsed() {
    state.lyricsHidden = !state.lyricsHidden;
    if (!el.full) return;
    el.full.classList.toggle('has-lyrics-collapsed', state.lyricsHidden);
    setLyricsToggleIcon(state.lyricsHidden);
    if (!state.lyricsHidden) {
      // Re-expanding: scroll the active line back into view on the next frame.
      requestAnimationFrame(() => {
        state.lyricsUserScrolled = false;
        scrollActiveIntoView();
      });
    }
  }

  function setLyricsToggleIcon(collapsed) {
    if (!el.lyricsToggleIcon || !el.lyricsToggleBtn) return;
    el.lyricsToggleIcon.classList.toggle('icon-angle-up', !collapsed);
    el.lyricsToggleIcon.classList.toggle('icon-angle-down', collapsed);
    el.lyricsToggleBtn.setAttribute(
      'aria-label', collapsed ? 'Show lyrics' : 'Hide lyrics'
    );
  }

  // Tap-to-seek on a lyric line — admin only. Delegated on the scroll
  // container so a single listener covers all lines without per-build wiring.
  function onLyricLineActivate(e) {
    if (!state.isAdmin) return;
    const node = e.target.closest('.pk-lyric-line.is-tappable');
    if (!node) return;
    const idx = parseInt(node.dataset.pkLyricIdx, 10);
    if (!isFinite(idx) || idx < 0 || idx >= state.lyrics.length) return;
    const line = state.lyrics[idx];
    if (!line) return;
    if (window.socket) window.socket.emit('seek', line.start);
  }

  function onLyricLineKeydown(e) {
    if (!state.isAdmin) return;
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    const node = e.target.closest('.pk-lyric-line.is-tappable');
    if (!node) return;
    e.preventDefault();
    onLyricLineActivate(e);
  }

  // preferences_update fires when any pilot drags the subtitle-offset
  // slider. Re-tick immediately so the highlight re-aligns without
  // waiting for the next playback_position event (~1 Hz away).
  function onPreferencesUpdate(data) {
    if (!data || typeof data.subtitle_offset !== 'number') return;
    if (state.data) state.data.subtitle_offset = data.subtitle_offset;
    anchorWordFillClock();
    tickLyrics(state.lyricsPosition);
  }

  // ===== Word-level fill (rAF interpolation) =====

  function anchorWordFillClock() {
    state.rafLastTickTs = (typeof performance !== 'undefined'
      ? performance.now() : Date.now());
    state.rafLastSocketPos = state.lyricsPosition;
  }

  // We deliberately do NOT key the rAF lifecycle off state.data.is_paused.
  // The server's is_paused can lag actual splash playback (e.g., user
  // resumes via the splash's own controls without going through the
  // pilot's /pause endpoint). If we trusted is_paused we'd kill the
  // word-fill animation while audio is still playing, which reads as
  // stutter on the phone. Instead, the rAF tick exits when socket
  // position events go stale — that's the real signal for "audio
  // stopped advancing".
  function syncWordFillRaf() {
    const line = state.lyrics[state.lyricsActiveIdx];
    const hasWords = !!(line && line.words && line.words.length);
    if (hasWords && state.lyricsActiveEl) {
      startWordFillRaf();
    } else {
      stopWordFillRaf();
    }
  }

  // Cap word-fill paint rate. 60 fps `background-clip: text` repaints on
  // a multi-line element thrash low-end phones (visible stutter). 60 ms
  // ≈ 16 fps still reads as smooth for word-by-word fill.
  const WORD_FILL_PAINT_INTERVAL_MS = 60;
  // playback_position is broadcast at ~1 Hz. If we go this long without
  // a socket update, treat the splash as actually-stopped and freeze the
  // fill — otherwise the local clock would drift past the real position.
  const POSITION_STALE_MS = 1800;

  function startWordFillRaf() {
    if (state.rafId !== null) return;
    anchorWordFillClock();
    let lastPaint = 0;
    const tick = (now) => {
      if (state.lyricsActiveIdx < 0 || !state.lyricsActiveEl) {
        state.rafId = null;
        return;
      }
      const line = state.lyrics[state.lyricsActiveIdx];
      if (!line || !line.words || !line.words.length) {
        state.rafId = null;
        return;
      }
      // Stop interpolating when socket position events have gone quiet
      // — the splash is paused for real (or disconnected). Next
      // playback_position event re-arms the rAF via syncWordFillRaf.
      if (now - state.rafLastTickTs > POSITION_STALE_MS) {
        state.rafId = null;
        return;
      }
      if (now - lastPaint >= WORD_FILL_PAINT_INTERVAL_MS) {
        const elapsed = (now - state.rafLastTickTs) / 1000;
        const offset = (state.data && typeof state.data.subtitle_offset === 'number')
          ? state.data.subtitle_offset : 0;
        const t = state.rafLastSocketPos + elapsed + offset;
        paintWordFill(t);
        lastPaint = now;
      }
      state.rafId = requestAnimationFrame(tick);
    };
    state.rafId = requestAnimationFrame(tick);
  }

  function stopWordFillRaf() {
    if (state.rafId !== null) {
      cancelAnimationFrame(state.rafId);
      state.rafId = null;
    }
  }

  // Paint one frame of the active line's amber fill. t is the offset-
  // adjusted playback time. Computes the global percentage of the line
  // text that should be amber, writes to the --pk-fill-pct custom prop.
  // Skips the DOM write when pct hasn't moved meaningfully — repainting
  // the same gradient costs as much as repainting a new one.
  function paintWordFill(t) {
    if (state.lyricsActiveIdx < 0 || !state.lyricsActiveEl) return;
    const line = state.lyrics[state.lyricsActiveIdx];
    if (!line || !line.words || !line.words.length) return;

    let totalChars = 0;
    for (const w of line.words) totalChars += w.text.length;
    if (totalChars <= 0) return;

    let charsBefore = 0;
    let pct = 0;
    if (t <= line.words[0].start) {
      pct = 0;
    } else {
      pct = 100;
      for (let i = 0; i < line.words.length; i++) {
        const w = line.words[i];
        if (t >= w.end) {
          charsBefore += w.text.length;
          continue;
        }
        const span = Math.max(0.001, w.end - w.start);
        const wordProgress = Math.max(0, Math.min(1, (t - w.start) / span));
        pct = ((charsBefore + wordProgress * w.text.length) / totalChars) * 100;
        break;
      }
    }
    if (Math.abs(pct - state.lastFillPct) < 0.5) return;
    state.lastFillPct = pct;
    state.lyricsActiveEl.style.setProperty(
      '--pk-fill-pct', pct.toFixed(1) + '%'
    );
  }

  window.PK.NowPlaying = { init, open, close };
})();
