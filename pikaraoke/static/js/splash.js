// Socket is initialized in handleConfirmation() so nothing connects before the
// user clicks Start (or testAutoplayCapability auto-confirms).
let socket = null;
let mouseTimer = null;
let cursorVisible = false;
let nowPlaying = {};
let octopusInstance = null;
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

const testAutoplayCapability = async () => {
  // Test if autoplay with audio is allowed using a real video file
  try {
    const testVideo = document.createElement('video');
    testVideo.playsInline = true;
    testVideo.muted = true;  // Start muted (always allowed)
    testVideo.src = "/static/video/test_autoplay.mp4";

    // Wait for video to be ready
    await new Promise((resolve, reject) => {
      testVideo.onloadeddata = resolve;
      testVideo.onerror = reject;
    });

    await testVideo.play();
    // Now try to unmute - this is the real test
    testVideo.muted = false;
    testVideo.volume = 0.01;

    // Brief delay to let browser enforce policy
    await new Promise(resolve => setTimeout(resolve, 500));

    // Check if browser paused or muted the video
    if (testVideo.muted || testVideo.paused) {
      testVideo.pause();
      $('#permissions-modal').addClass('is-active');
    } else {
      testVideo.pause();
      handleConfirmation();
    }
  } catch (e) {
    // Autoplay blocked
    console.log("Autoplay error thrown", e);
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

const flashNotification = (message, categoryClass) => {
  const sn = $("#splash-notification");
  if (sn.html()) return;
  sn.html(message);
  sn.addClass(categoryClass);
  sn.fadeIn();
  setTimeout(() => {
    sn.fadeOut();
    setTimeout(() => {
      sn.html("");
      sn.removeClass(categoryClass);
    }, 450);
  }, 3000);
}

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

// Sets up stems and crossfades from video audio. Bails if the context
// isn't running so the video keeps its audio instead of going silent —
// stems are re-tried when a gesture resumes the context.
const applyPendingStems = () => {
  if (!pendingStemsData) return;
  const data = pendingStemsData;
  const video = getVideoPlayer();
  const currentUid = currentVideoUrl ? extractStreamUid(currentVideoUrl) : null;
  if (data.stream_uid !== currentUid) {
    pendingStemsData = null;  // song changed
    return;
  }
  if (audioNodes) {
    pendingStemsData = null;  // already set up (cache hit path)
    return;
  }
  if (!stemAudioCtx || stemAudioCtx.state !== "running") return;
  pendingStemsData = null;

  const np = {
    ...nowPlaying,
    vocals_url: data.vocals_url,
    instrumental_url: data.instrumental_url,
  };
  setupStemAudio(np, video);
  if (!audioNodes) return;
  for (const label of audioNodes.labels) {
    try { audioNodes.tracks[label].el.currentTime = video.currentTime; } catch (e) {}
    audioNodes.tracks[label].el.play().catch(() => {});
  }
  // 300ms crossfade: video audio down to 0 as stem gains ramp up.
  const fadeMs = 300;
  $(video).animate({ volume: 0 }, fadeMs, () => {
    video.muted = true;
  });
  fadeStems(stemTargets(), fadeMs);
};

// Extracts the stream_uid from a /stream/<uid>.<ext> URL so stems_ready
// events can be matched against the song that's actually playing.
const extractStreamUid = (url) => {
  if (!url) return null;
  const match = url.match(/\/stream\/([^/.]+)/);
  return match ? match[1] : null;
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
  const playAll = () => {
    if (!audioNodes) return;
    syncAll();
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
  const seekedHandler = () => {
    if (!audioNodes) return;
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

  const handlers = {
    play: playAll,
    pause: pauseAll,
    seeking: pauseAll,
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
  if (np.now_playing) {

    // Handle updating now playing HTML
    let nowPlayingHtml = `<span>${np.now_playing}</span> `;
    if (np.now_playing_transpose !== 0) {
      nowPlayingHtml += `<span class='is-size-6 has-text-success'><b>Key</b>: ${getSemitonesLabel(np.now_playing_transpose)} </span>`;
    }
    $("#now-playing-song").html(nowPlayingHtml);
    $("#now-playing-singer").html(np.now_playing_user);
    $("#now-playing").fadeIn();
  } else {
    $("#now-playing").fadeOut();
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

  // Setup ASS subtitle file if found
  const subtitleUrl = np.now_playing_subtitle_url;
  if (octopusInstance) {
    octopusInstance.dispose();
    octopusInstance = null;
  }
  if (subtitleUrl && video) {
    const options = {
      video: video,
      subUrl: subtitleUrl,
      fonts: ["/static/fonts/Arial.ttf", "/static/fonts/DroidSansFallback.ttf"],
      debug: true,
      workerUrl: "/static/js/subtitles-octopus-worker.js"
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

  if (np.now_playing_url && np.now_playing_url !== currentVideoUrl) {
    currentVideoUrl = np.now_playing_url;
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
      setupAudioTracks(
        [{ url: np.now_playing_audio_track_url, gain: 1.0, label: "track" }],
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
      setUserCookie();
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
    if (val === "up") {
      video.volume = Math.min(1, video.volume + 0.1);
    } else if (val === "down") {
      video.volume = Math.max(0, video.volume - 0.1);
    } else {
      video.volume = val;
    }
  });
  socket.on('restart', () => {
    const video = getVideoPlayer();
    video.currentTime = 0;
    if (video.paused) video.play();
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
  // Live Demucs fires this once the first segment for both stems is on
  // disk. Video has been playing with its original audio; crossfade to the
  // stems so the user gets the karaoke mix (vocals ducked per slider).
  socket.on("stems_ready", (data) => {
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
});
