# Parallel yt-dlp: audio-only → Demucs, silent video

## Goal

Kick off Demucs as soon as the audio stream finishes downloading, instead of
waiting for yt-dlp to merge video+audio into a single mp4. Split the
preference-driven pipeline so vocal-removal downloads produce two sibling
files; the default pipeline stays unchanged.

## Decisions (confirmed)

- Cache key: **SHA256 of the audio file** (reuse existing
  `get_cache_key(path)` called on the `.m4a`).
- **Dual download pipeline**, keyed on `vocal_removal` preference at
  execute time:
  - ON → two parallel yt-dlp processes (audio-only + silent video,
    **always HQ selectors regardless of the `high_quality` pref**),
    Demucs starts when audio is done.
  - OFF → unchanged upstream pipeline (respects `high_quality` pref).
- **No backwards-compat shims.** Existing muxed mp4s in the library play
  via the unchanged path because they have no sibling `.m4a`.
- Preference changing after download is fine: playback picks the right
  path per file based on whether a sibling `.m4a` exists.

## File shape

- Vocal-removal download →
  - `<Title>---<ID>.mp4` (video-only, silent, h264, faststart)
  - `<Title>---<ID>.m4a` (AAC)
  - `<Title>---<ID>.info.json`, optional subs/ass (unchanged)
- Default download → `<Title>---<ID>.mp4` (merged, unchanged)

## Tasks

### 1. `youtube_dl.py`

- \[x\] Add `build_ytdl_audio_only_command(url, download_path, proxy, extra)`.
  - `-f bestaudio[ext=m4a]/bestaudio`
  - `-x --audio-format m4a` to force consistent extension
  - `-o "<path>/%(title)s---%(id)s.%(ext)s"`
  - No `--write-info-json` / subs on this side (video side owns them).
- \[x\] Add `build_ytdl_video_only_command(url, download_path, ...)`.
  - `-f bestvideo[ext=mp4][vcodec~='^avc1|^h264']/bestvideo[ext=mp4]/bestvideo`
  - Keep `-S vcodec:h264`, `--postprocessor-args ffmpeg_o:-movflags +faststart`.
  - Keep `--write-info-json`, `--write-subs`, `--sub-langs`,
    `--convert-subs vtt`, `--embed-metadata`.
  - Strip audio if yt-dlp fetches something with audio (shouldn't, but
    safe).

### 2. `demucs_processor.py`

- \[x\] `prewarm(file_path)`: if a sibling `.m4a` exists alongside the
  passed file, prefer it for cache key + extraction. If `file_path` is
  already an audio-only file (`.m4a`/`.mp3`/…), use it directly. Compute
  cache key from the audio file (already content-addressable).
- \[x\] `_prepare_stems` (stream_manager caller) — same resolution rule.
  Give the ffmpeg extract step the audio file instead of the silent mp4.

### 3. `download_manager.py`

- \[x\] Branch in `_execute_download` on `vocal_removal` preference.
- \[x\] New branch: spawn two `Popen`s in parallel (audio, video). Watch
  progress of both; merge into `active_download` (weighted by typical
  byte sizes, or simplest: average of the two percentages).
- \[x\] When audio `Popen` exits cleanly, fire Demucs prewarm on the
  resulting `.m4a` path. Don't wait for video.
- \[x\] When video `Popen` exits cleanly, emit `song_downloaded` (as today)
  and run metadata merge.
- \[x\] Failure policy:
  - Audio fails, video succeeds → song still usable; Demucs will fall
    back to extracting audio from the muxed mp4 on first play. Log a
    warning.
  - Video fails → treat whole download as failed (same error path as
    today). Audio Popen: if still running, terminate; clean up its
    output if the file exists.

### 4. `file_resolver.py`

- \[x\] Add `audio_sibling_path: str | None` on `FileResolver`. In
  `process_file`, after `self.file_path` is set to an `.mp4`, check for
  `{basename}.m4a` in the same directory. Populate the field.

### 5. `stream_manager.py`

- \[x\] `play_file`: when `fr.audio_sibling_path` is set:
  - `vocal_removal` on → stems flow already handles audio; nothing else
    to wire.
  - `vocal_removal` off → force `needs_audio_pipe = True` and use
    `AudioTrackConfig(source_path=fr.audio_sibling_path, …)`.
- \[x\] `_prepare_stems`: use `audio_sibling_path` (when present) as both
  the cache-key source and the ffmpeg extract input.

### 6. `song_manager.py`

- \[x\] `_get_companion_files`: also return `{base}.m4a`. Keeps delete and
  rename consistent.

### 7. `lyrics.py`

- \[x\] No change needed in callers — `_prewarm_stems(song_path)` still
  passes the mp4 path; `demucs_processor.prewarm` now auto-detects the
  sibling.

### 8. Tests

- \[x\] `test_download_manager.py` — add a parallel-path test mocking two
  Popens; confirm Demucs prewarm fires on audio completion.
- \[x\] `test_stream_routes.py` — silent-video + sibling `.m4a` →
  `audio_track_url` routed to sibling.
- \[x\] `test_youtube_dl.py` — command builders smoke test.
- \[x\] Any demucs_processor unit tests — sibling-detection branch.

## Out of scope

- True streaming Demucs (still needs full waveform).
- Extract-audio-from-stream piping (can come later if parallel downloads
  aren't enough).
- Library migration for existing muxed mp4s.

## Review

- **yt-dlp**: `build_ytdl_video_only_command` (HQ h264/mp4 + info.json +
  subs + faststart) and `build_ytdl_audio_only_command` (bestaudio,
  `-x --audio-format m4a`, no info.json/subs) added as siblings of the
  existing merged builder.
- **demucs_processor**: `resolve_audio_source()` centralises sibling
  lookup; `prewarm()` passes the audio path through it so cache key is
  SHA256 of the `.m4a` when present.
- **download_manager**: `_execute_download` dispatches on the
  `vocal_removal` pref. Merged path (`_run_merged_download`) preserves
  the upstream behaviour. Split path (`_run_split_download`) runs video
  and audio yt-dlp in parallel threads, averages progress, fires Demucs
  prewarm the moment audio exits cleanly, and sweeps up orphan files if
  either process fails.
- **file_resolver**: `audio_sibling_path` populated for `.mp4` inputs
  when a `<basename>.m4a` exists on disk.
- **stream_manager**: `play_file` forces the audio pipe on when a
  sibling is present and vocal removal is off (the video is silent,
  no native audio to serve). `_prepare_stems` runs Demucs off the
  sibling so download-time prewarm and play-time lookups agree on the
  cache key.
- **song_manager**: `.m4a` joins `.cdg`/`.ass` as a recognized companion
  so delete/rename stay in sync.
- **Tests**: parallel-download coverage in `test_download_manager`,
  command-builder coverage in `test_youtube_dl`, sibling detection in
  `test_file_resolver`, `resolve_audio_source` in the new
  `test_demucs_processor`, companion-file coverage in
  `test_song_manager`. Fixed a latent flake in `test_stream_manager`
  where the mock `FileResolver`'s auto-truthy `audio_sibling_path`
  would now have pulled every direct-video test into the audio-pipe
  branch. 948 tests pass.

## Follow-ups

- \[x\] Cancel the still-running yt-dlp when the sibling fails — both
  Popens now start on the caller thread so either reader can
  `terminate()` the other the moment its own rc is non-zero. Test
  `test_split_download_cancels_sibling_on_failure` pins the behaviour.
- \[ \] Consider cross-fading the sibling m4a to stems during the warmup
  window when playback starts before Demucs completes. Scope: open
  `needs_audio_pipe` for vocal_removal-on when `has_audio_sibling` and
  stems aren't cache-hit, then add an m4a→stems crossfade branch in
  `splash.js`. Deferred — belongs in its own PR.
