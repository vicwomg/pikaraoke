// Socket is initialized in handleConfirmation() so nothing connects before the
// user clicks Start (or testAutoplayCapability auto-confirms).
let socket = null;
let mouseTimer = null;
let cursorVisible = false;
let nowPlaying = {};
let octopusInstance = null;
let currentSubtitleUrl = null;

// Construct a SubtitlesOctopus worker bound to `video` for `subUrl`. Assumes
// the URL has already been preflight-verified to return 200; libass crashes
// with `jso: Failed to start a track` + exit(4) when fed a 404/5xx body.
//
// onReady kick: the worker boots with `_isPaused=true` and only flips to
// `false` when the <video> fires a `playing` event. When we hot-swap the
// worker mid-playback (lyrics_upgraded T2->T3), `playing` already fired
// before dispose, so the new worker never receives setIsPaused(false) via
// the listener and renders only on `timeupdate` ticks (~4 Hz, ~8 fps subs).
// A fresh page reload is smooth because the worker is constructed before
// `playing` fires. Kick the new worker once it signals ready, when the
// video is already playing.
function initOctopus(video, subUrl) {
  const options = {
    video: video,
    subUrl: subUrl,
    fonts: ["/static/fonts/Arial.ttf", "/static/fonts/DroidSansFallback.ttf"],
    debug: true,
    timeOffset: Number(PikaraokeConfig.subtitleOffset) || 0,
    workerUrl: "/static/js/subtitles-octopus-worker.js",
    onReady: () => {
      if (!octopusInstance || !video) return;
      // If video is already playing when the new worker comes up (hot-swap
      // case), the worker won't see a `playing` event so kick it manually.
      // Otherwise the existing listener will handle it on the next play.
      if (!video.paused && !video.ended) {
        const t = video.currentTime + (Number(PikaraokeConfig.subtitleOffset) || 0);
        try { octopusInstance.setIsPaused(false, t); } catch (e) { /* worker raced dispose */ }
      }
      if (video.playbackRate && video.playbackRate !== 1) {
        try { octopusInstance.setRate(video.playbackRate); } catch (e) { /* same */ }
      }
    }
  };
  try {
    octopusInstance = new SubtitlesOctopus(options);
    if (uiScale) {
      // Find the canvas created by SubtitlesOctopus (sibling of the video)
      const canvas = video.parentNode.querySelector('canvas');
      if (canvas) {
        canvas.style.transform = `scale(${uiScale})`;
        canvas.style.transformOrigin = 'bottom center';
      }
    }
  } catch (e) { console.error(e); }
}

let showMenu = false;
let menuButtonVisible = false;
let autoplayConfirmed = false;
let volume = 0.85;
const playbackStartTimeout = 10000;
const bgMediaResumeDelay = 2000;
let isScoreShown = false;
const hasBgVideo = PikaraokeConfig.hasBgVideo;
let currentVideoUrl = null;
let hlsInstance = null;

// Client-side audio mixing state. Used in two shapes:
//   - Stems mode (vocal_removal): 2 tracks (vocals, instrumental) from the
//     Demucs cache at /stream/<uid>/<stem>.<ext>.
//   - Single-track mode (pitch/normalize/avsync without vocal_removal):
//     1 track piped from ffmpeg at /stream/audio/<uid>/track.wav.
// Both shapes use the same drift-correction and seek machinery.
let stemAudioCtx = null;
// Generalized audio tracks attached to the video:
//   { tracks: { label: {el, source, gain} }, labels: [..], offsetSec, _handlers, _video }
// offsetSec applies an AV sync shift: audio.currentTime = video.currentTime + offsetSec.
let audioNodes = null;
// Stems data for the current song if AudioContext wasn't running when
// stems_ready arrived. Applied when the context becomes running.
let pendingStemsData = null;
let idleTime = 0;
let screensaverTimeoutSeconds = PikaraokeConfig.screensaverTimeout;
let bg_playlist = [];
let bgMediaResumeTimeout = null;
let scoreReviews = {
  low: ["Better luck next time!"],
  mid: ["Not bad!"],
  high: ["Great job!"],
};
let isMaster = false;
let uiScale = null;
let clockIntervalId = null;

// Browser detection
const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
const isMobileSafari = isSafari && (/iPhone|iPad|iPod/i.test(navigator.userAgent) || navigator.maxTouchPoints > 1);
const isChrome = /chrome/i.test(navigator.userAgent) && !/edg/i.test(navigator.userAgent);
const isFirefox = /firefox/i.test(navigator.userAgent);
const isEdge = /edg/i.test(navigator.userAgent);
const isSupportedBrowser = isSafari || isChrome || isFirefox || isEdge;

const isMediaPlaying = (media) =>
  !!(
    media.currentTime > 0 &&
    !media.paused &&
    !media.ended &&
    media.readyState > 2
  );

const formatTime = (seconds) => {
  if (isNaN(seconds)) {
    return "00:00";
  }
  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;
  const formattedMinutes = String(minutes).padStart(2, "0");
  const formattedSeconds = String(secs).padStart(2, "0");
  return `${formattedMinutes}:${formattedSeconds}`;
}

// Map the DB `lyrics_source` tag to a short human label + semantic CSS
// variant. Keeps the badge compact while surfacing provenance (user-authored
// vs auto-generated, and which auto pipeline produced it). Keys are the
// canonical scheme used by the source-picker (see karaoke_database.py:
// VALID_SUBTITLE_SOURCES).
const LYRICS_SOURCE_LABELS = {
  user:           { text: "Twoje napisy",  variant: "user"   },
  lrclib:         { text: "LRCLib",         variant: "trust"  },
  "lrclib-sync":  { text: "LRCLib + sync",  variant: "trust"  },
  "genius-sync":  { text: "Genius + sync",  variant: "trust"  },
  AI:             { text: "AI",             variant: "auto"   },
  "youtube-vtt":  { text: "YouTube CC",     variant: "trust"  },
};

const updateLyricsSourceBadge = (source) => {
  const el = document.getElementById("lyrics-source");
  const label = document.getElementById("lyrics-source-label");
  if (!el || !label) return;
  const entry = source && LYRICS_SOURCE_LABELS[source];
  if (!entry) {
    el.style.display = "none";
    el.removeAttribute("data-variant");
    return;
  }
  label.textContent = entry.text;
  el.setAttribute("data-variant", entry.variant);
  el.style.display = "";
}

const testAutoplayCapability = async () => {
  // Detect whether the browser will allow audio autoplay (US-27).
  //
  // The canonical spec signal is: call play() on an audible element and
  // watch for a rejected promise with NotAllowedError. The old
  // muted-then-unmuted dance was brittle because some browsers silently
  // keep the element muted rather than reporting a policy block, which
  // looked like "autoplay failed" even when the user gesture would have
  // unblocked it on the next interaction anyway.
  const testVideo = document.createElement('video');
  testVideo.playsInline = true;
  testVideo.volume = 0.01;   // audible (required for the policy test) but near-silent
  testVideo.muted = false;
  testVideo.src = "/static/video/test_autoplay.mp4";

  // Load the asset — graceful fallback when it's missing from the build.
  // Previously the onerror handler always showed the modal, forcing every
  // deployment without the asset to click-through. Missing asset ≠ blocked
  // autoplay, so proceed on the assumption that autoplay is allowed.
  try {
    await new Promise((resolve, reject) => {
      testVideo.onloadeddata = resolve;
      testVideo.onerror = () => reject(new Error("asset_missing"));
    });
  } catch (e) {
    if (e && e.message === "asset_missing") {
      console.warn("autoplay test asset missing; assuming autoplay is allowed");
      handleConfirmation();
      return;
    }
    console.warn("autoplay test load failed", e);
    $('#permissions-modal').addClass('is-active');
    return;
  }

  try {
    await testVideo.play();
    testVideo.pause();
    handleConfirmation();
  } catch (e) {
    // NotAllowedError is the spec signal for "user gesture required".
    // Treat anything else as a blocked policy too, to stay fail-safe.
    if (e && e.name === "NotAllowedError") {
      console.log("Autoplay blocked by browser policy (NotAllowedError)");
    } else {
      console.log("Autoplay test rejected", e);
    }
    $('#permissions-modal').addClass('is-active');
  }
};

const handleConfirmation = () => {
  if (autoplayConfirmed) return;
  autoplayConfirmed = true;
  $('#permissions-modal').removeClass('is-active');

  socket = io();
  setupSocketEvents();
  handleSocketRecovery();
  if (socket.connected) socket.emit("register_splash");

  setupBackgroundMusicPlayer();

  ensureAudioContextRunning();
  updateBackgroundMediaState(true);
  loadNowPlaying();
};

const hideVideo = () => {
  $("#video-container").hide();
}

const endSong = async (reason = null, showScore = false) => {
  if (showScore && !PikaraokeConfig.disableScore) {
    isScoreShown = true;
    await startScore("/static/");
    isScoreShown = false;
  }
  currentVideoUrl = null;
  currentSubtitleUrl = null;
  if (hlsInstance) {
    hlsInstance.destroy();
    hlsInstance = null;
  }
  teardownStemAudio();
  const video = getVideoPlayer();
  video.muted = false;
  video.pause();
  $("#video-source").attr("src", "");
  video.load();
  hideVideo();
  if (isMaster) {
    socket.emit("end_song", reason);
  } else {
    console.log("Slave active (read-only): skipping end_song emission");
  }
}

const getBackgroundMusicPlayer = () => document.getElementById('background-music');
const getBackgroundVideoPlayer = () => document.getElementById('bg-video');
const getVideoPlayer = () => $("#video")[0]

const getNextBgMusicSong = () => {
  let currentSong = getBackgroundMusicPlayer().getAttribute('src');
  let nextSong = bg_playlist[0];
  if (currentSong) {
    let currentIndex = bg_playlist.indexOf(currentSong);
    if (currentIndex >= 0 && currentIndex < bg_playlist.length - 1) {
      nextSong = bg_playlist[currentIndex + 1];
    }
  }
  return nextSong;
}

const playBGMusic = async (play) => {
  const audio = getBackgroundMusicPlayer();
  if (play) {
    if (PikaraokeConfig.disableBgMusic) return;
    if (!autoplayConfirmed) return;
    if (bg_playlist.length === 0) return;

    if (!audio.getAttribute('src')) audio.setAttribute('src', getNextBgMusicSong());

    if (isMediaPlaying(audio)) return;
    audio.volume = 0;
    if (audio.readyState <= 2) await audio.load();
    await audio.play().catch(e => console.log("Autoplay blocked (music)"));
    $(audio).animate({ volume: PikaraokeConfig.bgMusicVolume }, 2000);
  } else {
    if (audio) {
      $(audio).animate({ volume: 0 }, 2000, () => audio.pause());
    }
  }
}

const playBGVideo = async (play) => {
  const bgVideo = getBackgroundVideoPlayer();
  const bgVideoContainer = $('#bg-video-container');

  if (play) {
    if (PikaraokeConfig.disableBgVideo) return;
    if (!autoplayConfirmed) return;

    if (isMediaPlaying(bgVideo)) return;
    $("#bg-video").attr("src", "/stream/bg_video");
    if (bgVideo.readyState <= 2) await bgVideo.load();
    bgVideo.play().catch(() => console.log("Autoplay blocked (video)"));
    bgVideoContainer.fadeIn(2000);
  } else {
    if (bgVideo && isMediaPlaying(bgVideo)) {
      bgVideo.pause();
      bgVideoContainer.fadeOut(2000);
    }
  }
}

const shouldBackgroundMediaPlay = () => {
  return autoplayConfirmed &&
    !nowPlaying.now_playing &&
    !nowPlaying.up_next;
};

const updateBackgroundMediaState = (immediate = false) => {
  // Clear any pending resume
  if (bgMediaResumeTimeout) {
    clearTimeout(bgMediaResumeTimeout);
    bgMediaResumeTimeout = null;
  }

  if (shouldBackgroundMediaPlay()) {
    if (immediate) {
      playBGMusic(true);
      if (hasBgVideo) playBGVideo(true);
    } else {
      bgMediaResumeTimeout = setTimeout(() => {
        bgMediaResumeTimeout = null;
        if (shouldBackgroundMediaPlay()) {
          playBGMusic(true);
          if (hasBgVideo) playBGVideo(true);
        }
      }, bgMediaResumeDelay);
    }
  } else {
    playBGMusic(false);
    playBGVideo(false);
  }
};

const flashNotificationQueue = [];
let flashNotificationShowing = false;

const showNextFlashNotification = () => {
  if (flashNotificationShowing) return;
  const next = flashNotificationQueue.shift();
  if (!next) return;
  flashNotificationShowing = true;
  const sn = $("#splash-notification");
  sn.html(next.message);
  sn.addClass(next.categoryClass);
  sn.fadeIn();
  setTimeout(() => {
    sn.fadeOut();
    setTimeout(() => {
      sn.html("");
      sn.removeClass(next.categoryClass);
      flashNotificationShowing = false;
      showNextFlashNotification();
    }, 450);
  }, 3000);
};

const flashNotification = (message, categoryClass) => {
  // Dedupe adjacent duplicates so a burst of the same stage event
  // (e.g. repeated socket retries) doesn't stack up a long queue.
  const tail = flashNotificationQueue[flashNotificationQueue.length - 1];
  if (tail && tail.message === message && tail.categoryClass === categoryClass) return;
  flashNotificationQueue.push({ message, categoryClass });
  showNextFlashNotification();
}

// Warnings are buffered per-song so an emit that arrives before the song
// starts playing (e.g. lyrics fetch fails at download time) attaches to
// the correct song later, not to whatever is currently on splash. The
// buffer is bounded so queued-but-never-played songs don't grow unbounded.
const SONG_WARNING_BUFFER_MAX = 40;
const songWarningsBySong = new Map();  // basename -> [{message, detail}]
let songWarningSongKey = null;

const getCurrentSongWarnings = () =>
  songWarningSongKey ? songWarningsBySong.get(songWarningSongKey) || [] : [];

// Strip the YouTube ID suffix ("Title---dQw4w9WgXcQ" / "Title [dQw4w9WgXcQ]")
// from a basename so the tooltip header reads as a human title, not a filename.
const humanizeSongKey = (key) => {
  if (!key) return "";
  return String(key)
    .replace(/---[A-Za-z0-9_-]{11}$/, "")
    .replace(/\s*\[[A-Za-z0-9_-]{11}\]$/, "")
    .trim();
};

const setTooltipOpen = (open) => {
  const icon = $("#song-warning");
  const tooltip = $("#song-warning-tooltip");
  if (open) {
    tooltip.show();
    icon.attr("aria-expanded", "true");
  } else {
    tooltip.hide();
    icon.attr("aria-expanded", "false");
  }
};

const renderSongWarnings = () => {
  const icon = $("#song-warning");
  const list = $("#song-warning-messages");
  const nameEl = $("#song-warning-song-name");
  const warnings = getCurrentSongWarnings();
  if (!warnings.length) {
    icon.hide();
    setTooltipOpen(false);
    list.empty();
    nameEl.text("");
    return;
  }
  const html = warnings.map((w) => {
    const title = $("<div class='warning-title'></div>").text(w.message)[0].outerHTML;
    const detail = w.detail
      ? $("<div class='warning-detail'></div>").text(w.detail)[0].outerHTML
      : "";
    return `<div class="warning-entry">${title}${detail}</div>`;
  }).join("");
  list.html(html);
  nameEl.text(humanizeSongKey(songWarningSongKey));
  const title = warnings.length === 1
    ? warnings[0].message
    : `${warnings.length} warnings — click to view`;
  icon.find("i").attr("title", title);
  icon.show();
};

const clearSongWarnings = () => {
  // Only clears the icon/tooltip render; the buffer itself retains
  // entries for other songs.
  renderSongWarnings();
};

const bufferSongWarning = (songKey, message, detail) => {
  if (!songKey) return false;
  const list = songWarningsBySong.get(songKey) || [];
  if (list.some(w => w.message === message && w.detail === detail)) return false;
  list.push({ message, detail });
  songWarningsBySong.set(songKey, list);
  // Bound the buffer: oldest song keys fall out first (Map preserves
  // insertion order).
  while (songWarningsBySong.size > SONG_WARNING_BUFFER_MAX) {
    const first = songWarningsBySong.keys().next().value;
    if (first === songWarningSongKey) break;  // never evict the active song
    songWarningsBySong.delete(first);
  }
  return true;
};

const copySongWarnings = () => {
  const warnings = getCurrentSongWarnings();
  if (!warnings.length) return;
  const text = warnings
    .map(w => w.detail ? `${w.message}\n${w.detail}` : w.message)
    .join("\n\n");
  const done = (ok) => flashNotification(ok ? "Copied" : "Copy failed", ok ? "is-success" : "is-danger");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => done(true), () => done(false));
  } else {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
    done(ok);
  }
};

const setupScreensaver = () => {
  if (screensaverTimeoutSeconds > 0) {
    setInterval(() => {
      let screensaver = document.getElementById('screensaver');
      let video = getVideoPlayer();
      if (isMediaPlaying(video) || cursorVisible) {
        idleTime = 0;
      }
      if (idleTime >= screensaverTimeoutSeconds) {
        if (screensaver.style.visibility === 'hidden') {
          screensaver.style.visibility = 'visible';
          playBGVideo(false);
          startScreensaver(); // depends on upstream screensaver.js import
        }
        if (idleTime > screensaverTimeoutSeconds + 36000) idleTime = screensaverTimeoutSeconds;
      } else {
        if (screensaver.style.visibility === 'visible') {
          screensaver.style.visibility = 'hidden';
          stopScreensaver(); // depends on upstream screensaver.js import
          updateBackgroundMediaState(true);
        }
      }
      idleTime++;
    }, 1000)
  }
}

const ensureAudioContextRunning = () => {
  if (!stemAudioCtx) {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) {
      console.warn("Web Audio API not available; stem mixing disabled");
      return Promise.resolve();
    }
    stemAudioCtx = new Ctor();
  }
  if (stemAudioCtx.state === "suspended") return stemAudioCtx.resume();
  return Promise.resolve();
};

// Sets up stems and crossfades from whatever audio source is currently
// playing — the video's native track, or the m4a warmup pipe when the
// split-download path served a silent video. Bails if the context
// isn't running so the current source keeps playing; stems are re-tried
// when a gesture resumes the context.
const applyPendingStems = () => {
  if (!pendingStemsData) return;
  const data = pendingStemsData;
  const video = getVideoPlayer();
  const currentUid = currentVideoUrl ? extractStreamUid(currentVideoUrl) : null;
  if (data.stream_uid !== currentUid) {
    // Either the browser hasn't received now_playing yet (stems_ready can
    // race ahead of it when Demucs prewarm finishes right as play_file
    // runs) or the song truly changed. Keep pending so the next
    // handleNowPlayingUpdate can retry; invalidation for a genuinely new
    // song happens there.
    return;
  }
  const labels = audioNodes ? audioNodes.labels : [];
  if (labels.includes("vocals") && labels.includes("instrumental")) {
    pendingStemsData = null;  // already on stems (cache hit path)
    return;
  }
  if (!stemAudioCtx || stemAudioCtx.state !== "running") return;
  pendingStemsData = null;

  const np = {
    ...nowPlaying,
    vocals_url: data.vocals_url,
    instrumental_url: data.instrumental_url,
  };
  const fadeMs = 300;

  const startStems = () => {
    setupStemAudio(np, video);
    if (!audioNodes || !stemAudioCtx) return;
    // Pre-silence stem gains so they ramp up instead of popping in at
    // full vocal/instrumental volume.
    const now = stemAudioCtx.currentTime;
    for (const label of audioNodes.labels) {
      const g = audioNodes.tracks[label].gain.gain;
      g.cancelScheduledValues(now);
      g.setValueAtTime(0, now);
      try { audioNodes.tracks[label].el.currentTime = video.currentTime; } catch (e) {}
      audioNodes.tracks[label].el.play().catch(() => {});
    }
    fadeStems(stemTargets(), fadeMs);
  };

  if (labels.includes("track")) {
    // Warmup m4a pipe is playing. Fade it out first, then swap to stems
    // — setupStemAudio tears down the track pipe as part of its setup.
    fadeStems({ track: 0 }, fadeMs);
    setTimeout(startStems, fadeMs);
    return;
  }

  // Default: video's own audio track is the source — fade it down as
  // stems ramp up.
  startStems();
  $(video).animate({ volume: 0 }, fadeMs, () => {
    video.muted = true;
  });
};

// Extracts the stream_uid from the tail of a stream URL so stems_ready
// events can be matched against the song that's actually playing. Handles
// /stream/<uid>.<ext>, /stream/video/<uid>.mp4, and /stream/full/<uid>.
const extractStreamUid = (url) => {
  if (!url) return null;
  const lastSeg = url.split("/").pop() || "";
  const dot = lastSeg.indexOf(".");
  return (dot === -1 ? lastSeg : lastSeg.slice(0, dot)) || null;
};

// Chrome/Edge block AudioContext.resume() until a user gesture happens.
// First click/keydown/touch triggers resume so stems can play when they
// become ready, even if the user never clicked the permissions modal
// (testAutoplayCapability skips it when autoplay is already allowed).
// Also applies any stems_ready that arrived while suspended.
const resumeOnFirstGesture = () => {
  ensureAudioContextRunning().then(applyPendingStems);
};
document.addEventListener("click", resumeOnFirstGesture);
document.addEventListener("keydown", resumeOnFirstGesture);
document.addEventListener("touchstart", resumeOnFirstGesture);

const teardownStemAudio = () => {
  if (!audioNodes) return;
  // Detach listeners BEFORE nulling audioNodes — otherwise stale handlers
  // fire on subsequent video events and throw "Cannot read properties of
  // null" because they close over the module-level `audioNodes`.
  if (audioNodes._handlers && audioNodes._video) {
    for (const [evt, fn] of Object.entries(audioNodes._handlers)) {
      audioNodes._video.removeEventListener(evt, fn);
    }
  }
  for (const label of audioNodes.labels) {
    const node = audioNodes.tracks[label];
    if (!node) continue;
    try { node.el.pause(); } catch (e) {}
    try { node.source.disconnect(); } catch (e) {}
    try { node.gain.disconnect(); } catch (e) {}
    node.el.removeAttribute("src");
    try { node.el.load(); } catch (e) {}
    node.el.remove();
  }
  audioNodes = null;
};

// tracks: [{ url, gain, label }, ...]
// offsetMs: applied as audio.currentTime = video.currentTime + offsetMs/1000
const setupAudioTracks = (tracks, video, offsetMs = 0) => {
  teardownStemAudio();
  ensureAudioContextRunning();
  if (!stemAudioCtx) return;

  const makeTrack = (url, initialGain) => {
    const el = new Audio();
    el.crossOrigin = "anonymous";
    el.preload = "auto";
    el.src = url;
    const source = stemAudioCtx.createMediaElementSource(el);
    const gain = stemAudioCtx.createGain();
    gain.gain.value = initialGain;
    source.connect(gain).connect(stemAudioCtx.destination);
    return { el, source, gain };
  };

  const nodes = {
    tracks: {},
    labels: [],
    offsetSec: (offsetMs || 0) / 1000,
  };
  for (const t of tracks) {
    nodes.tracks[t.label] = makeTrack(t.url, t.gain);
    nodes.labels.push(t.label);
  }

  const targetTime = () => video.currentTime + nodes.offsetSec;

  // Sync audio tracks to video. Any drift > 150ms is corrected.
  const syncAll = () => {
    if (!audioNodes) return;
    const t = targetTime();
    for (const label of audioNodes.labels) {
      const a = audioNodes.tracks[label].el;
      if (Math.abs(a.currentTime - t) > 0.15) {
        try { a.currentTime = t; } catch (e) {}
      }
    }
  };
  // Set while we force a video seek to catch up to free-running stems
  // (resume-from-hidden). Suppresses the normal seeking/seeked cascade
  // so stems aren't paused, reset, and replayed — which is what produced
  // the residual micro-stutter when refocusing the tab.
  let internalSeek = false;

  const playAll = () => {
    if (!audioNodes) return;
    // Resume-from-hidden: stems advanced while the browser-paused <video>
    // stayed frozen (onVideoPause skips pauseAll while document.hidden).
    // Reverse the resync direction — seek the video forward to where the
    // stems actually are — so the stems keep playing untouched and the
    // video catches up to them.
    const anyLabel = audioNodes.labels[0];
    if (anyLabel) {
      const stem = audioNodes.tracks[anyLabel].el;
      const drift = stem.currentTime - targetTime();
      if (drift > 0.3) {
        internalSeek = true;
        try { video.currentTime = stem.currentTime - audioNodes.offsetSec; }
        catch (e) { internalSeek = false; }
      }
    }
    if (!internalSeek) syncAll();
    for (const label of audioNodes.labels) {
      audioNodes.tracks[label].el.play().catch(() => {});
    }
  };
  const pauseAll = () => {
    if (!audioNodes) return;
    for (const label of audioNodes.labels) {
      try { audioNodes.tracks[label].el.pause(); } catch (e) {}
    }
  };
  // On HLS seek the video stalls (0.5–2s) and may land on a fresh segment;
  // the audio stream cannot guarantee a seek to the target position (for
  // live Demucs the data may not be written; for non-range chunked streams
  // some browsers restart the decoder). We pause audio during `seeking` so
  // tracks don't play free, then on `seeked` attempt to re-sync. If a track
  // cannot reach the target within tolerance we leave it paused — silent
  // audio is better than a 0.3s loop of whatever the decoder lands on.
  const seekingHandler = () => {
    // Our own catch-up seek: stems are the source of truth and must
    // keep playing uninterrupted. Skip the pauseAll cascade.
    if (internalSeek) return;
    pauseAll();
  };
  const seekedHandler = () => {
    if (!audioNodes) return;
    if (internalSeek) {
      // Catch-up seek complete. Leave stems alone — they already hold
      // the correct position; video has been brought to them.
      internalSeek = false;
      return;
    }
    const t = targetTime();
    for (const label of audioNodes.labels) {
      try { audioNodes.tracks[label].el.currentTime = t; } catch (e) {}
    }
    if (video.paused) return;
    setTimeout(() => {
      if (!audioNodes) return;
      const t2 = targetTime();
      for (const label of audioNodes.labels) {
        const a = audioNodes.tracks[label].el;
        if (Math.abs(a.currentTime - t2) < 0.5) {
          a.play().catch(() => {});
        }
      }
    }, 120);
  };
  // Periodic drift correction while playing. Skip during seeking, and skip
  // tracks we've already given up on (paused) — don't loop-retry a seek
  // that the source stream can't service.
  const driftHandler = () => {
    if (!audioNodes) return;
    if (video.paused || video.seeking) return;
    const t = targetTime();
    for (const label of audioNodes.labels) {
      const a = audioNodes.tracks[label].el;
      if (a.paused) continue;
      const drift = a.currentTime - t;
      if (Math.abs(drift) > 0.2) {
        try { a.currentTime = t; } catch (e) {}
      }
    }
  };

  // Browsers pause the <video> when the document is hidden (macOS workspace
  // switch, minimized window). Cascading that to stems stops the song, so
  // by default we ignore those pauses and keep stems running. Users who
  // prefer the old behavior enable the `pause_on_blur` preference.
  const onVideoPause = () => {
    if (document.hidden && !PikaraokeConfig.pauseOnBlur) return;
    pauseAll();
  };
  const handlers = {
    play: playAll,
    pause: onVideoPause,
    seeking: seekingHandler,
    seeked: seekedHandler,
    timeupdate: driftHandler,
  };
  for (const [evt, fn] of Object.entries(handlers)) {
    video.addEventListener(evt, fn);
  }
  nodes._handlers = handlers;
  nodes._video = video;
  audioNodes = nodes;
};

const setupStemAudio = (np, video) => {
  setupAudioTracks(
    [
      { url: np.vocals_url, gain: np.vocal_volume ?? 0.3, label: "vocals" },
      { url: np.instrumental_url, gain: np.instrumental_volume ?? 1.0, label: "instrumental" },
    ],
    video,
    0,
  );
};

const applyStemVolumes = (np) => {
  if (!audioNodes) return;
  if (audioNodes.tracks.vocals && typeof np.vocal_volume === "number") {
    audioNodes.tracks.vocals.gain.gain.value = np.vocal_volume;
  }
  if (audioNodes.tracks.instrumental && typeof np.instrumental_volume === "number") {
    audioNodes.tracks.instrumental.gain.gain.value = np.instrumental_volume;
  }
};

// Smooth fades on audio gain nodes, mirroring the video.volume jQuery
// animations in the passthrough path so pause/play feels the same either
// way. `targets` maps label -> value; unknown labels are ignored.
const fadeStems = (targets, durationMs) => {
  if (!audioNodes || !stemAudioCtx) return false;
  const now = stemAudioCtx.currentTime;
  const dur = durationMs / 1000;
  for (const label of audioNodes.labels) {
    if (!(label in targets)) continue;
    const g = audioNodes.tracks[label].gain.gain;
    g.cancelScheduledValues(now);
    g.setValueAtTime(g.value, now);
    g.linearRampToValueAtTime(targets[label], now + dur);
  }
  return true;
};

const stemTargets = () => ({
  vocals: typeof nowPlaying.vocal_volume === "number" ? nowPlaying.vocal_volume : 0.3,
  instrumental: typeof nowPlaying.instrumental_volume === "number" ? nowPlaying.instrumental_volume : 1.0,
});

// Resume-from-pause gain targets for whichever audio-track shape is attached.
const audioTargets = () => {
  if (!audioNodes) return {};
  if (audioNodes.labels.length === 1 && audioNodes.labels[0] === "track") {
    return { track: 1.0 };  // single-track pipe: full gain
  }
  return stemTargets();
};

const handleNowPlayingUpdate = (np) => {
  nowPlaying = np;
  // Apply stem volume updates on every poll/socket update so home-page slider
  // changes take effect mid-song. Safe when audioNodes is null.
  applyStemVolumes(np);
  // Switch which buffered warnings are shown when the song changes. The
  // buffer retains entries for other songs so a warning emitted at download
  // time (before the song starts) still lands when its song begins playing.
  // When now_playing is null (idle between songs), keep whatever songWarning
  // key we had so pre-playback / end-of-song warnings stay visible until
  // a new song takes over.
  // Don't clobber unacknowledged warnings when the next song starts (US-13):
  // if the previous song had buffered warnings the operator hasn't dismissed,
  // keep them on screen. The retarget happens once they dismiss (see the
  // song_warnings_dismissed handler).
  const songKey = np.now_playing_basename || null;
  if (songKey && songKey !== songWarningSongKey) {
    const oldHasWarnings =
      songWarningSongKey &&
      (songWarningsBySong.get(songWarningSongKey) || []).length > 0;
    if (!oldHasWarnings) {
      songWarningSongKey = songKey;
      renderSongWarnings();
    }
  }
  if (np.now_playing) {

    // Handle updating now playing HTML
    let nowPlayingHtml = `<span>${np.now_playing}</span> `;
    if (np.now_playing_transpose !== 0) {
      nowPlayingHtml += `<span class='is-size-6 has-text-success'><b>Key</b>: ${getSemitonesLabel(np.now_playing_transpose)} </span>`;
    }
    $("#now-playing-song").html(nowPlayingHtml);
    $("#now-playing-singer").html(np.now_playing_user);
    updateLyricsSourceBadge(np.now_playing_lyrics_source);
    $("#now-playing").fadeIn();
  } else {
    $("#now-playing").fadeOut();
    updateLyricsSourceBadge(null);
  }
  if (np.up_next) {
    $("#up-next-song").html(np.up_next);
    $("#up-next-singer").html(np.next_user);
    $("#up-next").fadeIn();
  } else {
    $("#up-next").fadeOut();
  }

  // Update bg music and video state
  if (np.now_playing || np.up_next) {
    idleTime = 0;
  }
  updateBackgroundMediaState();

  const video = getVideoPlayer();

  // Setup ASS subtitle file if found.
  //
  // The server emits `now_playing` on many state changes (queue updates,
  // subtitle-offset tweaks, lyrics_upgraded tier bumps). Re-creating the
  // SubtitlesOctopus worker on every one of them burns CPU and — when the
  // events arrive in rapid succession — races the worker's WASM init, so
  // libass never finishes loading a track and no subs appear. Only tear
  // down + re-init when the URL actually changed (i.e. a new `?v=` from
  // _on_lyrics_upgraded, a different song, or gaining/losing subs).
  const subtitleUrl = np.now_playing_subtitle_url;
  if (typeof np.subtitle_offset === 'number') {
    PikaraokeConfig.subtitleOffset = np.subtitle_offset;
    if (octopusInstance) {
      octopusInstance.timeOffset = Number(PikaraokeConfig.subtitleOffset) || 0;
    }
  }

  // Source picker ``off`` pin: server flags the song as having subtitles
  // intentionally disabled. Tear down any in-flight Octopus instance and
  // hide the canvas; the ``subtitle_on`` socket event handles the return
  // path. TD1 from the autoplan review — ensures the off pin survives
  // page reloads (the canvas-only socket toggle was ephemeral).
  const subsDisabled = !!np.subtitle_disabled;
  const canvas = document.getElementById('subtitles-canvas')
    || (video && video.parentNode && video.parentNode.querySelector('canvas'));
  if (canvas) canvas.style.display = subsDisabled ? 'none' : '';
  if (subsDisabled) {
    if (octopusInstance) {
      octopusInstance.dispose();
      octopusInstance = null;
    }
    currentSubtitleUrl = null;
  } else if (subtitleUrl !== currentSubtitleUrl) {
    if (octopusInstance) {
      octopusInstance.dispose();
      octopusInstance = null;
    }
    currentSubtitleUrl = subtitleUrl || null;
    if (subtitleUrl && video) {
      // Preflight: server publishes the URL unconditionally (so `_on_lyrics_upgraded`
      // has something to cache-bust), but the .ass may not exist yet. A 404 body
      // ("Subtitle file not found...") fed to libass crashes the worker with
      // `jso: Failed to start a track` + exit(4). Verify 200 before constructing;
      // the `?v=` bump from `_on_lyrics_upgraded` will retry once the .ass lands.
      fetch(subtitleUrl, { method: 'HEAD' })
        .then(r => {
          if (!r.ok) return;
          if (currentSubtitleUrl !== subtitleUrl) return; // URL changed while fetching
          initOctopus(video, subtitleUrl);
        })
        .catch(() => {});
    }
  }

  if (np.now_playing_url && np.now_playing_url !== currentVideoUrl) {
    currentVideoUrl = np.now_playing_url;
    // If a stems_ready socket event arrived before this now_playing push
    // (tight prewarm race), applyPendingStems bailed on stream_uid mismatch
    // and kept the data pending. Drop it now only if it's for a different
    // song; otherwise let it apply once the track pipe is set up below.
    if (pendingStemsData
        && pendingStemsData.stream_uid !== extractStreamUid(currentVideoUrl)) {
      pendingStemsData = null;
    }
    const streamUrl = np.now_playing_url;
    // Tear down any previous HLS instance before we touch the element.
    // hls.destroy() revokes the old MediaSource blob; calling video.load()
    // or leaving the old blob on video.src after that would trigger
    // ERR_FILE_NOT_FOUND on the revoked URL.
    if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }

    const isHls = streamUrl.endsWith('.m3u8');
    const useNativeHLS = isHls && video.canPlayType('application/vnd.apple.mpegurl') &&
      !isChrome && !isEdge && !isMobileSafari;

    if (isHls && !useNativeHLS) {
      // hls.js owns the media element: creates a MediaSource, sets
      // video.src to its blob, feeds segments in. We must NOT call
      // video.load() here — it would close the MediaSource.
      $("#video-source").attr("src", "");
      hlsInstance = new Hls({ startPosition: 0 });
      hlsInstance.on(Hls.Events.ERROR, (event, data) => {
        console.warn("hls.js error:", data.type, data.details, "fatal:", data.fatal, data);
      });
      hlsInstance.loadSource(streamUrl);
      hlsInstance.attachMedia(video);
    } else {
      $("#video-source").attr("src", streamUrl);
      if (useNativeHLS) video.src = streamUrl;
      video.load();
    }

    // Video starts with its original audio. Three attachment modes:
    //   - Single-track pipe (direct-mp4 + audio transforms): mute video,
    //     play audio with AV sync offset applied client-side.
    //   - Stems ready: crossfade video audio to stems.
    //   - Neither: keep native video audio; stems may arrive later via
    //     the `stems_ready` event which triggers the crossfade.
    teardownStemAudio();
    if (np.now_playing_audio_track_url) {
      // Audio is routed through the pipe; video is muted. The single
      // volume slider controls the track gain (see 'volume' socket
      // handler), so seed the gain from np.volume to honor the current
      // slider position immediately.
      const initialTrackGain = typeof np.volume === "number" ? np.volume : 1.0;
      setupAudioTracks(
        [{ url: np.now_playing_audio_track_url, gain: initialTrackGain, label: "track" }],
        video,
        np.now_playing_avsync_offset_ms || 0,
      );
      video.muted = true;
      video.volume = 0;
    } else if (np.vocals_url && np.instrumental_url) {
      setupStemAudio(np, video);
      video.muted = true;
      video.volume = 0;
    } else {
      video.muted = false;
    }

    // Retry any stems_ready that raced ahead of this now_playing update.
    // Must run AFTER the track-pipe branch above so applyPendingStems sees
    // labels=["track"] and crossfades cleanly.
    if (pendingStemsData) ensureAudioContextRunning().then(applyPendingStems);

    if (volume !== np.volume) {
      volume = np.volume;
      video.volume = volume;
    }

    const duration = $("#duration");
    if (np.now_playing_duration) {
      duration.text(`/${formatTime(np.now_playing_duration)}`);
      duration.show();
    } else {
      duration.hide();
    }

    $("#video-container").show();

    video.play().catch(err => {
      console.error('Play failed:', err);
      // Retry once if it was an autoplay block
      setTimeout(() => video.play(), 1000);
    });

    if (np.now_playing_position && isMediaPlaying(video)) {
      if (Math.abs(video.currentTime - np.now_playing_position) > 2) {
        console.log("Syncing to server position:", np.now_playing_position);
        video.currentTime = np.now_playing_position;
      }
    }

    setTimeout(() => {
      if (!isMediaPlaying(video) && !video.paused) {
        endSong("failed to start");
      }
    }, playbackStartTimeout);
  }
}

async function loadNowPlaying() {
  const data = await $.get("/now_playing");
  handleNowPlayingUpdate(JSON.parse(data));
}

const setupOverlayMenus = () => {
  if (PikaraokeConfig.hideOverlay) {
    $('#bottom-container').hide();
    $('#top-container').hide();
  }
  $("#menu a").fadeOut(); // start hidden
  const triggerInactivity = () => {
    mouseTimer = null;
    document.body.style.cursor = 'none';
    cursorVisible = false;
    $("#menu a").fadeOut();
    if (PikaraokeConfig.showSplashClock) {
      setTimeout(() => {
        if (!cursorVisible) $("#clock").fadeIn();
      }, 1000);
    }
    menuButtonVisible = false;
  };

  document.onmousemove = function () {
    if (mouseTimer) window.clearTimeout(mouseTimer);
    if (!cursorVisible) {
      document.body.style.cursor = 'default';
      cursorVisible = true;
    }
    if (!menuButtonVisible) {
      $("#menu a").fadeIn();
      $("#clock").hide();
      menuButtonVisible = true;
    }
    mouseTimer = window.setTimeout(triggerInactivity, 5000);
  };

  // Set initial state to hidden
  triggerInactivity();
  $('#menu a').click(function () {
    if (showMenu) {
      $('#menu-container').hide();
      $('#menu-container iframe').attr('src', '');
      showMenu = false;
    } else {
      setPilotName();
      $("#menu-container").show();
      $("#menu-container iframe").attr("src", "/");
      showMenu = true;
    }
  });
  $('#menu-background').click(function () {
    if (showMenu) {
      $(".navbar-burger").click();
    }
  });
}

const setupVideoPlayer = () => {
  $('#video-container').hide();
  const video = getVideoPlayer();
  video.addEventListener("play", () => {
    $("#video-container").show();
    if (isMaster) {
      setTimeout(() => { socket.emit("start_song") }, 1200);
    }
  });

  // Master reports playback position to server
  setInterval(() => {
    if (isMaster && isMediaPlaying(video)) {
      socket.emit("playback_position", video.currentTime);
    }
  }, 1000);

  video.addEventListener("ended", () => { endSong("complete", true); });
  video.addEventListener("timeupdate", (e) => { $("#current").text(formatTime(video.currentTime)); });
  $("#video source")[0].addEventListener("error", (e) => {
    if (isMediaPlaying(video)) {
      endSong("error while playing");
    }
  });
  window.addEventListener(
    'beforeunload',
    function (event) {
      if (isMediaPlaying(video)) {
        endSong("splash screen closed");
      }
    },
    true
  );
}

const setupBackgroundMusicPlayer = () => {
  $.get("/bg_playlist", function (data) {
    if (data) bg_playlist = data;
  });
  const bgMusic = getBackgroundMusicPlayer();
  bgMusic.addEventListener("ended", async () => {
    bgMusic.setAttribute('src', getNextBgMusicSong());
    await bgMusic.load();
    await bgMusic.play();
  });
}

const handleUnsupportedBrowser = () => {
  if (!isSupportedBrowser) {
    let modalContents = document.getElementById("permissions-modal-content");
    let warningMessage = document.createElement("p");
    warningMessage.classList.add("notification", "is-warning");
    warningMessage.innerHTML =
      PikaraokeConfig.translations.unsupportedBrowser;
    modalContents.prepend(warningMessage);
  }
}

const startClock = () => {
  if (clockIntervalId) return;
  const update = () => {
    const el = document.getElementById('clock');
    if (el) el.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
  };
  update();
  clockIntervalId = setInterval(update, 1000);
}

const stopClock = () => {
  if (!clockIntervalId) return;
  clearInterval(clockIntervalId);
  clockIntervalId = null;
}

const toggleBGMedia = (configKey, playFn, disabled) => {
  PikaraokeConfig[configKey] = disabled;
  disabled ? playFn(false) : shouldBackgroundMediaPlay() && playFn(true);
};

const PREFERENCE_EFFECTS = {
  disable_bg_video:    (v) => toggleBGMedia("disableBgVideo", playBGVideo, v),
  disable_bg_music:    (v) => toggleBGMedia("disableBgMusic", playBGMusic, v),
  disable_score:       (v) => { PikaraokeConfig.disableScore = v; },
  show_splash_clock:   (v) => {
    PikaraokeConfig.showSplashClock = v;
    v ? startClock() : (stopClock(), $("#clock").hide());
  },
  hide_overlay:        (v) => {
    PikaraokeConfig.hideOverlay = v;
    $("#bottom-container, #top-container").toggle(!v);
  },
  hide_url:            (v) => { $("#qr-code, #screensaver-qr").toggle(!v); },
  bg_music_volume:     (v) => {
    PikaraokeConfig.bgMusicVolume = v;
    const player = getBackgroundMusicPlayer();
    if (isMediaPlaying(player)) $(player).animate({ volume: v }, 1000);
  },
  screensaver_timeout: (v) => {
    screensaverTimeoutSeconds = v;
    PikaraokeConfig.screensaverTimeout = v;
  },
  subtitle_offset: (v) => {
    const offset = Number(v) || 0;
    PikaraokeConfig.subtitleOffset = offset;
    if (octopusInstance) octopusInstance.timeOffset = offset;
  },
};

const parsePreferenceValue = (value) => {
  if (typeof value !== "string") return value;
  if (value === "True") return true;
  if (value === "False") return false;
  const num = Number(value);
  return !isNaN(num) && value.trim() !== "" ? num : value;
};

const applyPreferenceUpdate = (data) => {
  const effect = PREFERENCE_EFFECTS[data.key];
  if (effect) effect(parsePreferenceValue(data.value));
};

const applyPreferencesReset = (defaults) => {
  Object.entries(defaults).forEach(([key, value]) => applyPreferenceUpdate({ key, value }));
};

const setupSocketEvents = () => {
  socket.on('connect', () => {
    console.log('Socket connected');
    socket.emit("register_splash");
  });
  socket.on('splash_role', (role) => {
    isMaster = (role === "master");
    console.log("Splash role assigned:", role, isMaster ? "(Master active)" : "(Slave active - read-only)");
  });
  socket.on('connect_error', (error) => {
    console.error('Connection error:', error);
    flashNotification(PikaraokeConfig.translations.socketConnectionLost, "is-danger");
  });
  socket.on('disconnect', (reason) => {
    console.warn('Socket disconnected:', reason);
    flashNotification(PikaraokeConfig.translations.socketConnectionLost, "is-danger");
  });
  socket.on('pause', () => {
    const video = getVideoPlayer();
    if (video.paused) return;
    if (audioNodes) {
      // Audio tracks carry the real audio; fade all, then pause (video.pause
      // fires the listener that pauses the Audio elements).
      const zeros = Object.fromEntries(audioNodes.labels.map((l) => [l, 0]));
      fadeStems(zeros, 1000);
      setTimeout(() => video.pause(), 1000);
    } else {
      const currVolume = video.volume;
      $(video).animate({ volume: 0 }, 1000, () => {
        video.pause();
        video.volume = currVolume;
      });
    }
  });
  socket.on('play', () => {
    const video = getVideoPlayer();
    if (!video.paused) return;
    if (audioNodes && stemAudioCtx) {
      // Silence gains, start playback (video.play fires playAll on tracks),
      // then ramp gains back to the user's target values.
      const now = stemAudioCtx.currentTime;
      for (const label of audioNodes.labels) {
        const g = audioNodes.tracks[label].gain.gain;
        g.cancelScheduledValues(now);
        g.setValueAtTime(0, now);
      }
      video.play();
      fadeStems(audioTargets(), 1000);
    } else {
      const currVolume = video.volume;
      video.play();
      video.volume = 0;
      $(video).animate({ volume: currVolume }, 1000);
    }
  });
  socket.on('skip', (reason) => {
    const video = getVideoPlayer();
    if (isMediaPlaying(video)) {
      if (audioNodes) {
        const zeros = Object.fromEntries(audioNodes.labels.map((l) => [l, 0]));
        fadeStems(zeros, 1000);
        setTimeout(() => { video.pause(); hideVideo(); }, 1000);
      } else {
        const currVolume = video.volume;
        $(video).animate({ volume: 0 }, 1000, () => {
          video.pause();
          video.volume = currVolume;
          hideVideo();
        });
      }
    } else {
      video.pause();
      hideVideo();
    }
  });
  socket.on('volume', (val) => {
    const video = getVideoPlayer();
    // When audio is routed through the warmup "track" pipe (direct-mp4
    // transforms or split-download m4a sibling), video.muted is true and
    // the audible level lives on the track gain node, not video.volume.
    // Reach the right sink so the single slider works pre-stems.
    const usingTrackPipe = audioNodes && audioNodes.labels && audioNodes.labels.length === 1 && audioNodes.labels[0] === "track";
    const trackGain = usingTrackPipe ? audioNodes.tracks.track.gain.gain : null;
    const current = trackGain ? trackGain.value : video.volume;
    let next;
    if (val === "up") {
      next = Math.min(1, current + 0.1);
    } else if (val === "down") {
      next = Math.max(0, current - 0.1);
    } else {
      next = val;
    }
    if (trackGain) {
      trackGain.value = next;
    } else {
      video.volume = next;
    }
  });
  socket.on('restart', () => {
    const video = getVideoPlayer();
    video.currentTime = 0;
    if (video.paused) video.play();
  });
  // Source-picker ``off`` toggle: hide/show the SubtitlesOctopus canvas
  // without disposing the worker so re-enable is instant. The cold-load
  // path in applyNowPlaying also handles ``subtitle_disabled`` for page
  // reloads (TD1).
  socket.on('subtitle_off', () => {
    const video = getVideoPlayer();
    const c = document.getElementById('subtitles-canvas')
      || (video && video.parentNode && video.parentNode.querySelector('canvas'));
    if (c) c.style.display = 'none';
  });
  socket.on('subtitle_on', () => {
    const video = getVideoPlayer();
    const c = document.getElementById('subtitles-canvas')
      || (video && video.parentNode && video.parentNode.querySelector('canvas'));
    if (c) c.style.display = '';
  });
  socket.on('seek', (position) => {
    if (!isFinite(position)) return;
    const video = getVideoPlayer();
    if (video.readyState === 0) return;
    const duration = isFinite(video.duration) && video.duration > 0 ? video.duration : position;
    video.currentTime = Math.max(0, Math.min(duration, position));
    // Stem audio elements follow via the 'seeking'/'seeked' listeners in setupStemAudio.
  });
  socket.on("notification", (data) => {
    const notification = data.split("::");
    const message = notification[0];
    const categoryClass = notification.length > 1 ? notification[1] : "is-primary";
    flashNotification(message, categoryClass);
    if (isMaster) {
      socket.emit("clear_notification");
    }
  });
  socket.on("now_playing", handleNowPlayingUpdate);
  socket.on("song_warning", (data) => {
    if (!data || !data.message || !data.song) return;
    const added = bufferSongWarning(data.song, data.message, data.detail || "");
    if (!added) return;
    // If no song is currently tracked (nothing playing, no prior warnings
    // shown), promote this song so warnings emitted during download /
    // demucs / alignment are surfaced before playback starts. Otherwise
    // keep per-song targeting so queued-song warnings don't steal focus
    // from the currently-playing song.
    if (!songWarningSongKey) songWarningSongKey = data.song;
    if (data.song === songWarningSongKey) {
      renderSongWarnings();
      flashNotification(data.message, "is-warning");
    }
  });
  socket.on("song_warnings_dismissed", (data) => {
    if (!data || !data.song) return;
    songWarningsBySong.delete(data.song);
    if (data.song === songWarningSongKey) {
      // Retarget to the currently-playing song so any warnings that
      // landed for the new song while the old song's warnings were
      // held on screen immediately surface.
      const currentPlaying =
        (nowPlaying && nowPlaying.now_playing_basename) || null;
      songWarningSongKey = currentPlaying;
      renderSongWarnings();
    }
  });
  // Live Demucs fires this once the first segment for both stems is on
  // disk. Video has been playing with its original audio; crossfade to the
  // stems so the user gets the karaoke mix (vocals ducked per slider).
  // Prewarm (US-7) emits a lighter ``stems_ready`` carrying only
  // ``song_basename``/``cache_key`` — no stream URLs — so skip the
  // audio-routing path there and leave the real play-time event to do
  // the crossfade.
  socket.on("stems_ready", (data) => {
    if (!data || !data.stream_uid) return;
    pendingStemsData = data;
    ensureAudioContextRunning().then(applyPendingStems);
  });
  // Lightweight live stem volume updates during slider drag. Applies the
  // new gain directly without triggering the full now_playing refresh.
  socket.on("stem_volume", (data) => {
    if (!audioNodes) return;
    if (audioNodes.tracks.vocals && typeof data.vocal_volume === "number") {
      audioNodes.tracks.vocals.gain.gain.value = data.vocal_volume;
    }
    if (audioNodes.tracks.instrumental && typeof data.instrumental_volume === "number") {
      audioNodes.tracks.instrumental.gain.gain.value = data.instrumental_volume;
    }
  });
  socket.on("preferences_update", applyPreferenceUpdate);
  socket.on("preferences_reset", applyPreferencesReset);
  socket.on("score_phrases_update", (phrases) => { scoreReviews = phrases; });

  socket.on("playback_position", (position) => {
    if (!isMaster) {
      const video = getVideoPlayer();
      if (isMediaPlaying(video)) {
        if (Math.abs(video.currentTime - position) > 2) {
          console.log("Slave drifting, syncing position to:", position);
          video.currentTime = position;
        }
      }
    }
  });
}

const handleSocketRecovery = () => {
  // A socket may disconnect if the tab is backgrounded for a while
  // Reconnect and configure event listeners when tab becomes visible again
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === 'visible') {
      autoplayConfirmed && loadNowPlaying();
      if (!socket.connected) {
        socket = io();
        setupSocketEvents();
      }
    }
  });
}

const setupUIScaling = () => {
  const urlParams = new URLSearchParams(window.location.search);
  const rawScale = urlParams.get('scale');
  if (!rawScale) return;
  uiScale = parseFloat(rawScale) || 1;

  const scaleTargets = [
    { selector: '#logo-container img.logo', origin: null },
    { selector: '#top-container', origin: 'top right' },
    { selector: '#ap-container', origin: 'top left' },
    { selector: '#qr-code', origin: 'bottom left' },
    { selector: '#up-next', origin: 'bottom right' },
    { selector: '#dvd', origin: null },
    { selector: '#your-score-text', origin: null },
    { selector: '#score-number-text', origin: null },
    { selector: '#score-review-text', origin: null },
    { selector: '#splash-notification', origin: 'top left' },
    { selector: '#clock', origin: 'top left' },
  ];

  scaleTargets.forEach(({ selector, origin }) => {
    const el = document.querySelector(selector);
    if (el) {
      el.style.transform = `scale(${uiScale})`;
      if (origin) el.style.transformOrigin = origin;
    }
  });
}

// Document ready procedures

$(function () {
  // Setup various features and listeners. Nothing here opens a socket, fetches
  // a playlist, or starts playback - those live in handleConfirmation().
  setupUIScaling();
  if (PikaraokeConfig.showSplashClock) startClock();
  setupScreensaver();
  setupOverlayMenus();
  setupVideoPlayer();

  // Handle browser compatibility
  handleUnsupportedBrowser();
  testAutoplayCapability();

  // Per-song warning tooltip: click toggles; hover/focus open; leaving the
  // hover region with no keyboard focus closes. Clicks inside the tooltip
  // don't close (so the copy button stays usable). Keyboard: Enter/Space
  // toggles, Escape closes and returns focus to the icon.
  let songWarningHoverTimer = null;
  const openTooltipOnHover = () => {
    if (songWarningHoverTimer) { clearTimeout(songWarningHoverTimer); songWarningHoverTimer = null; }
    if (!$("#song-warning").is(":visible")) return;
    setTooltipOpen(true);
  };
  const scheduleCloseTooltipOnLeave = () => {
    if (songWarningHoverTimer) clearTimeout(songWarningHoverTimer);
    songWarningHoverTimer = setTimeout(() => {
      // Don't close if focus moved inside the icon / tooltip.
      const active = document.activeElement;
      if (active && $(active).closest("#song-warning").length) return;
      setTooltipOpen(false);
    }, 150);
  };
  $("#song-warning")
    .on("click", (e) => {
      if ($(e.target).closest("#song-warning-tooltip").length) return;
      const isOpen = $("#song-warning-tooltip").is(":visible");
      setTooltipOpen(!isOpen);
    })
    .on("mouseenter", openTooltipOnHover)
    .on("mouseleave", scheduleCloseTooltipOnLeave)
    .on("focusin", openTooltipOnHover)
    .on("focusout", scheduleCloseTooltipOnLeave)
    .on("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        if ($(e.target).closest("#song-warning-tooltip").length) return;
        e.preventDefault();
        const isOpen = $("#song-warning-tooltip").is(":visible");
        setTooltipOpen(!isOpen);
      } else if (e.key === "Escape") {
        setTooltipOpen(false);
        $("#song-warning").trigger("focus");
      }
    });
  $("#song-warning-copy").on("click", (e) => {
    e.stopPropagation();
    copySongWarnings();
  });
  // Dismiss the tooltip on outside click.
  $(document).on("click", (e) => {
    if (!$(e.target).closest("#song-warning").length) {
      setTooltipOpen(false);
    }
  });
});
