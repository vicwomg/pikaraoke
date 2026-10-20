// Unit tests for the splash screen's protocol logic.
//
// The splash screen talks to the server over Socket.IO: it beacons its
// position, reports starts and ends, applies now-playing and preference
// updates, and keeps slave screens in sync with the master. These tests
// drive the real handlers through a stub socket and a fake media element,
// so the protocol behaviour - which tab emits what, with which playback
// id, and when - is pinned down the same way the server side is pinned
// down by the Python suite.

import { describe, expect, it, beforeEach, vi } from "vitest";
import {
  splash,
  socketStub,
  startScoreCalls,
  octopusInstances,
  getVideoElement,
  getVideoFake,
  getBgMusicFake,
  state,
} from "./splash.setup.js";

// The element the code under test plays through; its media properties read
// and write to the per-test fake.
let video = null;

beforeEach(() => {
  video = getVideoElement();
  // The config object is file-scoped; the tests change it, so restore the
  // defaults the same way the state is restored.
  globalThis.PikaraokeConfig.disableScore = false;
  globalThis.PikaraokeConfig.disableBgMusic = false;
  globalThis.PikaraokeConfig.disableBgVideo = false;
});

// Put the fake in the four states isMediaPlaying reads.
function setPlaying(playing = true) {
  video.currentTime = playing ? 5 : 0;
  video.paused = !playing;
  video.ended = false;
  video.readyState = 4;
}

describe("isMediaPlaying", () => {
  it("is true for an element that has content and is running", () => {
    const el = { currentTime: 5, paused: false, ended: false, readyState: 4 };
    expect(splash.isMediaPlaying(el)).toBe(true);
  });

  it("is false when the element is paused", () => {
    const el = { currentTime: 5, paused: true, ended: false, readyState: 4 };
    expect(splash.isMediaPlaying(el)).toBe(false);
  });

  it("is false when the element has ended", () => {
    const el = { currentTime: 5, paused: true, ended: true, readyState: 4 };
    expect(splash.isMediaPlaying(el)).toBe(false);
  });

  it("is false before the element has enough data to play", () => {
    const el = { currentTime: 5, paused: false, ended: false, readyState: 2 };
    expect(splash.isMediaPlaying(el)).toBe(false);
  });

  it("is false at time zero, where a seek cannot yet have happened", () => {
    const el = { currentTime: 0, paused: false, ended: false, readyState: 4 };
    expect(splash.isMediaPlaying(el)).toBe(false);
  });
});

describe("formatTime", () => {
  it("returns 00:00 for a non-number", () => {
    expect(splash.formatTime(NaN)).toBe("00:00");
  });

  it("pads minutes and seconds to two digits", () => {
    expect(splash.formatTime(0)).toBe("00:00");
    expect(splash.formatTime(75)).toBe("01:15");
    expect(splash.formatTime(3599.9)).toBe("59:59");
  });

  it("wraps at the hour boundary", () => {
    expect(splash.formatTime(3600)).toBe("00:00");
  });
});

describe("parsePreferenceValue", () => {
  it("maps the string forms of Python booleans", () => {
    expect(splash.parsePreferenceValue("True")).toBe(true);
    expect(splash.parsePreferenceValue("False")).toBe(false);
  });

  it("maps numeric strings to numbers", () => {
    expect(splash.parsePreferenceValue("5")).toBe(5);
    expect(splash.parsePreferenceValue("3.5")).toBe(3.5);
  });

  it("passes other strings through unchanged", () => {
    expect(splash.parsePreferenceValue("abc")).toBe("abc");
    expect(splash.parsePreferenceValue("")).toBe("");
  });

  it("passes non-strings through unchanged", () => {
    expect(splash.parsePreferenceValue(5)).toBe(5);
    expect(splash.parsePreferenceValue(true)).toBe(true);
    expect(splash.parsePreferenceValue(null)).toBe(null);
  });
});

describe("position beacon", () => {
  it("beacons the position and the playback id once a second while playing", () => {
    state().isMaster = false;
    state().currentPlaybackId = "PB1";
    setPlaying(true);
    video.currentTime = 12.5;

    vi.advanceTimersByTime(1000);
    expect(socketStub.emitted).toContainEqual([
      "playback_position",
      12.5,
      "PB1",
    ]);
  });

  it("beacons from a slave, not only from the master", () => {
    // The master seat is elected by these beacons, so a master-only gate
    // would make the election self-fulfilling. Pin the un-gating.
    state().isMaster = false;
    state().currentPlaybackId = "PB1";
    setPlaying(true);

    vi.advanceTimersByTime(1000);
    expect(
      socketStub.emitted.some(([e]) => e === "playback_position"),
    ).toBe(true);
  });

  it("does not beacon a paused video", () => {
    state().currentPlaybackId = "PB1";
    setPlaying(false);

    vi.advanceTimersByTime(1000);
    expect(socketStub.emitted.some(([e]) => e === "playback_position")).toBe(
      false,
    );
  });

  it("beacons the id the playback has at tick time, not at load time", () => {
    state().currentPlaybackId = "PB1";
    setPlaying(true);

    state().currentPlaybackId = "PB2";
    vi.advanceTimersByTime(1000);
    expect(socketStub.emitted).toContainEqual(["playback_position", 5, "PB2"]);
  });
});

describe("start_song emission", () => {
  it("emits start_song with the id captured at play time", () => {
    state().isMaster = true;
    state().currentPlaybackId = "PB1";

    video.dispatchEvent(new Event("play"));
    // A song skipped inside the delay must not move the report onto it.
    state().currentPlaybackId = "PB2";
    vi.advanceTimersByTime(1200);
    expect(socketStub.emitted).toContainEqual(["start_song", "PB1"]);
  });

  it("does not emit start_song from a slave", () => {
    state().isMaster = false;
    state().currentPlaybackId = "PB1";

    video.dispatchEvent(new Event("play"));
    vi.advanceTimersByTime(1200);
    expect(socketStub.emitted.some(([e]) => e === "start_song")).toBe(false);
  });
});

describe("endSong", () => {
  it("emits end_song with the id current when the song ended", async () => {
    state().isMaster = true;
    state().currentPlaybackId = "PB1";
    state().currentVideoUrl = "http://stream/1";

    await splash.endSong("complete");
    expect(socketStub.emitted).toContainEqual([
      "end_song",
      "complete",
      "PB1",
    ]);
    // The local claim is cleared up front, so a late end cannot hit the
    // successor.
    expect(state().currentPlaybackId).toBe(null);
  });

  it("does not emit end_song from a slave", async () => {
    state().isMaster = false;
    state().currentPlaybackId = "PB1";

    await splash.endSong("skip");
    expect(socketStub.emitted.some(([e]) => e === "end_song")).toBe(false);
  });

  it("shows the score screen when asked and the preference allows it", async () => {
    state().isMaster = true;
    state().currentPlaybackId = "PB1";

    await splash.endSong("complete", true);
    expect(startScoreCalls).toEqual(["/static/"]);
  });

  it("skips the score screen when the preference disables it", async () => {
    globalThis.PikaraokeConfig.disableScore = true;
    try {
      state().isMaster = true;
      state().currentPlaybackId = "PB1";

      await splash.endSong("complete", true);
      expect(startScoreCalls).toEqual([]);
    } finally {
      globalThis.PikaraokeConfig.disableScore = false;
    }
  });

  it("tears the player down when nothing loaded during the score screen", async () => {
    state().isMaster = true;
    state().currentPlaybackId = "PB1";
    state().currentVideoUrl = "http://stream/1";

    await splash.endSong("complete");
    expect(state().currentVideoUrl).toBe(null);
    expect(video.paused).toBe(true);
  });

  it("does not tear down a song that loaded during the score screen", async () => {
    // A second song that starts while the score screen is up has already
    // loaded itself into the player; tearing down now would go black.
    const realStartScore = globalThis.startScore;
    globalThis.startScore = async () => {
      state().currentPlaybackId = "PB2";
      state().currentVideoUrl = "http://stream/2";
    };
    try {
      state().isMaster = true;
      state().currentPlaybackId = "PB1";
      state().currentVideoUrl = "http://stream/1";

      await splash.endSong("complete", true);
      expect(state().currentVideoUrl).toBe("http://stream/2");
      expect(socketStub.emitted).toContainEqual([
        "end_song",
        "complete",
        "PB1",
      ]);
    } finally {
      globalThis.startScore = realStartScore;
    }
  });

  it("ends the song as complete when the video element fires ended", async () => {
    state().isMaster = true;
    state().currentPlaybackId = "PB3";
    setPlaying(true);

    video.dispatchEvent(new Event("ended"));
    await vi.advanceTimersByTimeAsync(0);
    expect(socketStub.emitted).toContainEqual([
      "end_song",
      "complete",
      "PB3",
    ]);
  });
});

describe("handleNowPlayingUpdate", () => {
  const base = {
    now_playing: null,
    now_playing_user: null,
    now_playing_duration: null,
    now_playing_transpose: 0,
    now_playing_url: null,
    now_playing_subtitle_url: null,
    now_playing_position: 0,
    is_paused: false,
    up_next: null,
    next_user: null,
    session_name: "",
    playback_id: null,
    volume: 0.85,
  };

  it("stores the playback id of the update", () => {
    splash.handleNowPlayingUpdate({ ...base, playback_id: "PB9" });
    expect(state().currentPlaybackId).toBe("PB9");
  });

  it("starts a new stream at its url when one is named", () => {
    splash.handleNowPlayingUpdate({
      ...base,
      now_playing_url: "http://stream/one",
      playback_id: "PB1",
    });
    const source = document.getElementById("video-source");
    expect(source.getAttribute("src")).toBe("http://stream/one");
    expect(getVideoFake().loadCalls).toBeGreaterThanOrEqual(1);
    expect(getVideoFake().playCalls).toBeGreaterThanOrEqual(1);
    expect(state().currentVideoUrl).toBe("http://stream/one");
  });

  it("seeks a mid-song load to the reported position once metadata is in", () => {
    splash.handleNowPlayingUpdate({
      ...base,
      now_playing_url: "http://stream/one",
      now_playing_position: 30,
      playback_id: "PB1",
    });
    video.dispatchEvent(new Event("loadedmetadata"));
    expect(video.currentTime).toBe(30);
  });

  it("does not seek a fresh start", () => {
    splash.handleNowPlayingUpdate({
      ...base,
      now_playing_url: "http://stream/one",
      now_playing_position: 0,
      playback_id: "PB1",
    });
    video.dispatchEvent(new Event("loadedmetadata"));
    expect(video.currentTime).toBe(0);
  });

  it("routes an hls playlist through hls.js with the resume point", () => {
    splash.handleNowPlayingUpdate({
      ...base,
      now_playing_url: "http://stream/one.m3u8",
      now_playing_position: 15,
      playback_id: "PB1",
    });
    expect(window.__hlsInstances.length).toBe(1);
    expect(window.__hlsInstances[0].loadedSource).toBe("http://stream/one.m3u8");
    expect(window.__hlsInstances[0].config.startPosition).toBe(15);
  });

  it("stops the video when the server ended the song", () => {
    state().currentVideoUrl = "http://stream/old";
    state().currentPlaybackId = "PB1";

    splash.handleNowPlayingUpdate({ ...base, now_playing_url: null });
    expect(state().currentVideoUrl).toBe(null);
    expect(state().currentPlaybackId).toBe(null);
    expect(getVideoFake().paused).toBe(true);
  });

  it("renders the now-playing line with the transpose label", () => {
    splash.handleNowPlayingUpdate({
      ...base,
      now_playing: "Song One",
      now_playing_user: "Singer",
      now_playing_transpose: 2,
      now_playing_duration: 90,
      now_playing_url: "http://stream/one",
      playback_id: "PB1",
    });
    const song = document.getElementById("now-playing-song");
    expect(song.innerHTML).toContain("Song One");
    expect(song.innerHTML).toContain("+2");
    const duration = document.getElementById("duration");
    expect(duration.textContent).toBe("/01:30");
    expect(duration.style.display).not.toBe("none");
  });

  it("hides the now-playing block when nothing is playing", () => {
    splash.handleNowPlayingUpdate({ ...base, now_playing: "Song One" });
    splash.handleNowPlayingUpdate({ ...base });
    expect(document.getElementById("now-playing").style.display).toBe("none");
  });

  it("shows and hides the session name from the update", () => {
    splash.handleNowPlayingUpdate({ ...base, session_name: "Karaoke Night" });
    const name = document.getElementById("session-name");
    expect(name.textContent).toBe("Karaoke Night");
    expect(name.style.display).not.toBe("none");

    splash.handleNowPlayingUpdate({ ...base, session_name: "" });
    expect(name.style.display).toBe("none");
  });

  it("builds a subtitle instance and disposes the previous one", () => {
    splash.handleNowPlayingUpdate({
      ...base,
      now_playing_subtitle_url: "http://sub/one.ass",
      now_playing_url: "http://stream/one",
      playback_id: "PB1",
    });
    splash.handleNowPlayingUpdate({
      ...base,
      now_playing_subtitle_url: "http://sub/two.ass",
      now_playing_url: "http://stream/one",
      playback_id: "PB1",
    });
    expect(octopusInstances.length).toBe(2);
    expect(octopusInstances[0].disposed).toBe(true);
    expect(octopusInstances[1].disposed).toBe(false);
    expect(octopusInstances[1].options.subUrl).toBe("http://sub/two.ass");
  });

  it("applies the volume carried by the update", () => {
    splash.handleNowPlayingUpdate({
      ...base,
      now_playing_url: "http://stream/one",
      volume: 0.5,
      playback_id: "PB1",
    });
    expect(video.volume).toBe(0.5);
  });
});

describe("role handling", () => {
  it("sets the master flag and resets the playback rate", () => {
    video.playbackRate = 1.05;
    socketStub.fire("splash_role", "master");
    expect(state().isMaster).toBe(true);
    expect(video.playbackRate).toBe(1);
  });

  it("clears the master flag for a slave", () => {
    state().isMaster = true;
    socketStub.fire("splash_role", "slave");
    expect(state().isMaster).toBe(false);
  });
});

describe("preference updates", () => {
  it("toggles the url and the qr code separately", () => {
    splash.applyPreferenceUpdate({ key: "hide_url", value: "True" });
    expect(document.querySelector(".splash-url").style.display).toBe("none");

    splash.applyPreferenceUpdate({ key: "hide_url", value: "False" });
    expect(
      document.querySelector(".splash-url").style.display,
    ).not.toBe("none");

    splash.applyPreferenceUpdate({ key: "hide_qr_code", value: "True" });
    expect(document.querySelector(".splash-qr-image").style.display).toBe(
      "none",
    );
  });

  it("updates the config for the background media switches", () => {
    splash.applyPreferenceUpdate({ key: "disable_bg_video", value: "True" });
    expect(globalThis.PikaraokeConfig.disableBgVideo).toBe(true);

    splash.applyPreferenceUpdate({ key: "disable_bg_music", value: "False" });
    expect(globalThis.PikaraokeConfig.disableBgMusic).toBe(false);
  });

  it("stores the screensaver timeout as a number", () => {
    splash.applyPreferenceUpdate({ key: "screensaver_timeout", value: "5" });
    expect(globalThis.PikaraokeConfig.screensaverTimeout).toBe(5);
  });

  it("applies the splash scale to the ui targets", () => {
    splash.applyPreferenceUpdate({ key: "splash_scale", value: "1.5" });
    const top = document.getElementById("top-container");
    expect(top.style.transform).toBe("scale(1.5)");
    expect(top.style.transformOrigin).toBe("top right");

    splash.applyPreferenceUpdate({ key: "splash_scale", value: "1" });
    // At 1 the transform is cleared, not set to scale(1): a transform
    // starts a stacking context and would change the rendering.
    expect(top.style.transform).toBe("");
  });

  it("applies a whole defaults table on reset", () => {
    splash.applyPreferencesReset({
      hide_url: "True",
      hide_qr_code: "True",
      splash_scale: "2",
    });
    expect(document.querySelector(".splash-url").style.display).toBe("none");
    expect(document.querySelector(".splash-qr-image").style.display).toBe(
      "none",
    );
    expect(
      document.getElementById("top-container").style.transform,
    ).toBe("scale(2)");
  });

  it("ignores a key with no registered effect", () => {
    expect(() =>
      splash.applyPreferenceUpdate({ key: "no_such_key", value: "True" }),
    ).not.toThrow();
  });
});

describe("slave drift sync", () => {
  beforeEach(() => {
    state().isMaster = false;
    setPlaying(true);
    video.currentTime = 10;
    video.playbackRate = 1;
  });

  it("seeks a slave that is more than two seconds out of step", () => {
    socketStub.fire("playback_position", 13);
    expect(video.currentTime).toBe(13);
  });

  it("closes a larger small gap at the ten percent clamp, not faster", () => {
    // Without the clamp the rate would be 1 + 0.4/2 = 1.2.
    socketStub.fire("playback_position", 10.4);
    expect(video.playbackRate).toBeCloseTo(1.1);
  });

  it("nudges a small gap proportionally", () => {
    socketStub.fire("playback_position", 10.1);
    expect(video.playbackRate).toBeCloseTo(1.05);
  });

  it("slows a slave that is ahead of the master", () => {
    socketStub.fire("playback_position", 9.9);
    expect(video.playbackRate).toBeCloseTo(0.95);
  });

  it("restores the normal rate for a negligible gap", () => {
    socketStub.fire("playback_position", 10.01);
    expect(video.playbackRate).toBe(1);
  });

  it("is a no-op on the master", () => {
    state().isMaster = true;
    socketStub.fire("playback_position", 13);
    expect(video.currentTime).toBe(10);
    expect(video.playbackRate).toBe(1);
  });
});

describe("ui scaling", () => {
  it("uses the stored preference when the url carries no scale", () => {
    globalThis.PikaraokeConfig.splashScale = 2;
    expect(splash.effectiveUIScale()).toBe(2);
  });

  it("clears the transform at scale one", () => {
    splash.applyUIScale(1.5);
    splash.applyUIScale(1);
    expect(
      document.getElementById("top-container").style.transform,
    ).toBe("");
  });
});

describe("socket housekeeping", () => {
  it("registers the splash on connect", () => {
    socketStub.fire("connect");
    expect(socketStub.emitted).toContainEqual(["register_splash"]);
  });

  it("flashes a notification", () => {
    socketStub.fire("notification", "Song added::is-success");
    const sn = document.getElementById("splash-notification");
    expect(sn.innerHTML).toContain("Song added");
    expect(sn.querySelector(".notification").classList.contains("is-success")).toBe(true);
  });

  it("flashes a notification on a slave too", () => {
    state().isMaster = false;
    socketStub.fire("notification", "Another song::is-primary");
    expect(document.getElementById("splash-notification").innerHTML).toContain("Another song");
  });

  it("clears the notification after it has shown", () => {
    splash.flashNotification("Shown once", "is-primary");
    expect(
      document.getElementById("splash-notification").innerHTML,
    ).toContain("Shown once");
    vi.advanceTimersByTime(3000 + 450);
    expect(document.getElementById("splash-notification").innerHTML).toBe("");
  });
});

describe("volume and transport controls", () => {
  it("steps the volume up and down", () => {
    video.volume = 0.5;
    socketStub.fire("volume", "up");
    expect(video.volume).toBeCloseTo(0.6);
    socketStub.fire("volume", "down");
    socketStub.fire("volume", "down");
    expect(video.volume).toBeCloseTo(0.4);
  });

  it("clamps the volume at the top and bottom", () => {
    video.volume = 1;
    socketStub.fire("volume", "up");
    expect(video.volume).toBe(1);
    video.volume = 0;
    socketStub.fire("volume", "down");
    expect(video.volume).toBe(0);
  });

  it("sets an absolute volume when given a number", () => {
    socketStub.fire("volume", 0.3);
    expect(video.volume).toBe(0.3);
  });

  it("pauses a playing song with the fade-out, restoring the volume", () => {
    setPlaying(true);
    video.volume = 0.7;
    socketStub.fire("pause");
    expect(video.paused).toBe(true);
    expect(video.volume).toBe(0.7);
  });

  it("resumes a paused song", () => {
    setPlaying(false);
    video.volume = 0.7;
    socketStub.fire("play");
    expect(getVideoFake().playCalls).toBeGreaterThanOrEqual(1);
    expect(video.paused).toBe(false);
  });

  it("skips to the next song and hides the video", () => {
    setPlaying(true);
    socketStub.fire("skip");
    expect(video.paused).toBe(true);
    expect(document.getElementById("video-container").style.display).toBe(
      "none",
    );
  });

  it("restarts the song from the beginning", () => {
    setPlaying(false);
    video.currentTime = 42;
    socketStub.fire("restart");
    expect(video.currentTime).toBe(0);
    expect(getVideoFake().playCalls).toBeGreaterThanOrEqual(1);
  });
});

describe("background media state", () => {
  it("plays only after confirmation and only when idle", () => {
    state().autoplayConfirmed = false;
    state().nowPlaying = { now_playing: null, up_next: null };
    expect(splash.shouldBackgroundMediaPlay()).toBe(false);

    state().autoplayConfirmed = true;
    expect(splash.shouldBackgroundMediaPlay()).toBe(true);

    state().nowPlaying = { now_playing: "Song One", up_next: null };
    expect(splash.shouldBackgroundMediaPlay()).toBe(false);
  });

  it("advances the background playlist in order, wrapping at the end", () => {
    state().bgPlaylist = ["a.mp3", "b.mp3", "c.mp3"];
    const audio = document.getElementById("background-music");

    expect(splash.getNextBgMusicSong()).toBe("a.mp3");
    audio.setAttribute("src", "a.mp3");
    expect(splash.getNextBgMusicSong()).toBe("b.mp3");
    audio.setAttribute("src", "c.mp3");
    expect(splash.getNextBgMusicSong()).toBe("a.mp3");
  });

  it("does not start background music before confirmation", () => {
    state().autoplayConfirmed = false;
    state().bgPlaylist = ["a.mp3"];
    expect(getBgMusicFake().playCalls).toBe(0);
  });
});
