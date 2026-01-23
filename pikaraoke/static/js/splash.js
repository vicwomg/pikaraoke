// Global variables
var socket = io();
var mouseTimer = null,
  cursorVisible = true;
var nowPlaying = {};
var octopusInstance = null;
var showMenu = false;
var menuButtonVisible = false;
var autoplayConfirmed = false;
var volume = 0.85;
var playbackStartTimeout = 10000;
var isScoreShown = false;
var hasBgVideo = PikaraokeConfig.hasBgVideo;
var currentVideoUrl = null;
var hlsInstance = null;
var idleTime = 0;
var screensaverTimeoutSeconds = PikaraokeConfig.screensaverTimeout;
var bg_playlist = []
const scoreReviews = PikaraokeConfig.scorePhrases;

// Browser detection
const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
const isMobileSafari = isSafari && (/iPhone|iPad|iPod/i.test(navigator.userAgent) || navigator.maxTouchPoints > 1);
const isChrome = /chrome/i.test(navigator.userAgent) && !/edg/i.test(navigator.userAgent);
const isFirefox = /firefox/i.test(navigator.userAgent);
const isEdge = /edg/i.test(navigator.userAgent);
const isSupportedBrowser = isSafari || isChrome || isFirefox || isEdge;

// Support functions below

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

const handleConfirmation = async () => {
  $('#permissions-modal').removeClass('is-active');
  autoplayConfirmed = true;
  if (hasBgVideo) playBGVideo(true);
  playBGMusic(true);
}

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
  const video = getVideoPlayer();
  video.pause();
  $("#video-source").attr("src", "");
  video.load();
  hideVideo();
  socket.emit("end_song", reason);
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
  let bgMusicVolume = PikaraokeConfig.bgMusicVolume;
  if (!PikaraokeConfig.disableBgMusic) {
    if (!autoplayConfirmed) return;
    let audio = getBackgroundMusicPlayer();
    if (bg_playlist.length == 0) return;
    if (!audio.getAttribute('src')) audio.setAttribute('src', getNextBgMusicSong());

    if (play == true) {
      if (isMediaPlaying(audio)) return;
      audio.volume = 0;
      if (audio.readyState <= 2) await audio.load();
      await audio.play().catch(e => console.log("Autoplay blocked (music)"));
      $(audio).animate({ volume: bgMusicVolume }, 2000);
    } else {
      $(audio).animate({ volume: 0 }, 2000, () => audio.pause());
    }
  }
}

const playBGVideo = async (play) => {
  if (!PikaraokeConfig.disableBgVideo) {
    if (!autoplayConfirmed) return;
    let bgVideo = getBackgroundVideoPlayer();
    const bgVideoContainer = $('#bg-video-container');
    if (play == true) {
      if (isMediaPlaying(bgVideo)) return;
      $("#bg-video").attr("src", "/stream/bg_video");
      if (bgVideo.readyState <= 2) await bgVideo.load();
      bgVideo.play().catch(e => console.log("Autoplay blocked (video)"));
      bgVideoContainer.fadeIn(2000);
    } else {
      if (isMediaPlaying(bgVideo)) {
        bgVideo.pause();
        bgVideoContainer.fadeOut(2000);
      }
    }
  }
}

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
        if (screensaver.style.visibility == 'hidden') {
          screensaver.style.visibility = 'visible';
          playBGVideo(false);
          startScreensaver(); // depends on upstream screensaver.js import
        }
        if (idleTime > screensaverTimeoutSeconds + 36000) idleTime = screensaverTimeoutSeconds;
      }
      else {
        if (screensaver.style.visibility == 'visible') {
          screensaver.style.visibility = 'hidden';
          stopScreensaver(); // depends on upstream screensaver.js import
          if (!nowPlaying.up_next && !isMediaPlaying(video)) playBGVideo(true);
        }
      }
      idleTime++;
    }, 1000)
  }
}

const handleNowPlayingUpdate = (np) => {
  nowPlaying = np;
  if (np.now_playing) {

    // Handle updating now playing HTML
    var nowPlayingHtml = `<span>${np.now_playing}</span> `;
    if (np.now_playing_transpose != 0) {
      nowPlayingHtml += `<span class='is-size-6 has-text-success'><b>Key</b>: ${getSemitonesLabel(np.now_playing_transpose)} </span>`
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

  // Stop bg music and video
  if (np.now_playing || np.up_next) {
    idleTime = 0;
    playBGMusic(false);
    playBGVideo(false);
  } else {
    setTimeout(() => {
      playBGMusic(true);
      playBGVideo(true)
    }, 2000);
  }

  const video = getVideoPlayer();

  // Setup ASS subtitle file if found
  const subtitleUrl = np.now_playing_subtitle_url;
  if (octopusInstance) {
    octopusInstance.dispose();
    octopusInstance = null;
  }
  if (subtitleUrl && video) {
    var options = {
      video: video,
      subUrl: subtitleUrl,
      fonts: ["/static/fonts/Arial.ttf"],
      debug: true,
      workerUrl: "/static/js/subtitles-octopus-worker.js"
    };
    try {
      octopusInstance = new SubtitlesOctopus(options);
    } catch (e) { console.error(e); }
  }

  if (np.now_playing_url && np.now_playing_url !== currentVideoUrl) {
    currentVideoUrl = np.now_playing_url;
    const streamUrl = np.now_playing_url;
    $("#video-source").attr("src", "");
    video.load();
    $("#video-source").attr("src", streamUrl);

    if (streamUrl.endsWith('.m3u8')) {
      const useNativeHLS = video.canPlayType('application/vnd.apple.mpegurl') && !isChrome && !isEdge && !isMobileSafari;
      if (useNativeHLS) {
        video.src = streamUrl;
      } else {
        if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
        hlsInstance = new Hls({ startPosition: 0 });
        hlsInstance.loadSource(streamUrl);
        hlsInstance.attachMedia(video);
      }
    }

    video.load();
    if (volume != np.volume) {
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

    setTimeout(() => {
      if (!isMediaPlaying(video) && !video.paused) {
        endSong("failed to start");
      }
    }, playbackStartTimeout);
  }
}

function loadNowPlaying() {
  $.get("/now_playing", function (data) {
    handleNowPlayingUpdate(JSON.parse(data));
  });
}

const setupOverlayMenus = () => {
  if (PikaraokeConfig.hideOverlay) {
    $('#bottom-container').hide();
    $('#top-container').hide();
  }
  document.onmousemove = function () {
    if (mouseTimer) window.clearTimeout(mouseTimer);
    if (!cursorVisible) {
      document.body.style.cursor = 'default';
      cursorVisible = true;
    }
    if (!menuButtonVisible) {
      $("#menu a").fadeIn();
      menuButtonVisible = true;
    }
    mouseTimer = window.setTimeout(() => {
      mouseTimer = null;
      document.body.style.cursor = 'none';
      cursorVisible = false;
      $("#menu a").fadeOut();
      menuButtonVisible = false;
    }, 5000);
  };
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
    setTimeout(() => { socket.emit("start_song") }, 1200);
  });
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
    // await setTimeout(() => { }, 2500)
    await bgMusic.play();
  });
}

const handleUnsupportedBrowser = () => {
  if (!isSupportedBrowser) {
    let modalContents = document.getElementById("permissions-modal-content");
    let warningMessage = document.createElement("p");
    warningMessage.classList.add("notification", "is-warning");
    warningMessage.innerHTML =
      "This browser is not fully supported. You may experience streaming issues. Please use Chrome/Safari/Firefox for best results.";
    modalContents.prepend(warningMessage);
  }
}

const setupSocketEvents = () => {
  socket.on('connect', () => { console.log('Socket connected'); });
  socket.on('connect_error', (error) => {
    console.error('Connection error:', error);
    flashNotification("Connection lost. Is the server still running?", "is-danger");
  });
  socket.on('disconnect', (reason) => {
    console.warn('Socket disconnected:', reason);
    flashNotification("Connection lost. Is the server still running?", "is-danger");
  });
  socket.on('pause', () => {
    const video = getVideoPlayer();
    const currVolume = video.volume;
    if (!video.paused) {
      $(video).animate({ volume: 0 }, 1000, () => {
        video.pause();
        video.volume = currVolume;
      });
    }
  });
  socket.on('play', () => {
    const video = getVideoPlayer();
    const currVolume = video.volume;
    if (video.paused) {
      video.play();
      video.volume = 0;
      $(video).animate({ volume: currVolume }, 1000);
    }
  });
  socket.on('skip', (reason) => {
    const video = getVideoPlayer();
    const currVolume = video.volume;
    if (isMediaPlaying(video)) {
      $(video).animate({ volume: 0 }, 1000, () => {
        video.pause();
        video.volume = currVolume;
        hideVideo();
      });
    } else {
      video.pause();
      hideVideo();
    }
  });
  socket.on('volume', (val) => {
    const video = getVideoPlayer();
    if (val == "up") {
      video.volume = Math.min(1, video.volume + 0.1);
    } else if (val == "down") {
      video.volume = Math.max(0, video.volume - 0.1);
    }
    else {
      video.volume = val;
    }
  });
  socket.on('restart', () => {
    const video = getVideoPlayer();
    video.currentTime = 0;
    if (video.paused) video.play();
  });
  socket.on("notification", (data) => {
    const notification = data.split("::");
    const message = notification[0];
    const categoryClass = notification.length > 1 ? notification[1] : "is-primary";
    flashNotification(message, categoryClass);
    socket.emit("clear_notification");
  });
  socket.on("now_playing", handleNowPlayingUpdate);
}

const handleSocketRecovery = () => {
  // A socket may disconnect if the tab is backgrounded for a while
  // Reconnect and configure event listeners when tab becomes visible again
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === 'visible') {
      loadNowPlaying();
      if (!socket.connected) {
        socket = io();
        setupSocketEvents();
      }
    }
  });
}

// Document ready procedures

$(function () {
  // Setup various features and listeners
  setupScreensaver();
  setupOverlayMenus();
  setupVideoPlayer();
  setupBackgroundMusicPlayer();

  // Setup sockets and recovery
  setupSocketEvents();
  handleSocketRecovery();

  // Handle browser and autoplay test
  handleUnsupportedBrowser();
  testAutoplayCapability();

  // Load now playing data for first time
  loadNowPlaying();
});
