// Test harness for splash.js.
//
// splash.js is a plain top-level <script>: it reads its configuration from
// template-injected globals, creates its socket at load, and reaches into
// sibling static scripts for a handful of functions. This file stands up
// those globals as stubs, installs a real jQuery, and loads the real
// splash.js so the tests exercise the served file, not a copy.
//
// The stubs are fakes, not mocks: they hold state the way the real
// objects do, so the logic under test interacts with them through its
// usual property reads and writes.

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import $ from "jquery";
import { beforeAll, beforeEach, vi } from "vitest";

const require = createRequire(import.meta.url);

// The served file, resolved relative to this file.
const SPLASH_JS = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "pikaraoke",
  "static",
  "js",
  "splash.js",
);

// The media properties splash.js reads and writes on the video and audio
// elements.
const MEDIA_PROPS = [
  "currentTime",
  "paused",
  "ended",
  "readyState",
  "playbackRate",
  "volume",
  "src",
];

// One socket stub per test file: splash.js creates its connection at load,
// and the tests drive it through the same object.
export const socketStub = makeSocketStub();

// Call records for the stubs the tests assert on.
export const startScoreCalls = [];
export const screensaverCalls = { start: 0, stop: 0 };
export const octopusInstances = [];

// The module exports of the loaded splash.js. Assigned in beforeAll; the
// export below is a live binding, so the tests see the loaded module.
let splashExports = { state: {} };
export { splashExports as splash };

export const state = () => splashExports.state;

function makeSocketStub() {
  return {
    connected: true,
    emitted: [],
    handlers: {},
    emit(event, ...args) {
      this.emitted.push([event, ...args]);
    },
    on(event, handler) {
      (this.handlers[event] ||= []).push(handler);
    },
    // Fire a registered handler the way the server would emit.
    fire(event, ...args) {
      for (const handler of this.handlers[event] ?? []) handler(...args);
    },
  };
}

function makeMediaFake() {
  // A fake media element: it holds the state splash.js reads and records
  // the calls it makes, the way a real element would.
  return {
    currentTime: 0,
    paused: true,
    ended: false,
    readyState: 0,
    playbackRate: 1,
    volume: 0.85,
    src: "",
    playCalls: 0,
    loadCalls: 0,
    play() {
      this.playCalls += 1;
      this.paused = false;
      return Promise.resolve();
    },
    pause() {
      this.paused = true;
    },
    load() {
      this.loadCalls += 1;
    },
  };
}

// The DOM ids splash.js looks up, mirroring the splash template.
const TEMPLATE = `
  <div id="top-container"></div>
  <div id="bottom-container"></div>
  <div id="video-container">
    <video id="video" playsinline webkit-playsinline>
      <source id="video-source" src="" />
    </video>
  </div>
  <div id="bg-video-container"><video id="bg-video" muted></video></div>
  <div id="now-playing" style="display: none">
    <div id="now-playing-song"></div>
    <div id="now-playing-singer"></div>
  </div>
  <div id="up-next" style="display: none">
    <div id="up-next-song"></div>
    <div id="up-next-singer"></div>
  </div>
  <div id="duration" style="display: none"></div>
  <div id="current">00:00</div>
  <div id="session-name" style="display: none"></div>
  <div id="splash-notification" class="notification" style="display: none"></div>
  <div id="permissions-modal" class="modal"></div>
  <div id="permissions-modal-content"></div>
  <div id="menu"><a role="button" class="navbar-burger"></a></div>
  <div id="menu-container" style="display: none"><iframe></iframe></div>
  <div id="menu-background"></div>
  <div id="clock" style="display: none"></div>
  <div id="logo-container"><div><img class="logo" alt="logo" /></div></div>
  <div id="qr-code"><img class="splash-qr-image" alt="qr" /></div>
  <div class="splash-url"></div>
  <audio id="background-music"></audio>
  <div id="screensaver" style="visibility: hidden"><div id="dvd"></div></div>
  <div id="ap-container"></div>
  <div id="your-score-text"></div>
  <div id="score-number-text"></div>
  <div id="score-review-text"></div>
`;

let currentVideoFake = null;
let currentAudioFake = null;

beforeAll(async () => {
  vi.useFakeTimers();

  // The template-injected configuration objects.
  window.pikaraokeConfig = {
    basePath: "",
    socketioPath: "/socket.io",
    cookiePath: "/",
  };
  globalThis.PikaraokeConfig = {
    bgVideoChoices: ["/static/video/bg_one.mp4", "/static/video/bg_two.mp4"],
    disableScore: false,
    bgMusicVolume: 0.8,
    disableBgMusic: false,
    disableBgVideo: false,
    showSplashClock: false,
    screensaverTimeout: 0,
    splashScale: 1,
    hideOverlay: false,
    hideSessionName: false,
    translations: {
      socketConnectionLost: "Connection lost.",
      unsupportedBrowser: "Unsupported browser.",
    },
    scorePhrasesUrl: "/api/score_phrases",
  };

  // Socket.IO: splash.js calls io({ path: ... }) at load.
  window.io = () => socketStub;

  // hls.js: only the .m3u8 path constructs it; the tests use a stub and
  // inspect the instances through window.__hlsInstances.
  window.__hlsInstances = [];
  window.Hls = class HlsStub {
    constructor(config) {
      this.config = config;
      this.loadedSource = null;
      this.media = null;
      window.__hlsInstances.push(this);
    }
    loadSource(url) {
      this.loadedSource = url;
    }
    attachMedia(video) {
      this.media = video;
    }
    destroy() {}
  };

  // SubtitlesOctopus: the vendored class is out of scope; record instances.
  window.SubtitlesOctopus = class SubtitlesOctopusStub {
    constructor(options) {
      this.options = options;
      this.disposed = false;
      octopusInstances.push(this);
    }
    dispose() {
      this.disposed = true;
    }
  };

  // Helpers defined inline in base.html, not in any static file.
  window.getSemitonesLabel = (value) => `${value > 0 ? "+" : ""}${value}`;

  // Functions from the sibling static scripts: score.js, screensaver.js.
  window.startScore = async (staticPath) => {
    startScoreCalls.push(staticPath);
  };
  window.startScreensaver = () => {
    screensaverCalls.start += 1;
  };
  window.stopScreensaver = () => {
    screensaverCalls.stop += 1;
  };

  // The splash template body, so jQuery lookups resolve.
  document.body.innerHTML = TEMPLATE;

  // jQuery: the real library, since splash.js and the templates use it.
  window.jQuery = $;
  window.$ = $;

  // The splash screen never makes a real request in tests: stand up the
  // one endpoint it fetches at load (the background playlist).
  $.get = (url, success) => {
    if (typeof url === "function") {
      success = url;
    }
    if (typeof success === "function") success([]);
    return { abort() {} };
  };

  // jQuery's animate drives CSS transitions jsdom does not run. Replace it
  // with a stub that applies the observable end state - the display and
  // opacity properties, and the volume on a media element - and runs the
  // completion callback immediately. The code under test is the decision
  // logic around the fade, not the fade itself.
  $.fn.animate = function (props, duration, callback) {
    if (typeof duration === "function") {
      callback = duration;
    }
    this.each(function () {
      const el = this;
      for (const key of Object.keys(props)) {
        let value = props[key];
        if (typeof value === "function") value = value.call(el);
        if (
          value === "show" ||
          value === "hide" ||
          value === "toggle" ||
          key === "display"
        ) {
          if (value === "hide" || value === "none") {
            el.style.display = "none";
          } else if (value === "show" || (key === "display" && value)) {
            el.style.display = "";
          } else if (value === "toggle") {
            el.style.display = el.style.display === "none" ? "" : "none";
          }
          continue;
        }
        if (key === "opacity") {
          el.style.opacity = String(value);
          continue;
        }
        if (key === "volume") {
          el.volume = value;
          continue;
        }
        try {
          el[key] = value;
        } catch {
          // A property the stub cannot set is not the logic under test.
        }
      }
    });
    if (typeof callback === "function") callback.call(this[0]);
    return this;
  };
  // jQuery's stop(true) clears animation state; the real one also jumps to
  // the end of an animation, which the stub above has no concept of.
  $.fn.stop = function () {
    return this;
  };

  // Load the served file once, with the stubs in place, through Node's
  // CJS loader: the module.exports guard at the end of splash.js fires,
  // the top-level code (socket creation, browser detection, the ready
  // callback) runs against the stubs, and the V8 coverage provider can
  // attribute the executed lines to the file on disk.
  const source = readFileSync(SPLASH_JS, "utf8");
  // The source is read so a missing or unreadable file fails here with a
  // clear error instead of deep in the loader.
  if (!source.includes("module.exports")) {
    throw new Error("splash.js is missing the test export guard");
  }
  splashExports = require(SPLASH_JS);

  // Defensive: if the document was not ready when the factory ran, jQuery
  // queued the setup for DOMContentLoaded; dispatch it so the setup runs
  // before any test does.
  if (document.readyState !== "complete") {
    document.dispatchEvent(new Event("DOMContentLoaded"));
  }
});

beforeEach(() => {
  // The module state is file-scoped in splash.js, so it outlives the test
  // that set it; reset it the way the Python suite resets election state.
  const s = splashExports.state;
  s.isMaster = false;
  s.currentPlaybackId = null;
  s.currentVideoUrl = null;
  s.nowPlaying = {};
  s.autoplayConfirmed = false;
  s.bgPlaylist = [];

  // Fresh media fakes per test, bound to the real elements below.
  currentVideoFake = makeMediaFake();
  bindMediaFake(document.getElementById("video"), currentVideoFake);
  currentAudioFake = makeMediaFake();
  bindMediaFake(
    document.getElementById("background-music"),
    currentAudioFake,
  );

  // A clean notification per test: the fade timers from a previous test
  // may otherwise leave it populated, and a populated notification makes
  // the flash guard return early.
  const sn = document.getElementById("splash-notification");
  sn.innerHTML = "";
  sn.className = "notification";
  sn.style.display = "none";

  // A clean slate for the call records and the per-file timers: flush
  // anything a previous test scheduled (notification fades, the background
  // resume delay) now that the state says nothing may play.
  socketStub.emitted = [];
  startScoreCalls.length = 0;
  screensaverCalls.start = 0;
  screensaverCalls.stop = 0;
  octopusInstances.length = 0;
  window.__hlsInstances.length = 0;
  vi.advanceTimersByTime(10000);
});

// Redirect a real media element's media properties and methods to a fake:
// the code under test keeps its normal element interactions (reads,
// writes, play, pause, load, and the event listeners) while the state it
// sees lives on the fake the tests assert on.
function bindMediaFake(el, fake) {
  for (const prop of MEDIA_PROPS) {
    Object.defineProperty(el, prop, {
      get: () => fake[prop],
      set: (value) => {
        fake[prop] = value;
      },
      configurable: true,
    });
  }
  el.play = () => fake.play();
  el.pause = () => fake.pause();
  el.load = () => fake.load();
}

// The element the code under test plays through; media properties read and
// write to the per-test fake.
export function getVideoElement() {
  return document.getElementById("video");
}

export function getVideoFake() {
  return currentVideoFake;
}

export function getBgMusicFake() {
  return currentAudioFake;
}
