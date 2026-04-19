# PiKaraoke User Story Compliance — TODO

Action items derived from the verification of `docs/USER_STORIES.md` against the
current codebase. Each item is grouped by user story and tagged with a priority:

- **P0** — architectural break or directly contradicts the story
- **P1** — significant functional gap
- **P2** — robustness / polish / minor non-conformity

> **Status**: All five P0 items have been resolved. See the
> "Completed P0s" section at the bottom for landed changes.

---

## Library and Search

### US-1 Unified search bar (PARTIAL)

- [ ] **P1** Add MusicBrainz suggestions to `/suggest`
      (`pikaraoke/routes/search.py:99`). Wire `fetch_musicbrainz_ids()` from
      `music_metadata.py:212`; merge with iTunes results, dedupe, mark
      `type: "musicbrainz"` so the UI can render distinct icons.
- [ ] **P2** Replace path-substring library matching
      (`pikaraoke/routes/search.py:84`) with a parsed artist/title field
      search so "songs"/"home" don't match. Use `metadata_parser` or DB
      `artist`/`title` columns.

### US-2 Add by YouTube URL (RESOLVED P0; remaining P2)

- [x] ~~**P0** Make yt-dlp cache-aware.~~ Done — pre-download `find_by_id`
      short-circuit in `queue_download` emits `song_downloaded` for the
      existing path and skips yt-dlp entirely.
- [x] ~~**P0** Emit `song_downloaded` on cache hit.~~ Done.
- [ ] **P2** Consider feeding canonical artist/track from yt-dlp's own
      info.json into the iTunes lookup, not just the display title
      (`pikaraoke/lib/download_manager.py:250`).

### US-3 Cache-aware re-request (RESOLVED P0; remaining P2)

- [x] ~~**P0** Inherits the yt-dlp cache fix from US-2.~~ Done.
- [ ] **P2** Skip re-running line-level ASS pipeline when a non-stale
      line-level `.ass` already exists and whisper isn't configured
      (`pikaraoke/lib/lyrics.py:178-186`, `_is_word_level_auto_ass` at
      `lyrics.py:427`).

### US-4 Delete a song (PARTIAL)

- [x] ~~**P1** Decide and document the lifecycle for `info_json` and
      `vtt`.~~ Done — chose option (b). `_cleanup_yt_subs_and_info`
      now unregisters `vtt` / `info_json` artifact rows when the files
      are deleted, keeping DB-vs-disk in sync (see US-29 note below).
- [ ] **P2** Reconcile the story wording vs. the `ass_user` preservation
      decision (`song_manager.py:180-181`). If preservation is correct
      (most likely), update `USER_STORIES.md` to call it out.
- [ ] **P2** Document the "queue blocks delete" behavior
      (`routes/files.py:151-166`) in the story or remove the restriction.

### US-5 Sync library — PASS

No action.

---

## Processing Pipeline

### US-6 Split audio/video download (PARTIAL)

- [ ] **P1** Either always use the split download path (story says
      unconditional), or update the story to acknowledge that split is
      gated on `vocal_removal`. Code: `download_manager.py:258-263`.
- [ ] **P2** Decide what should own `info.json`/subtitles when the video
      half fails. Consider letting the audio command write its own
      `info.json` so downstream isn't blocked
      (`pikaraoke/lib/youtube_dl.py:197-209`).

### US-7 Demucs on audio completion (PARTIAL)

- [ ] **P1** Emit `stems_ready` from the prewarm completion path so
      front-ends that connect after prewarm but before play receive the
      flip. Hook into `demucs_processor.prewarm`'s `done_event`
      (`demucs_processor.py:611-679`) and call the same emitter that
      `stream_manager._emit_stems_ready` (`stream_manager.py:767`) uses.
- [ ] **P1** Wire the splash to stream `vocals.wav.partial` →
      `vocals.wav` → `vocals.mp3` from prewarm-time, not just play-time.
      Surface partial progress to the seek-bar before the song starts.

### US-8 Lyrics lookup (RESOLVED P0)

- [x] ~~**P0** Fix the trigger inversion.~~ Done — chose option (b).
      `docs/USER_STORIES.md` US-8 has been rewritten to reflect that
      lyrics fetch starts at download time (cheaper signal, faster
      captions on screen) while whisper alignment waits for
      `stems_ready` internally. Pipeline implementation is unchanged.

### US-9 Subtitles ASS + word-level upgrade (PARTIAL)

- [ ] **P2** When the 120-second wait expires and whisper falls back to
      raw mix (`lyrics.py:1054-1076`), emit a `song_warning` so the
      operator knows alignment used a degraded source.

### US-10 Non-blocking parallel execution (RESOLVED P0; remaining P1)

- [x] ~~**P0** Emit `download_progress` SocketIO events.~~ Done — yt-dlp
      progress callbacks now emit `download_progress` (throttled to one
      event per integer percent change) with title/url/user/progress/
      speed/eta/status. Forwarded to clients in `karaoke.py:391-397`.
- [ ] **P1** Move LRCLib + iTunes lookup off the `song_downloaded`
      synchronous listener. Either:
      (a) make `EventSystem` async/threaded for that listener, OR
      (b) wrap `lyrics_service.fetch_and_convert` in a daemon thread
          inside the listener.
      Otherwise the download worker stalls up to ~10s per song
      (`karaoke.py:381`, `lyrics.py:154-246`).

---

## Splash Progress and Errors

### US-11 Splash notifications per stage (PARTIAL)

- [ ] **P1** Add the missing stage notifications:
      - "Downloading audio…" (separate from video,
        `download_manager.py:243`)
      - "Separating vocals… <pct>%" (drive from `demucs_progress`)
      - "Fetching lyrics…" (before `lyrics.py:175`)
      - "Aligning words…" (before `lyrics.py:241`)
- [ ] **P1** Replace the `if (sn.html()) return;` guard
      (`pikaraoke/static/splash.js:247`) with a small queue or stack so
      concurrent stage notifications aren't dropped silently.
- [ ] **P2** Consider per-stage styling (color/icon) so stages are
      visually distinct chips rather than one shared toast.

### US-12 Splash error indicators (PARTIAL)

- [ ] **P1** Add hover/focus tooltip behavior in addition to click
      (`splash.js:1218`). Required for keyboard accessibility.
- [ ] **P1** Show the affected song name inside the tooltip body, not
      just the icon `title` attribute (`splash.js:273-296`).
- [ ] **P1** Render the `#song-warning` icon outside `#now-playing` (or
      ensure it's visible pre-playback) so warnings emitted during
      processing are visible before the song actually starts.
- [ ] **P2** Add `role="button"`, `aria-label`, and `tabindex="0"` to
      `#song-warning` for accessibility.

### US-13 Warnings survive until acknowledged (PARTIAL)

- [ ] **P1** Add a "dismiss" action in the control panel
      (`pikaraoke/templates/base.html:219-247`) that clears warnings for
      a given song from the buffer, with a server-side socket event so
      all clients stay in sync.
- [ ] **P1** Don't drop warnings from the splash render when the next
      song starts (`splash.js:263-317`). Either keep them visible until
      operator dismisses, or persist them to a per-song log surfaced
      from the control panel.

---

## Lyrics Semantics

### US-14 Lyrics source precedence (PARTIAL)

- [x] ~~**P1** Stop writing VTT-derived `.ass` first and overwriting
      it with LRCLib.~~ Done — `_do_fetch_and_convert` now picks the
      chosen source (LRC > VTT) before writing, so the `.ass` is
      written exactly once per run. Covered by
      `test_lrc_wins_writes_ass_exactly_once`.
- [x] ~~**P1** Match VTT language against the track's language.~~
      Done — `_pick_best_vtt` now takes `preferred_lang`; matching
      primary subtags beat shorter/manual in the sort key. The
      service passes `self._db_language(song_path)` which pulls from
      `songs.language`.
- [x] ~~**P1** Persist VTT language to `songs.language`.~~ Done —
      when VTT is the chosen source, `_persist_vtt_language` extracts
      the lang code from the filename and writes it via
      `update_track_metadata_with_provenance(..., "scanner", ...)`,
      so whisperx alignment (and subsequent VTT picks) skip audio
      detection.
- [ ] **P2** Tighten the "auto/translation" detection — replace the
      brittle `"auto" in lang` substring check with a proper rule (e.g.
      `lang.endswith("-auto")` or `lang.endswith("-orig")`).

### US-15 Cache-correct re-alignment (PARTIAL)

- [ ] **P1** Call `ensure_audio_fingerprint` at download time as well as
      play time (`stream_manager.py:519`), so a re-downloaded source
      invalidates downstream caches before next play.
- [ ] **P1** Have `_maybe_drop_stale_auto_ass` (`lyrics.py:129`) also
      check the audio sha (or call `ensure_audio_fingerprint` first) so
      audio changes cascade to lyrics without waiting for playback.

### US-16 Language hint reuse (PARTIAL)

- [ ] **P1** Use the VTT language code as the fast-path language source
      when LRCLib misses (`lyrics.py:238` currently gates on `lrc`).
      Persist it to `songs.language`.
- [ ] **P2** Make `last_detected_language` thread-safe — either move it
      off the shared aligner singleton (`lyrics_align.py:47`) or guard
      it with a per-song lock so concurrent alignments don't clobber
      each other.
- [ ] **P2** Allow correcting a stale `db_lang` — drop the
      `not db_lang` guard at `lyrics.py:296` if the new detection has
      higher confidence.

### US-17 Reprocess library on aligner install (PARTIAL)

- [ ] **P1** Track "first whisperx install" with a sentinel in the DB
      `metadata` table (e.g. `whisperx_initial_reprocess_done = 1`) and
      gate `reprocess_library` on it (`karaoke.py:473`). On every
      restart it currently scans all songs.
- [ ] **P2** When upgrading existing line-level `.ass`, prefer reading
      the existing `.ass` text over re-fetching LRCLib via the filename
      (`lyrics.py:377-398`). Songs whose title/artist drifted are
      currently skipped silently.

---

## Playback

### US-18 Play a queued song (PARTIAL)

- [ ] **P2** Cache `can_serve_video_directly` results per file
      (`file_resolver.py:113-124`) — keyed by mtime/size — so we don't
      shell out to ffprobe on every play.
- [ ] **P2** Document that a non-aac audio track on h264 video still
      triggers an ffmpeg audio pipe (`stream_manager.py:230`); strictly
      speaking that's a transcode, even on the "direct video" path.

### US-19 Skip, pause, restart (PARTIAL)

- [x] ~~**P1** Make `karaoke.restart()` reset the server-side position
      to 0 and broadcast a `seek` to 0.~~ Done — restart zeros
      `playback_controller.now_playing_position` / `position_updated_at`
      and emits `socketio.emit("seek", 0.0)` so every splash (not just
      the click origin) rewinds, even if its client-side handler is
      slow or disconnected.
- [ ] **P2** Decide whether server-side pause should also pause the
      ffmpeg subprocess (SIGSTOP/SIGCONT) on the transcoded paths, or
      whether client-only pause is sufficient.

### US-20 Queue management — PASS

No action.

---

## Now-Playing Panel

### US-21 Now-playing display (PARTIAL)

- [x] ~~**P1** Add an `artist` field to now_playing and render it.~~
      Done — `PlaybackController.play_file` looks up the artist via
      `db.get_song_by_path()` on song start, exposes it as
      `now_playing_artist` in `get_now_playing()`, and the expanded
      player template + `now-playing-bar.js` render it under the title
      using the `.pk-player-artist` CSS class. Hidden when empty so
      rows without a canonical artist don't leave a blank line.

### US-22 Volume: single vs. dual sliders (PARTIAL)

- [ ] **P2** Persist a "stems enabled this session" flag on the
      front-end so a `now_playing` poll arriving just after
      `stems_ready` doesn't flip the sliders back
      (`pikaraoke/static/now-playing-bar.js:200-223`).

### US-23 Seek bar with buffering progress (PARTIAL)

- [ ] **P1** Emit `ffmpeg_progress` on the non-HLS transcoded MP4 path
      too (`stream_manager.py:351-352` only starts the monitor when
      `is_hls=True`). Otherwise the seek bar can't show ffmpeg
      buffering on that path.
- [ ] **P2** Initialize `seekBufferedDemucs` to a conservative ceiling
      (e.g. 0) before the first `demucs_progress` tick so the slider
      reflects the actual buffered range from the start
      (`now-playing-bar.js:281-289`).

### US-24 Visual cues for processing progress (PARTIAL)

- [ ] **P1** Add a separate processing indicator in the now-playing
      panel (spinner, badge, or text chip — e.g. "Separating vocals…
      <pct>%") next to the seek bar. Currently the only cue is the
      seek-bar shading.

---

## Splash Screen

### US-25 Captions rendering (PARTIAL)

- [ ] **P2** Emit a `song_warning` (severity `info`) when `librosa` is
      missing and the BPM pulse is silently disabled
      (`lyrics.py:613-615`). Operator should know dependencies are
      degraded.
- [ ] **P2** Document (or fix) that `_estimate_bpm` may run on the raw
      mix when stems aren't ready in time (`lyrics.py:300`).

### US-26 Now-playing overlay and QR — PASS

No action.

### US-27 User-action introduction screen (PARTIAL)

- [ ] **P1** Replace the brittle muted-then-unmuted autoplay test
      (`splash.js:74-109`). Use the actual `play()` promise on an
      audible test asset and catch `NotAllowedError`, which is the
      browser-spec signal for blocked audio autoplay.
- [ ] **P2** Provide a graceful fallback when
      `/static/video/test_autoplay.mp4` is missing — currently the
      `onerror` always shows the modal.

---

## Database as Source of Truth

### US-28 Track metadata persisted in DB (RESOLVED P0; remaining P2)

- [x] ~~**P0** Add provenance tracking for canonical metadata.~~ Done —
      migration V4 adds a `metadata_sources` JSON column on `songs`
      ({field: source}). New helpers `update_track_metadata_with_provenance`
      and `get_metadata_sources` on `KaraokeDatabase`.
- [x] ~~**P0** Implement confidence-based override in `song_enricher`.~~
      Done — `METADATA_SOURCE_CONFIDENCE` ladder
      (musicbrainz > itunes > youtube > scanner; manual on top).
      `_MEDIA_SOURCE_CONFIDENCE` separately ranks YouTube above remote
      DBs for media-specific fields (`duration_seconds`, `source_url`).
      All callers (`song_enricher`, `library_scanner`, `song_manager`,
      `lyrics`) now go through the provenance-aware method.
- [ ] **P2** Populate `year` and `variant` columns in the enricher.
      Currently in schema but never written.

### US-29 Artifact registry (PARTIAL)

- [x] ~~**P1** Unregister `info_json` and `vtt` artifact rows.~~ Done —
      `_cleanup_yt_subs_and_info` now accepts the DB, looks up the
      song_id, and calls `db.delete_artifacts_by_role(song_id, "vtt")`
      / `"info_json"` right after unlinking the files. LyricsService
      passes `self._db` at all three call sites, so the DB no longer
      keeps ghost rows for files that were cleaned up post-convert.
- [ ] **P2** Re-register the audio sibling `.m4a` after split downloads
      finish; `discover_song_artifacts` (`song_manager.py:45-83`) only
      finds it if it exists at registration time.
- [ ] **P2** Make `LibraryScanner` re-evaluate songs that have a
      partial artifact set (not just zero rows;
      `karaoke_database.py:186`).

### US-30 Cache-file fingerprints (PARTIAL)

- [ ] **P1** Add `sha256`, `size`, `mtime` columns to `song_artifacts`
      (`karaoke_database.py:63-75`) so each cache file has its own
      fingerprint, with the same cheap-refresh logic as audio.
- [ ] **P2** Apply the same fingerprint flow to stems directory and
      `.ass` outputs.

### US-31 Waterfall invalidation (RESOLVED P0; remaining P1)

- [x] ~~**P0** Clear `lyrics_sha` on audio sha change.~~ Done —
      `_invalidate_auto_ass` now also clears `lyrics_sha` and
      `aligner_model` via `update_processing_config`. Next pipeline run
      treats LRCLib as never-fetched and re-queries it. Covered by new
      tests in `tests/unit/test_audio_fingerprint.py::TestLyricsShaClearedOnAudioChange`.
- [ ] **P1** Make sure `ensure_stems_config` also invalidates the
      aligned `.ass` (currently the `.ass` invalidation depends on
      `ensure_lyrics_config` running independently;
      `audio_fingerprint.py:79-100`).
- [ ] **P1** Run `ensure_audio_fingerprint` at download time, not only
      at playback (`stream_manager.py:519`). Without this, model/audio
      changes don't cascade until the song is next played.

### US-32 Cache cleanup on song delete (PARTIAL)

- [ ] **P2** Replace `os.path.basename(path)` as the sha extraction
      (`song_manager.py:178`) with reading the sha from the DB row to
      avoid trailing-slash fragility.
- [ ] Resolves automatically once US-29 ghost-row issue is fixed.

---

## Caching and Storage

### US-33 Stems cache — PASS

No action.

### US-34 Atomic writes (PARTIAL)

- [x] ~~**P1** Make `_merge_metadata_into_info_json` atomic.~~ Done —
      writes to `info_path + ".part"` then `os.replace`; cleans up the
      tempfile on OSError so the original is never truncated.
- [ ] **P2** Make `PreferenceManager.set` atomic
      (`preference_manager.py:130`): tempfile + `os.replace`.

---

## Settings and Preferences

### US-35 Preferences persist across restarts (PARTIAL)

- [x] ~~**P1** Add `download_path`, `youtubedl_proxy`, and
      `preferred_language` to `PreferenceManager.DEFAULTS`.~~ Done —
      added as string defaults (empty = unset), `apply_all` now
      persists CLI values for them on startup so the UI can read them
      back via `get_or_default`. `reset_all` skips `download_path` /
      `youtubedl_proxy` so admin "reset preferences" doesn't blank
      the live runtime paths.

### US-36 Toggle vocal removal — PASS

No action.

---

## Admin and Diagnostics

### US-37 Manual library sync — PASS

No action.

### US-38 Download error surface (PARTIAL)

- [x] ~~**P1** Persist `download_errors` to DB.~~ Done — stored as a
      JSON blob under `metadata["download_errors"]`; `DownloadManager`
      loads on init and flushes on every append/remove. Survives
      restart.
- [x] ~~**P1** Admin-gate the dismiss endpoint.~~ Done — the
      `DELETE /queue/downloads/errors/<id>` route now returns 403 for
      non-admins.
- [x] ~~**P2** Add a `timestamp` field to each error dict.~~ Done —
      new errors carry `timestamp: time.time()` (epoch seconds) for
      diagnostics.

### US-39 Structured warnings (PARTIAL)

- [x] ~~**P1** Use real severity levels at emission sites.~~ Done —
      `StreamManager._emit_song_warning` now takes a `severity` kwarg,
      and "Audio extraction failed" (the only unrecoverable case)
      emits with `severity="error"`. Degraded-but-functional paths
      (Demucs fallback, MP3 encode, lyrics misses, word-level align
      failure) stay on `"warning"`.
- [x] ~~**P1** Persist `song_warning` events + admin view.~~ Done —
      Karaoke keeps a rolling 200-entry buffer persisted to
      `metadata["song_warnings"]`; every `song_warning` event carries
      a `timestamp` and is appended. New routes
      `GET/DELETE /admin/song_warnings` (admin-gated) surface and
      clear the log.
- [x] ~~**P1** Bridge `download_errors` and `song_warning`.~~ Done —
      after appending to `download_errors`, `DownloadManager` emits a
      mirrored `song_warning` with `severity="error"` so the two
      surfaces stay in sync.

---

## Priority Roll-Up

**P0 (architectural / story-breaking):** all resolved (see below).

**P1 (significant gaps):** the bulk of the items above.

**P2 (polish/robustness):** ASS pulse `librosa` warning, ffprobe caching,
queue-blocks-delete documentation, etc.

---

## Completed P0s

| Story | What landed | Files touched |
|---|---|---|
| US-2 / US-3 | Pre-download cache short-circuit in `queue_download`: when `find_by_id` finds the video on disk we emit `song_downloaded` (and optionally enqueue) without invoking yt-dlp. | `pikaraoke/lib/download_manager.py`, `tests/unit/test_download_manager.py` |
| US-8 | Story rewritten to match the better implementation: lyrics fetch fires at download time so captions appear sooner; whisper alignment alone waits for `stems_ready`. | `docs/USER_STORIES.md` |
| US-10 | Throttled `download_progress` socket event from yt-dlp progress callbacks (one emit per integer-pct bucket); forwarder wired in `karaoke.py`. | `pikaraoke/lib/download_manager.py`, `pikaraoke/karaoke.py`, `tests/unit/test_download_manager.py` |
| US-28 | Schema migration V4 adds `metadata_sources` JSON column. New `update_track_metadata_with_provenance` + `get_metadata_sources` apply a confidence ladder (musicbrainz > itunes > youtube > scanner; manual on top). All metadata writers updated. | `pikaraoke/lib/karaoke_database.py`, `pikaraoke/lib/song_enricher.py`, `pikaraoke/lib/library_scanner.py`, `pikaraoke/lib/song_manager.py`, `pikaraoke/lib/lyrics.py`, plus DB and enricher tests |
| US-31 | `_invalidate_auto_ass` now also clears `lyrics_sha` and `aligner_model`, so audio-sha changes trigger an LRCLib re-fetch on next run. | `pikaraoke/lib/audio_fingerprint.py`, `tests/unit/test_audio_fingerprint.py` |

Full unit suite: 1078 passed, 1 skipped.
