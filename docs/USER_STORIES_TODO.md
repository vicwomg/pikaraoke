# PiKaraoke User Story Compliance — TODO

Action items derived from the verification of `docs/USER_STORIES.md` against the
current codebase. Each item is grouped by user story and tagged with a priority:

- **P0** — architectural break or directly contradicts the story
- **P1** — significant functional gap
- **P2** — robustness / polish / minor non-conformity

> **Status**: All five original P0 items are resolved. US-40 (satellites)
> adds three new P0s — architectural decisions for a new feature, not
> regressions. See the "Completed P0s" section at the bottom for
> already-landed work.

______________________________________________________________________

## Library and Search

### US-1 Unified search bar (PARTIAL)

- \[x\] ~~**P1** Add MusicBrainz suggestions to `/suggest`.~~ Done —
  new `music_metadata.search_musicbrainz(query, limit)` does a
  free-text MB recording search (LRU-cached, bounded timeout).
  `/suggest` runs it in parallel with `search_itunes`, dedupes on
  lowercased `"artist - track"`, and tags hits with
  `type: "itunes"` / `"musicbrainz"` so the UI can render
  distinct icons. iTunes wins the dedupe when both sources
  return the same pair.
- \[ \] **P2** Replace path-substring library matching
  (`pikaraoke/routes/search.py:84`) with a parsed artist/title field
  search so "songs"/"home" don't match. Use `metadata_parser` or DB
  `artist`/`title` columns.

### US-2 Add by YouTube URL (RESOLVED P0; remaining P2)

- \[x\] ~~**P0** Make yt-dlp cache-aware.~~ Done — pre-download `find_by_id`
  short-circuit in `queue_download` emits `song_downloaded` for the
  existing path and skips yt-dlp entirely.
- \[x\] ~~**P0** Emit `song_downloaded` on cache hit.~~ Done.
- \[ \] **P2** Consider feeding canonical artist/track from yt-dlp's own
  info.json into the iTunes lookup, not just the display title
  (`pikaraoke/lib/download_manager.py:250`).

### US-3 Cache-aware re-request (RESOLVED P0; remaining P2)

- \[x\] ~~**P0** Inherits the yt-dlp cache fix from US-2.~~ Done.
- \[ \] **P2** Skip re-running line-level ASS pipeline when a non-stale
  line-level `.ass` already exists and whisper isn't configured
  (`pikaraoke/lib/lyrics.py:178-186`, `_is_word_level_auto_ass` at
  `lyrics.py:427`).

### US-4 Delete a song (PARTIAL)

- \[x\] ~~**P1** Decide and document the lifecycle for `info_json` and
  `vtt`.~~ Done — chose option (b). `_cleanup_yt_subs_and_info`
  now unregisters `vtt` / `info_json` artifact rows when the files
  are deleted, keeping DB-vs-disk in sync (see US-29 note below).
- \[ \] **P2** Reconcile the story wording vs. the `ass_user` preservation
  decision (`song_manager.py:180-181`). If preservation is correct
  (most likely), update `USER_STORIES.md` to call it out.
- \[ \] **P2** Document the "queue blocks delete" behavior
  (`routes/files.py:151-166`) in the story or remove the restriction.

### US-5 Sync library — PASS

No action.

______________________________________________________________________

## Processing Pipeline

### US-6 Split audio/video download (PARTIAL)

- \[x\] ~~**P1** Reconcile split gate vs story.~~ Done — updated US-6 in
  `docs/USER_STORIES.md` to call out that split-download is gated
  on `vocal_removal=True`. Kept the gating (intentional
  bandwidth/CPU save on machines that can't run Demucs anyway).
- \[ \] **P2** Decide what should own `info.json`/subtitles when the video
  half fails. Consider letting the audio command write its own
  `info.json` so downstream isn't blocked
  (`pikaraoke/lib/youtube_dl.py:197-209`).

### US-7 Demucs on audio completion (PARTIAL)

- \[x\] ~~**P1** Emit `stems_ready` from the prewarm completion path.~~
  Done — new module-level `_ready_hook` in `demucs_processor`; prewarm
  calls it on cache hit and after a successful live separation.
  `karaoke.py` forwards it as a `stems_ready` event carrying
  `{song_basename, cache_key}` (no stream URLs — those remain
  play-time only). `now-playing-bar.js` filters by
  `now_playing_basename` so prewarm of a queued song doesn't flip
  the sliders for a different now-playing song; `splash.js` ignores
  stream_uid-less events so the audio-routing path isn't disturbed.
- \[x\] ~~**P1** Wire partial-vocals streaming from prewarm-time.~~ Done —
  prewarm now passes a throttled `progress_callback` into
  `separate_stems`, driven by a new `_progress_hook`. `karaoke.py`
  forwards ticks as `demucs_progress` events carrying
  `song_basename` so the seek-bar's buffered shading and the
  "Separating vocals… N%" chip track prewarm progress for the
  current song. Covered by `TestPrewarmHooks`.

### US-8 Lyrics lookup (RESOLVED P0)

- \[x\] ~~**P0** Fix the trigger inversion.~~ Done — chose option (b).
  `docs/USER_STORIES.md` US-8 has been rewritten to reflect that
  lyrics fetch starts at download time (cheaper signal, faster
  captions on screen) while whisper alignment waits for
  `stems_ready` internally. Pipeline implementation is unchanged.

### US-9 Subtitles ASS + word-level upgrade (PARTIAL)

- \[x\] ~~**P2** When the 120-second wait expires and whisper falls back
  to raw mix, emit a `song_warning`.~~ Done —
  `_upgrade_to_word_level` checks whether the audio path returned
  by `_wait_for_alignment_audio` is inside `demucs_processor.CACHE_DIR`.
  If not, stems weren't ready within `_STEM_WAIT_TIMEOUT_S`, so it
  emits a `warning`-severity `song_warning` ("Aligned on raw mix")
  with the timeout in the detail body so the operator can
  correlate poor word-timing with the degraded source.

### US-10 Non-blocking parallel execution (RESOLVED P0; remaining P1)

- \[x\] ~~**P0** Emit `download_progress` SocketIO events.~~ Done — yt-dlp
  progress callbacks now emit `download_progress` (throttled to one
  event per integer percent change) with title/url/user/progress/
  speed/eta/status. Forwarded to clients in `karaoke.py:391-397`.
- \[x\] ~~**P1** Move LRCLib + iTunes lookup off the sync listener.~~
  Done (option b) — `Karaoke._dispatch_lyrics_fetch_async` wraps
  `lyrics_service.fetch_and_convert` in a daemon thread named
  `lyrics-fetch-<basename>` so the download worker returns
  immediately and can pick up the next queued song instead of
  blocking up to ~10s on LRCLib/iTunes HTTPS.

______________________________________________________________________

## Splash Progress and Errors

### US-11 Splash notifications per stage (PARTIAL)

- \[x\] ~~**P1** Add the missing stage notifications.~~ Done —
  `"Downloading audio: <title>"` fires in `_execute_download` right
  before `_run_split_download` (split mode only; the merged path
  already had one unified toast). `"Separating vocals: <title>"`
  fires from `_prewarm_audio_sibling` after the .m4a is resolved
  and before Demucs starts. `"Fetching lyrics: <title>"` and
  `"Aligning words: <title>"` fire from a new
  `LyricsService._emit_stage_notification` helper at the top of
  `_do_fetch_and_convert` (after the user-ASS short-circuit) and
  `_upgrade_to_word_level`. The progressive per-percent variant
  for Demucs stays out of US-11 — it's the seek-bar demucs shading
  under US-24.
- \[x\] ~~**P1** Replace the `if (sn.html()) return;` guard with a small
  queue so concurrent stage notifications aren't dropped silently.~~
  Done — `flashNotification` now pushes onto a FIFO queue and a
  `showNextFlashNotification` drainer fires the next toast as soon
  as the current one finishes its 3000ms display + 450ms fade-out.
  Adjacent duplicates dedupe at enqueue time so a burst of socket
  retries doesn't stack up.
- \[ \] **P2** Consider per-stage styling (color/icon) so stages are
  visually distinct chips rather than one shared toast.

### US-12 Splash error indicators (PARTIAL)

- \[x\] ~~**P1** Add hover/focus tooltip behavior in addition to click.~~
  Done — `#song-warning` now opens the tooltip on `mouseenter` and
  `focusin`, closes on `mouseleave`/`focusout` (unless focus moved
  inside the popover), and supports Enter/Space to toggle and
  Escape to close + return focus. `aria-expanded` flips with
  open/close state.
- \[x\] ~~**P1** Show the affected song name inside the tooltip body.~~
  Done — new `#song-warning-song-name` header above the messages
  shows the humanized basename (strips the `---<ytid>` /
  `[<ytid>]` suffix). Hides when empty.
- \[x\] ~~**P1** Render the `#song-warning` icon outside `#now-playing`
  so warnings emitted during processing are visible before the
  song actually starts.~~ Done — icon is now a top-center
  `sp-overlay` sibling of `#top-container`, independent of the
  now-playing block's `display: none`. `song_warning` handler
  auto-promotes the first incoming song to `songWarningSongKey`
  when nothing is playing yet, and idle `now_playing` updates no
  longer clear the key (preserves pre-playback warnings until a
  new song actually starts).
- \[x\] ~~**P2** Add `role="button"`, `aria-label`, and `tabindex="0"`
  to `#song-warning` for accessibility.~~ Done — added
  `role="button"`, `tabindex="0"`, `aria-haspopup="true"`,
  `aria-expanded` (toggled in JS), and `aria-label` on the icon
  plus `role="dialog"` + `aria-label` on the tooltip.

### US-13 Warnings survive until acknowledged (PARTIAL)

- \[x\] ~~**P1** Add a "dismiss" action in the control panel that clears
  warnings for a given song from the buffer, with a server-side
  event so all clients stay in sync.~~ Done — new admin-gated
  `DELETE /admin/song_warnings/<song>` route calls
  `Karaoke.dismiss_song_warnings(song)`, which filters the
  persisted buffer and broadcasts `song_warnings_dismissed`. Both
  pilot (`base.html`) and splash (`splash.js`) listen for the
  broadcast and drop the song's entries from their local buffer.
  A "Dismiss" button in `#pk-song-warning-panel` fires the DELETE;
  the button is only rendered for admin pilots. Covered by new
  tests in `TestSongWarningBuffer`.
- \[x\] ~~**P1** Don't drop warnings from the splash render when the
  next song starts.~~ Done — `handleNowPlayingUpdate` no longer
  retargets `songWarningSongKey` when the previous song still has
  unacknowledged buffered warnings; the icon holds its content
  until dismiss drops the entries, at which point the dismiss
  listener retargets to the currently-playing song so any freshly
  buffered warnings for the new song surface immediately. Pre-
  playback promotion (from US-12) still kicks in when the buffer
  is empty.

______________________________________________________________________

## Lyrics Semantics

### US-14 Lyrics source precedence (PARTIAL)

- \[x\] ~~**P1** Stop writing VTT-derived `.ass` first and overwriting
  it with LRCLib.~~ Done — `_do_fetch_and_convert` now picks the
  chosen source (LRC > VTT) before writing, so the `.ass` is
  written exactly once per run. Covered by
  `test_lrc_wins_writes_ass_exactly_once`.
- \[x\] ~~**P1** Match VTT language against the track's language.~~
  Done — `_pick_best_vtt` now takes `preferred_lang`; matching
  primary subtags beat shorter/manual in the sort key. The
  service passes `self._db_language(song_path)` which pulls from
  `songs.language`.
- \[x\] ~~**P1** Persist VTT language to `songs.language`.~~ Done —
  when VTT is the chosen source, `_persist_vtt_language` extracts
  the lang code from the filename and writes it via
  `update_track_metadata_with_provenance(..., "scanner", ...)`,
  so whisperx alignment (and subsequent VTT picks) skip audio
  detection.
- \[ \] **P2** Tighten the "auto/translation" detection — replace the
  brittle `"auto" in lang` substring check with a proper rule (e.g.
  `lang.endswith("-auto")` or `lang.endswith("-orig")`).

### US-15 Cache-correct re-alignment (PARTIAL)

- \[x\] ~~**P1** Call `ensure_audio_fingerprint` at download time.~~
  Done — new `song_downloaded` listener
  `Karaoke._ensure_fingerprint_on_download` runs
  `ensure_audio_fingerprint` on the resolved audio sibling right
  after `register_download`, before `fetch_and_convert`. A
  re-download with changed bytes now invalidates stems and auto
  `.ass` immediately instead of waiting for the first play.
- \[x\] ~~**P1** Have `_maybe_drop_stale_auto_ass` also check the audio
  sha.~~ Done — the helper now calls `ensure_audio_fingerprint`
  before `ensure_lyrics_config`, so any path into
  `fetch_and_convert` (not just the download listener) picks up
  audio-sha changes. Redundant with the download-time call but
  cheap (fast stat-match short-circuit when mtime+size agree).

### US-16 Language hint reuse (PARTIAL)

- \[x\] ~~**P1** Use the VTT language code as the fast-path language
  source; persist to `songs.language`.~~ Done — `_persist_vtt_language`
  (added for US-14) writes the VTT-derived lang into `songs.language`
  via the provenance-aware writer. `_upgrade_to_word_level`
  already reads from that column, so any future alignment pass on
  a VTT-only song (e.g. after whisperx install) skips audio
  detection and starts with the persisted hint.
- \[ \] **P2** Make `last_detected_language` thread-safe — either move it
  off the shared aligner singleton (`lyrics_align.py:47`) or guard
  it with a per-song lock so concurrent alignments don't clobber
  each other.
- \[ \] **P2** Allow correcting a stale `db_lang` — drop the
  `not db_lang` guard at `lyrics.py:296` if the new detection has
  higher confidence.

### US-17 Reprocess library on aligner install (PARTIAL)

- \[x\] ~~**P1** Track "first whisperx install" with a sentinel.~~ Done —
  added `Karaoke._maybe_initial_reprocess_with_whisperx` which
  reads `metadata["whisperx_initial_reprocess_done"]` and skips
  the full-library scan when set. The first successful run (or
  any startup where the aligner is available) stamps the flag so
  restarts no longer re-walk every song. Freshly-downloaded
  songs take the word-level path during their own
  `fetch_and_convert` run, so the sentinel doesn't regress their
  behavior.
- \[ \] **P2** When upgrading existing line-level `.ass`, prefer reading
  the existing `.ass` text over re-fetching LRCLib via the filename
  (`lyrics.py:377-398`). Songs whose title/artist drifted are
  currently skipped silently.

### US-43 Lyrics source transparency (PASS)

- \[x\] ~~**P2** Surface which source produced the current subtitles so
  the user can tell an LRCLib line-level render from a Whisper ASR
  fallback at a glance.~~ Done — `PlaybackController.lookup_lyrics_source`
  (`lib/playback_controller.py:105`) reads `songs.lyrics_source`; the
  value rides on `/now_playing` as `now_playing_lyrics_source` and is
  rendered in the splash as a compact badge (`#lyrics-source`,
  `templates/splash.html`) with colour variants for user-authored,
  curated (LRCLib / Genius / YouTube VTT / word-level `whisperx`), and
  auto-generated (`whisper`). `updateLyricsSourceBadge`
  (`static/js/splash.js:86`) handles the state flip, and
  `LYRICS_SOURCE_LABELS` is the single source of truth for label text.
- \[x\] ~~**P2** Refresh the badge mid-playback when the source changes
  (word-level alignment completes, Whisper fallback lands after the
  song has already started).~~ Done — `_register_ass` now emits
  `lyrics_upgraded` unconditionally (`lib/lyrics.py:168`), and
  `Karaoke._on_lyrics_upgraded` (`karaoke.py:1059`) re-runs
  `lookup_lyrics_source` + pushes a socket update so the badge reflects
  the latest provenance without a reload. `_register_user_ass`
  (`lib/lyrics.py:202`) stamps `lyrics_source="user_ass"` too, so
  manually placed Aegisub files are distinguishable from the auto
  pipeline.
- \[x\] ~~**P1** Reject LRCLib when its detected language disagrees
  with the DB's `songs.language` — the dub trap (e.g. LRCLib returning
  the English "Colors of the Wind" for Edyta Górniak's Polish "Kolorowy
  wiatr").~~ Done — `_is_lrc_language_mismatch` (`lib/lyrics.py:495`)
  compares primary subtags (`pl` ≡ `pl-PL`); mismatches drop the LRC
  and fall through to Genius / VTT / Whisper. Covered by
  `TestLyricsServiceLanguageMismatch` in `tests/unit/test_lyrics.py`.

______________________________________________________________________

## Playback

### US-18 Play a queued song (PARTIAL)

- \[x\] ~~**P2** Cache `can_serve_video_directly` results per file.~~
  Done — `file_resolver._probe_codecs` now memoizes on
  `(path, size, mtime_ns)`, so `can_serve_video_directly` /
  `can_serve_directly` stop shelling to ffprobe on repeat plays.
  Cache is bounded (2048 entries, FIFO eviction) and invalidates
  automatically when the file changes on disk.
- \[ \] **P2** Document that a non-aac audio track on h264 video still
  triggers an ffmpeg audio pipe (`stream_manager.py:230`); strictly
  speaking that's a transcode, even on the "direct video" path.

### US-19 Skip, pause, restart (PARTIAL)

- \[x\] ~~**P1** Make `karaoke.restart()` reset the server-side position
  to 0 and broadcast a `seek` to 0.~~ Done — restart zeros
  `playback_controller.now_playing_position` / `position_updated_at`
  and emits `socketio.emit("seek", 0.0)` so every splash (not just
  the click origin) rewinds, even if its client-side handler is
  slow or disconnected.
- \[ \] **P2** Decide whether server-side pause should also pause the
  ffmpeg subprocess (SIGSTOP/SIGCONT) on the transcoded paths, or
  whether client-only pause is sufficient.

### US-20 Queue management — PASS

No action.

______________________________________________________________________

## Now-Playing Panel

### US-21 Now-playing display (PARTIAL)

- \[x\] ~~**P1** Add an `artist` field to now_playing and render it.~~
  Done — `PlaybackController.play_file` looks up the artist via
  `db.get_song_by_path()` on song start, exposes it as
  `now_playing_artist` in `get_now_playing()`, and the expanded
  player template + `now-playing-bar.js` render it under the title
  using the `.pk-player-artist` CSS class. Hidden when empty so
  rows without a canonical artist don't leave a blank line.

### US-22 Volume: single vs. dual sliders (PARTIAL)

- \[ \] **P2** Persist a "stems enabled this session" flag on the
  front-end so a `now_playing` poll arriving just after
  `stems_ready` doesn't flip the sliders back
  (`pikaraoke/static/now-playing-bar.js:200-223`).

### US-23 Seek bar with buffering progress (PARTIAL)

- \[x\] ~~**P1** Emit `ffmpeg_progress` on the non-HLS MP4 path.~~ Done —
  added an `on_line` callback hook on the shared stderr reader
  (`enqueue_output`) and a `_mp4_progress_line_handler` that
  parses ffmpeg's `time=HH:MM:SS.MS` status lines. HLS keeps its
  segment-count monitor; MP4 now drives the seek-bar from
  ffmpeg's own reported position. Throttled to one emit per
  integer second.
- \[x\] ~~**P2** Initialize `seekBufferedDemucs` to a conservative
  ceiling before the first `demucs_progress` tick.~~ Done — when
  `vocal_removal` is on and stems aren't live yet, render seeds
  `seekBufferedDemucs = 0` (and the processing chip at 0%). The
  slider now starts at zero-amber and fills as real ticks arrive,
  instead of painting fully amber because a null ceiling meant
  "unrestricted" to the clamp helper.

### US-24 Visual cues for processing progress (PARTIAL)

- \[x\] ~~**P1** Add a separate processing indicator in the now-playing
  panel next to the seek bar.~~ Done — new
  `[data-pk-processing]` chip sits directly above the seek bar in
  the expanded player (`#pk-player-full`). `setProcessingIndicator`
  shows "Separating vocals… N%" while Demucs is in flight, hides
  on the first `stems_ready` and on render when `demucs_processed`
  has caught up to `demucs_total`. A small CSS spinner keeps the
  cue visible even when the percent delta between ticks is tiny.

______________________________________________________________________

## Splash Screen

### US-25 Captions rendering (PARTIAL)

- \[x\] ~~**P2** Emit a `song_warning` (severity `info`) when `librosa`
  is missing and the BPM pulse is silently disabled.~~ Done —
  new `LyricsService._warn_once_if_bpm_disabled` fires on the
  first song that tries to estimate BPM and gets `None` back
  because librosa's import failed. Uses a module-level sentinel
  so a missing install-level dep logs one "Caption pulse
  disabled" (info) warning per process, not per song.
- \[ \] **P2** Document (or fix) that `_estimate_bpm` may run on the raw
  mix when stems aren't ready in time (`lyrics.py:300`).

### US-25b Sub-word karaoke precision — PASS

Shipped. Renderer emits one `\kf` per character on the WhisperX path
(real wav2vec2 CTC timings) and one `\kf` per syllable on the
Whisper-ASR fallback path (pyphen split, duration sliced
proportionally to syllable char length). Monosyllabic words and
unsupported languages fall back to a single per-word `\kf` - never
worse than the pre-feature baseline. Model id bumped to
`wav2vec2-char` so cached per-word `.ass` files regenerate.
Covered by `tests/unit/test_lyrics_align.py::TestCharAlignmentExtraction`,
`TestPartsForRef`, and `tests/unit/test_lyrics.py::TestSyllabify`,
`TestSyllableParts`, `TestKToken` part cases.

### US-26 Now-playing overlay and QR — PASS

No action.

### US-27 User-action introduction screen (PARTIAL)

- \[x\] ~~**P1** Replace the brittle autoplay test.~~ Done — the test
  now calls `video.play()` on an audible-but-near-silent clip and
  catches `NotAllowedError` (the spec signal for a blocked autoplay
  policy). Removed the muted-then-unmuted timing dance that
  false-positived when the browser kept the element muted.
- \[x\] ~~**P2** Graceful fallback when the test asset is missing.~~
  Done — missing asset no longer shows the modal; it logs a warning
  and proceeds (a missing file isn't the same signal as a blocked
  policy, and every deployment without the mp4 was otherwise
  stuck with a click-through modal).

______________________________________________________________________

## Database as Source of Truth

### US-28 Track metadata persisted in DB (RESOLVED P0; remaining P2)

- \[x\] ~~**P0** Add provenance tracking for canonical metadata.~~ Done —
  migration V4 adds a `metadata_sources` JSON column on `songs`
  ({field: source}). New helpers `update_track_metadata_with_provenance`
  and `get_metadata_sources` on `KaraokeDatabase`.
- \[x\] ~~**P0** Implement confidence-based override in `song_enricher`.~~
  Done — `METADATA_SOURCE_CONFIDENCE` ladder
  (musicbrainz > itunes > youtube > scanner; manual on top).
  `_MEDIA_SOURCE_CONFIDENCE` separately ranks YouTube above remote
  DBs for media-specific fields (`duration_seconds`, `source_url`).
  All callers (`song_enricher`, `library_scanner`, `song_manager`,
  `lyrics`) now go through the provenance-aware method.
- \[ \] **P2** Populate `year` and `variant` columns in the enricher.
  Currently in schema but never written.

### US-29 Artifact registry (PARTIAL)

- \[x\] ~~**P1** Unregister `info_json` and `vtt` artifact rows.~~ Done —
  `_cleanup_yt_subs_and_info` now accepts the DB, looks up the
  song_id, and calls `db.delete_artifacts_by_role(song_id, "vtt")`
  / `"info_json"` right after unlinking the files. LyricsService
  passes `self._db` at all three call sites, so the DB no longer
  keeps ghost rows for files that were cleaned up post-convert.
- \[ \] **P2** Re-register the audio sibling `.m4a` after split downloads
  finish; `discover_song_artifacts` (`song_manager.py:45-83`) only
  finds it if it exists at registration time.
- \[ \] **P2** Make `LibraryScanner` re-evaluate songs that have a
  partial artifact set (not just zero rows;
  `karaoke_database.py:186`).

### US-30 Cache-file fingerprints (PARTIAL)

- \[x\] ~~**P1** Add `sha256`, `size`, `mtime` columns to
  `song_artifacts`.~~ Done — V5 migration adds the three nullable
  columns. New `KaraokeDatabase.update_artifact_fingerprint`
  persists per-file fingerprints; new
  `audio_fingerprint.ensure_artifact_fingerprint` mirrors the
  cheap-stat / recompute-on-change flow used for audio. `get_artifacts`
  now selects the new columns so consumers see them.
- \[ \] **P2** Apply the same fingerprint flow to stems directory and
  `.ass` outputs.

### US-31 Waterfall invalidation (RESOLVED P0; remaining P1)

- \[x\] ~~**P0** Clear `lyrics_sha` on audio sha change.~~ Done —
  `_invalidate_auto_ass` now also clears `lyrics_sha` and
  `aligner_model` via `update_processing_config`. Next pipeline run
  treats LRCLib as never-fetched and re-queries it. Covered by new
  tests in `tests/unit/test_audio_fingerprint.py::TestLyricsShaClearedOnAudioChange`.
- \[x\] ~~**P1** Make sure `ensure_stems_config` also invalidates the
  aligned `.ass`.~~ Done — `ensure_stems_config` now calls
  `_invalidate_auto_ass` after `_invalidate_stems` when the
  demucs_model changed. Whisper alignment runs on stem output, so
  a new separator means the old `.ass` is aligned to the wrong
  input.
- \[x\] ~~**P1** Run `ensure_audio_fingerprint` at download time.~~
  Done — see US-15 above (new `song_downloaded` listener).

### US-32 Cache cleanup on song delete (PARTIAL)

- \[ \] **P2** Replace `os.path.basename(path)` as the sha extraction
  (`song_manager.py:178`) with reading the sha from the DB row to
  avoid trailing-slash fragility.
- \[ \] Resolves automatically once US-29 ghost-row issue is fixed.

______________________________________________________________________

## Caching and Storage

### US-33 Stems cache — PASS

No action.

### US-34 Atomic writes (PARTIAL)

- \[x\] ~~**P1** Make `_merge_metadata_into_info_json` atomic.~~ Done —
  writes to `info_path + ".part"` then `os.replace`; cleans up the
  tempfile on OSError so the original is never truncated.
- \[x\] ~~**P2** Make `PreferenceManager.set` atomic: tempfile +
  `os.replace`.~~ Done — `set()` now writes to
  `config.ini.part` and calls `os.replace` to swap it over the
  live file. A mid-write crash can no longer leave a truncated
  config.ini. Partial files from a failed replace are cleaned up
  in the exception handler.

______________________________________________________________________

## Settings and Preferences

### US-35 Preferences persist across restarts (PARTIAL)

- \[x\] ~~**P1** Add `download_path`, `youtubedl_proxy`, and
  `preferred_language` to `PreferenceManager.DEFAULTS`.~~ Done —
  added as string defaults (empty = unset), `apply_all` now
  persists CLI values for them on startup so the UI can read them
  back via `get_or_default`. `reset_all` skips `download_path` /
  `youtubedl_proxy` so admin "reset preferences" doesn't blank
  the live runtime paths.

### US-36 Toggle vocal removal — PASS

No action.

______________________________________________________________________

## Admin and Diagnostics

### US-37 Manual library sync — PASS

No action.

### US-38 Download error surface (PARTIAL)

- \[x\] ~~**P1** Persist `download_errors` to DB.~~ Done — stored as a
  JSON blob under `metadata["download_errors"]`; `DownloadManager`
  loads on init and flushes on every append/remove. Survives
  restart.
- \[x\] ~~**P1** Admin-gate the dismiss endpoint.~~ Done — the
  `DELETE /queue/downloads/errors/<id>` route now returns 403 for
  non-admins.
- \[x\] ~~**P2** Add a `timestamp` field to each error dict.~~ Done —
  new errors carry `timestamp: time.time()` (epoch seconds) for
  diagnostics.

### US-39 Structured warnings (PARTIAL)

- \[x\] ~~**P1** Use real severity levels at emission sites.~~ Done —
  `StreamManager._emit_song_warning` now takes a `severity` kwarg,
  and "Audio extraction failed" (the only unrecoverable case)
  emits with `severity="error"`. Degraded-but-functional paths
  (Demucs fallback, MP3 encode, lyrics misses, word-level align
  failure) stay on `"warning"`.
- \[x\] ~~**P1** Persist `song_warning` events + admin view.~~ Done —
  Karaoke keeps a rolling 200-entry buffer persisted to
  `metadata["song_warnings"]`; every `song_warning` event carries
  a `timestamp` and is appended. New routes
  `GET/DELETE /admin/song_warnings` (admin-gated) surface and
  clear the log.
- \[x\] ~~**P1** Bridge `download_errors` and `song_warning`.~~ Done —
  after appending to `download_errors`, `DownloadManager` emits a
  mirrored `song_warning` with `severity="error"` so the two
  surfaces stay in sync.

______________________________________________________________________

## Multi-device Playback

### US-40 Pilots as playback satellites (NOT STARTED)

Whole story is unbuilt: no satellite wiring in `karaoke.py`,
`playback_controller.py`, or any pilot JS. Call out as its own
implementation block rather than a sprinkle of P1s.

- \[ \] **P0** Define the satellite sync contract. A satellite pilot needs
  to know, for the currently-playing song: media URL (stream or
  direct mp4), stems URLs (`vocals`/`instrumental`) when ready,
  target playhead position, play/pause state, and per-pilot mix
  state. Either extend `now_playing` with a `for_satellites` block
  or introduce a dedicated `satellite_state` event — both need a
  design decision and test coverage before any client work.
- \[ \] **P0** Server-side opt-in registry. Each pilot that toggles
  satellite-on declares itself (socket room, client_id). Server
  tracks which pilots are live satellites so it can broadcast
  per-satellite stem volume / drift corrections without leaking
  them to silent pilots. Needs persistence across pilot reconnect
  (short-lived TTL is fine).
- \[ \] **P0** Drift correction loop. Satellite audio must stay within
  ~200 ms of the splash playhead. Piggyback on the existing
  `playback_position` broadcast; satellite client compares its
  `audio.currentTime` against the reported position and nudges
  (small `playbackRate` adjustments for \<500 ms drift, seek for
  anything larger). Document the exact tolerance in this file so
  regressions have an anchor.
- \[ \] **P1** Per-pilot mix state. Satellite exposes its own volume (or
  vocal + instrumental sliders once stems are ready) that's
  independent of the splash mix. New routes + socket events:
  `satellite_volume`, `satellite_stem_volume`. Must not collide
  with the existing global `stem_volume` broadcast (which drives
  the splash + every non-satellite pilot).
- \[ \] **P1** "No second splash" guardrails. A satellite pilot renders
  only the audio pipeline — no captions, no QR, no intro overlay,
  no seek-bar scrubbing (view-only). Shared UI components
  (`splash.js`'s SubtitlesOctopus, now-playing overlay, QR) must
  be gated behind an `isSatellite` flag or split into a dedicated
  satellite template so new features don't accidentally leak.
- \[ \] **P1** Shared queue / shared transport. Satellite listens to the
  same play/pause/skip/restart events as any other client.
  Importantly, any pilot (including a satellite) can still drive
  the queue — the satellite flag is strictly "this device also
  plays audio", not "this device is read-only".
- \[ \] **P1** Stems sync. When `stems_ready` fires for the current song,
  every active satellite crossfades to stems on its own
  AudioContext. Reuse `splash.js`'s stem routing code (factor out
  into a shared module — see refactor below) so we don't fork two
  copies of the Web Audio graph.
- \[ \] **P1** Refactor shared audio setup. Extract `splash.js`'s Web
  Audio stem-routing + crossfade logic into a module that both
  the splash and a new `satellite.js` can import. Today it's
  ~500 lines of tightly-coupled callbacks in `splash.js`; forking
  it for the satellite path would double the maintenance cost.
- \[ \] **P2** Satellite autoplay policy. Every satellite needs the same
  user-gesture unlock dance as the splash (US-27). Reuse
  `testAutoplayCapability` but gate the UI so the toggle to
  enable-satellite itself counts as the gesture.
- \[ \] **P2** Visibility indicator on the now-playing panel: show "N
  satellites active" so an operator can see when phones are
  pulling audio. Low-priority diagnostic.
- \[ \] **P2** Reconnect semantics. When a satellite pilot reconnects
  mid-song, it must resume playback at the current position with
  no gap + crossfade (don't silent-restart from zero). Document
  and test the cold-join flow separately from the opt-in flow.

**Open questions before starting:**

1. Do we bind satellite audio to the same MediaSource stream the splash
   uses (one encode, many readers), or does each satellite get its own
   `/stream/<uid>/...` subscription? The former is cheaper; the latter
   is simpler to reason about w.r.t. per-client position.
2. How do we handle a satellite whose playhead is *ahead* of the
   splash (e.g. splash paused for a buffering hiccup)? Pause
   satellites too, or just let them coast and resync on resume?
3. Should per-pilot stem volumes persist across songs (preference) or
   reset each song (ephemeral)? Affects `PreferenceManager` schema.

### US-41 Settings panel (PARTIAL)

The existing `/info` page (`info.html`) already renders preferences
alongside system info — rather than stand up a second page, US-41 is
being closed by enriching that surface. Remaining P2s are genuine
polish rather than architectural gaps.

- \[x\] ~~**P1** Render the full preference set in a single admin
  surface.~~ Already landed under `/info` (`pikaraoke/routes/info.py`,
  `pikaraoke/templates/info.html`), now extended with
  `download_path` and `youtubedl_proxy` text rows so the CLI-path
  prefs are visible in the UI rather than config-file-only. Saves
  via the existing `/change_preferences` route; already broadcasts
  `preferences_update` to keep every pilot in sync.
- \[x\] ~~**P1** GPU-aware vocal-splitter default.~~ Already landed in
  `PreferenceManager.DEFAULTS["vocal_removal"] = has_torch_gpu()`
  (`preference_manager.py:51`). US-41 adds the matching UI hint
  "Default: on when a GPU is detected (<backend> active)" under
  the toggle so the default is explainable without reading source.
- \[x\] ~~**P1** Runtime version readout.~~ Done — new
  `get_library_versions()` in `get_platform.py` uses
  `importlib.metadata` to resolve whisperx + demucs at render
  time (no torch import for a version number). Rendered in the
  System `<dl>` on `/info`; missing packages show "not installed".
  Covered by `TestGetLibraryVersions`.
- \[x\] ~~**P2** Accelerator backend readout.~~ Done —
  `get_accelerator_backend()` returns
  `{backend: CUDA|MPS|CPU|none, detail}`; surfaced as a new
  "Accelerator" row in the System `<dl>` and referenced in the
  vocal-removal default hint. Covered by
  `TestGetAcceleratorBackend`.
- \[x\] ~~**P2** "Reset preferences" button.~~ Already present on
  `/info` (`#pk-clear-prefs`), backed by the existing
  `PreferenceManager.reset_all` which skips `download_path` /
  `youtubedl_proxy` per the US-35 work. No work needed.
- \[ \] **P2** Inline validation for `download_path` (must exist + be
  writable) and `youtubedl_proxy` (parseable URL). Currently the
  text fields accept anything; a bad value silently fails on next
  download. Add a lightweight server-side check in
  `change_preferences` plus inline error rendering.
- \[ \] **P2** Server-side test for the `/info` route — a smoke test
  that asserts `library_versions` and `accelerator` are populated
  in the template context (non-admin gets public fields only;
  admin gets the full preference set). Current tests cover the
  helpers but not the route wiring.

### US-42 Persistent pilot name (PASS)

The legacy `"user"` cookie was being silently evicted by Safari ITP's
7-day script-writable-cookie cap (and by aggressive cookie purges on
other browsers), so pilots kept losing their display name between
sessions. Pilot name now lives in `localStorage`, which is not subject
to that cap.

- \[x\] **P0** Persist pilot name in `localStorage` under
  `pk-pilot-name` (mirrors the existing `pk-theme` pattern in
  `pikaraoke/static/js/theme.js`).
- \[x\] **P0** One-time migration: read the legacy `"user"` cookie on
  first load, copy to `localStorage`, then `Cookies.remove("user")`
  so there is a single source of truth.
- \[x\] **P1** All call sites read/write through the new
  `getPilotName` / `setPilotName` helpers in `base.html`. Renamed
  from `getUserCookie` / `setUserCookie` and updated in
  `spa-navigation.js`, `search.html`, `files.html`, and `splash.js`
  in the same commit (no half-migration).
- \[x\] **P1** Backend contract unchanged: `song_added_by` form field
  and `user=` query param on `/enqueue` still carry the name as
  before; no changes to `routes/queue.py`, `routes/search.py`, or
  `lib/queue_manager.py`.

______________________________________________________________________

## Lyrics Timing and Language Safety

### US-43 Fast-path synced lyrics + language-safe re-alignment (PARTIAL)

Timing side is largely in place (US-8 + US-9 already dispatch LRC fetch
off the `song_downloaded` event and gate whisperx on `stems_ready`).
The language-safety side has a real hole: the dub-trap guard in
`ab066fef` is ineffective on the first download of a cold-DB track,
because there is no pre-LRC signal to compare the LRC's language
against — so LRC is trusted, whisperx aligns it, and the LRC's own
language is then persisted as `songs.language` with provenance
`scanner`. Every subsequent run passes the guard trivially (DB and
LRC agree), so the bad state is sticky.

Evidence: Edyta Górniak's Polish "Kolorowy wiatr" (song_id 33) is
currently in exactly this state — DB `language='en'`, `.ass` contains
the English "Colors of the Wind" lyrics, whisperx aligned those English
words to the Polish vocals. Files on disk:
`/Users/zygzagz/.pikaraoke/songs/Edyta Gorniak - „Kolorowy wiatr' …---UityBuZoXv0.{ass,m4a,mp4}`.

Root cause of the English text is **upstream data poisoning, not our
bug alone**: LRCLib record id `10005773` (and its duplicate
id `24859990`) is labelled "Kolorowy Wiatr / Edyta Górniak /
Pocahontas Original Soundtrack" but its `plainLyrics` /
`syncedLyrics` fields are the English "Colors of the Wind" text.
Query `GET https://lrclib.net/api/get?artist_name=Edyta%20Gorniak&track_name=Kolorowy%20Wiatr`
to reproduce. LRCLib doesn't return a language field, so we cannot
detect the mislabel from the HTTP response — the only defence is the
title/artist-derived language hint we're not currently computing
before the LRC call.

**Accepted design: tiered classifier-gated LRC acceptance.**
Tier 1 text-consensus runs on data we already have (yt-dlp info.json,
cached iTunes/MusicBrainz responses, langdetect on DB title+artist)
at `song_downloaded` time — zero new HTTP and well under the 50ms
per-song fast-path budget. Tier 2 adds a Whisper language-ID probe on
the raw audio when Tier 1 can't reach >=2-signal consensus, and a
re-probe on the vocals stem once `stems_ready` fires (optionally
overwriting `songs.language` and invalidating the `.ass` if the
cleaner stem disagrees with the raw-audio probe). Tier 3 is a
token-overlap Whisper ASR pass for same-language mislabels and runs
only on Tier-1 consensus + Tier-2 tiebreaker ambiguity. All probes
run on files already on disk; no new network endpoints anywhere.

- \[x\] ~~**P0** Provenance ladder split.~~ Landed in
  `69e1b847 feat(lyrics): provenance ladder split for US-43 language classifier`. `METADATA_SOURCE_CONFIDENCE` in
  `karaoke_database.py` now carries 16 language-specific rungs
  (`lrc_heuristic` below `scanner`; `itunes_country` →
  `whisper_probe_stems` above; `manual` on top). The post-LRC and
  post-Genius langdetect writes (`lyrics.py:643`, `:777`) moved
  from `scanner` to `lrc_heuristic`; the post-Whisper-ASR write
  (`:843`) moved to `whisper_asr`. VTT filename lang code stays at
  `scanner`. Covered by `TestLanguageProvenanceLadder` in
  `test_karaoke_database.py`.
- \[x\] ~~**P0** Tier 1 text-consensus classifier.~~ Landed in
  `cba41dc6 feat(lyrics): Tier 1 text-consensus language classifier (US-43)`. New `pikaraoke/lib/lyrics_language_classifier.py`
  collects up to eight signals — `yt_info_lang`,
  `yt_subtitle_lang`, `yt_title_lang`, `itunes_text`,
  `itunes_country`, `mb_release_titles`, `mb_release_country`,
  `title_heuristic` — and applies the design-doc consensus rule
  (`>=2 agreeing primary subtags → persist under the highest-ranked agreeing source`). Disagreement leaves the DB alone and the
  row falls through to Tier 2 acoustic probe. Wired into
  `LyricsService._do_fetch_and_convert` ahead of the LRC fetch
  so `_is_lrc_language_mismatch` has ground truth on cold-DB
  first runs. Every signal + the consensus decision log at INFO
  under the `US-43` tag. iTunes/MB queries share the enricher's
  LRU caches (zero net new HTTP). Covered by 38 tests in
  `test_lyrics_language_classifier.py` plus the
  `test_classifier_seeds_language_before_lrc_fetch` integration
  case in `test_lyrics.py`.
- \[x\] ~~**P0** Tier 2a Whisper raw-audio language probe.~~ Landed in
  `cf1d9279 feat(lyrics): Tier 2a Whisper language-ID probe on raw audio (US-43)`.
  New `pikaraoke/lib/lyrics_audio_probe.py` runs a 30-second
  `faster_whisper.WhisperModel.detect_language` pass at 50% of
  track duration; low-confidence (\<0.5) results re-probe at 30%
  and only accept when both windows agree on the same primary
  subtag (cheap instrumental-heavy guard). Reuses the
  faster-whisper singleton from `lyrics._get_whisper_model`
  (the `WhisperXAligner` in `lyrics_align.py` only loads
  wav2vec2 — no whisper model to share). Cached per
  `audio_sha256` in `db.metadata` as
  `whisper_probe_raw:<sha>` JSON; negative verdicts cache too
  so repeat boots don't re-pay. Wired into
  `LyricsService._run_language_classifier` to fire only when
  Tier 1 returns no consensus. Persists under `whisper_probe_raw`
  (rung 22). 19 tests in `test_lyrics_audio_probe.py`.
- \[x\] ~~**P0** Tier 2b Whisper vocals-stem re-probe.~~ Landed in
  `6a957da7 feat(lyrics): Tier 2b whole-song language-ID probe on vocals stem (US-43)`.
  `probe_language_whole_song` runs the full vocals stem through
  `detect_language(vad_filter=True, language_detection_segments=20)` —
  VAD concatenates every sung chunk, then probabilities are
  averaged across 6-10 mel segments for a 3-5 min song.
  Fires inside `_upgrade_to_word_level` right after
  `_wait_for_alignment_audio` resolves, only when real stems
  are present (not the raw-mix timeout fallback). Decision rule
  from the design doc: agreement bumps provenance to
  `whisper_probe_stems` (rung 23); disagreement overwrites
  `songs.language`, invalidates `.ass` + `lyrics_sha` +
  `aligner_model` via `audio_fingerprint._invalidate_auto_ass`,
  and aborts the current alignment pass so wav2vec2 doesn't burn
  cycles on the wrong-language model. `manual` rung (100) blocks
  the flip. Shared cache helpers with 2a (distinct prefix).
  12 additional tests in `test_lyrics_audio_probe.py`.
- \[x\] ~~**P2** End-to-end observability pass.~~ Landed in
  `6c55804e chore(lyrics): end-to-end observability across the subtitle pipeline`.
  Pipeline entry + terminal "source=X db_lang=Y" summary logs
  in `_do_fetch_and_convert`; metadata-read log; LRCLib hit/miss;
  VTT picker decision; Genius query/hit/miss; wav2vec2 align
  start/done with word count + elapsed (previously zero log
  lines in `lyrics_align.py`); basename↔sha bridge at Tier 2a/2b
  entry so one grep joins probe-internal sha-keyed logs with the
  rest of the pipeline's basename-keyed logs.
- \[x\] ~~**P0** Re-dispatch pipeline after Tier 2b flip.~~ Caught
  during manual Kolorowy wiatr testing: after 2b flipped `en -> pl`
  and deleted the `.ass`, subsequent playbacks stayed caption-less
  because the "next pipeline pass" the design relied on only fires
  on `song_downloaded`, which never re-fires for an
  already-downloaded row. `_run_tier2b_probe` now spawns
  `fetch_and_convert` in a daemon thread right after invalidation,
  closing the feedback loop: DB is now `pl`, the second pass
  rejects the English LRC via `_is_lrc_language_mismatch`, and
  falls through to Genius / VTT / Whisper ASR. The 2b probe on the
  second pass hits the per-sha cache and agrees, so no loop.
  Test: `test_disagreement_flips_language_and_invalidates_ass`
  now asserts the re-dispatch; `test_agreement_bumps_provenance_no_invalidation`
  asserts no re-dispatch on agreement.
- \[ \] **P0** Tier 2 safety net when Tier 1 consensus is split or
  stems miss. Pocahontas "Kolorowy wiatr" re-download on a slow MBP
  exposed the gap: Tier 1 returned `en 3/5` (iTunes album blob +
  country + MB titles langdetected to `en` outweigh the two Polish
  signals), so Tier 2a never ran. Demucs then missed its 120s
  budget, alignment fell back to raw mix, and Tier 2b is gated on
  `audio_path.startswith(_CACHE_DIR)` at `lyrics.py:936` so it
  silently skipped too. No acoustic probe fired at all; DB stayed
  `en`; re-fetch never re-triggered. Two candidate fixes:
  - Preferred: trigger Tier 2a whenever Tier 1 agreement is
    \< unanimous (e.g. 3/2 or 4/1), not only on no-consensus. One
    extra `detect_language` call on ambiguous songs, zero on
    unanimous ones. Doesn't depend on stems timing.
  - Or: run a Tier 2b-equivalent whole-song probe on the raw mix
    inside the raw-mix fallback branch of `_upgrade_to_word_level`.
    Higher CPU cost per miss, but catches the stems-too-slow path
    the preferred fix still leaves uncovered on unanimous-but-wrong
    Tier 1 runs (rare).
- \[x\] ~~**P0** Stems `/stream/<hash>/{vocals,instrumental}.wav` 404
  with no audio fallback.~~ Root cause: the per-song separation
  coordinator (`_sep_handles` / `_sep_done_keys` in
  `demucs_processor.py`) was keyed by the audio file *path*, not its
  content hash. Pocahontas repro: queue-prewarm at 11:50:50 hit the
  mp3 cache, ran `release_separation(m4a_path, success=True)`
  — which added the m4a path to `_sep_done_keys`. After the user
  deleted + re-downloaded the file (different bytes, same path), the
  post-download prewarm's `acquire_separation(m4a_path)` returned
  the stale "done" handle with both events already set, so `_run`
  skipped separation entirely. `_attach_to_inflight_separation`
  then registered stem URLs pointing at `<new_sha>/vocals.wav.partial`
  that nobody was ever going to write — splash 404'd after the 15s
  grace period. Fixed by switching the coordinator to key on
  `cache_key` (content SHA) throughout. Delete + re-download now
  computes a different key and correctly claims fresh ownership.
  Regression test:
  `test_distinct_content_at_same_path_does_not_share_done_state`.
- \[ \] **P0** Widen LRCLib candidate list. Upgrade
  `_fetch_lrc_with_itunes_fallback` to return **all** candidates
  from `/api/search` (not just `/api/get`'s single possibly
  mislabelled hit). Rank by classifier score; on all-reject fall
  through to Genius / VTT / Whisper ASR per US-14.
- \[ \] **P0** Tier 3 same-language mislabel token overlap. When
  `language_match=True` but two candidates disagree on text, run
  a 15-30s Whisper ASR pass on the probe window and score token
  overlap against the first 30s of each LRC. Pick the higher-
  overlap candidate; reject all if none clears the threshold.
  Raw-mix WER separates right-lyrics-vs-wrong-lyrics cleanly at
  ~30%.
- \[ \] **P1** Heal already-poisoned rows. On startup (behind the
  existing `whisperx_initial_reprocess_done` sentinel in
  `Karaoke._maybe_initial_reprocess_with_whisperx`), re-run the
  probe for every song whose `songs.language` was written under
  `scanner` / `lrc_heuristic` / `whisper_asr` provenance. If
  the probe disagrees, overwrite (higher-rung now wins), clear
  `lyrics_sha` + `aligner_model`, delete the `ass_auto`
  artifact, and let the next playback trigger a clean re-fetch.
  One song at a time so CPU/GPU isn't thrashed.
- \[ \] **P1** Manual remediation for Kolorowy wiatr (song_id 33)
  while the above is in flight: delete the `.ass`, clear
  `songs.language` + `songs.lyrics_source` + `songs.lyrics_sha` +
  `songs.aligner_model`, and replay. With Tier 1 landed the
  replay should now seed `pl` from the iTunes/MB consensus even
  without the Whisper probe; verify before closing out.
- \[ \] **P2** Regression tests anchored on the Kolorowy wiatr
  signature:
  - \[x\] Stub LRCLib to return the English `10005773` record for
    a cold-DB Polish song; Tier 1 classifier establishes `pl`
    from iTunes/MB fixtures, the existing dub-trap guard rejects
    the LRC. Covered by
    `TestLyricsServiceLanguageMismatch::test_classifier_seeds_language_before_lrc_fetch`.
  - \[ \] Stub two LRCLib candidates in the same language, one
    matching the audio and one not; assert Tier 3 token overlap
    picks the correct one.
  - \[ \] Seed an already-poisoned DB row (`language='en'`,
    provenance `scanner` or `lrc_heuristic`) and assert the
    startup heal pass flips it to `pl` after the Whisper probe
    runs.
- \[x\] ~~**P2** Decide whether raw-mix probe results are re-validated
  once stems land.~~ Resolved by Tier 2b landing above — every 2a
  verdict is re-validated against the isolated vocals stem, and
  disagreement triggers a language overwrite + `.ass` invalidation
  so the next pipeline pass re-fetches LRC in the corrected
  language.

______________________________________________________________________

## Priority Roll-Up

**P0 (architectural / story-breaking):** all original P0s resolved (see
"Completed P0s" below). Three new P0s open under **US-40 satellites**
(sync contract, server-side registry, drift loop) — these are
new-feature architectural decisions rather than regressions.

**P1 (significant gaps):** US-7 and US-41 closed. US-40 owns the bulk
of the remaining P1 scope (per-pilot mix, shared-queue transport,
stems sync, splash refactor, satellite guardrails).

**P2 (polish/robustness):** ASS pulse `librosa` warning, ffprobe caching,
queue-blocks-delete documentation, settings-panel validation + route
smoke test, etc.

______________________________________________________________________

## Completed P0s

| Story | What landed | Files touched |
|---|---|---|
| US-2 / US-3 | Pre-download cache short-circuit in `queue_download`: when `find_by_id` finds the video on disk we emit `song_downloaded` (and optionally enqueue) without invoking yt-dlp. | `pikaraoke/lib/download_manager.py`, `tests/unit/test_download_manager.py` |
| US-8 | Story rewritten to match the better implementation: lyrics fetch fires at download time so captions appear sooner; whisper alignment alone waits for `stems_ready`. | `docs/USER_STORIES.md` |
| US-10 | Throttled `download_progress` socket event from yt-dlp progress callbacks (one emit per integer-pct bucket); forwarder wired in `karaoke.py`. | `pikaraoke/lib/download_manager.py`, `pikaraoke/karaoke.py`, `tests/unit/test_download_manager.py` |
| US-28 | Schema migration V4 adds `metadata_sources` JSON column. New `update_track_metadata_with_provenance` + `get_metadata_sources` apply a confidence ladder (musicbrainz > itunes > youtube > scanner; manual on top). All metadata writers updated. | `pikaraoke/lib/karaoke_database.py`, `pikaraoke/lib/song_enricher.py`, `pikaraoke/lib/library_scanner.py`, `pikaraoke/lib/song_manager.py`, `pikaraoke/lib/lyrics.py`, plus DB and enricher tests |
| US-31 | `_invalidate_auto_ass` now also clears `lyrics_sha` and `aligner_model`, so audio-sha changes trigger an LRCLib re-fetch on next run. | `pikaraoke/lib/audio_fingerprint.py`, `tests/unit/test_audio_fingerprint.py` |

Full unit suite: 1130 passed, 1 skipped.
