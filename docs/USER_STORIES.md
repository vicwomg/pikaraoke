# PiKaraoke User Stories

Living document of required functionalities and expected behavior. Organized by
area so each story can be exercised independently during QA or regression
checks.

## Library and search

### US-1 Unified search bar

As a user, I have one search bar that:

- Searches my **downloaded library** by artist/title and shows matching
  songs inline (so I can queue what I already have without re-downloading).
- Simultaneously autocompletes against **iTunes / MusicBrainz** metadata so
  I can pick a canonical artist+title even when I remember only part of one.
- On selecting an autocomplete entry (or when I type a free-form query),
  fetches **YouTube results** and lets me download one with a single click.
  Library matches and autocomplete suggestions are visually distinct so I can
  tell at a glance what's already available versus what would trigger a
  download.

### US-2 Add a song by YouTube URL

As a user, I paste a YouTube URL and the song is downloaded, metadata-enriched,
and shown in the library.

- yt-dlp runs once per URL. If the media file is already on disk, yt-dlp
  detects the cache and exits fast.
- The app emits a `song_downloaded` event whether the download was fresh or a
  cache hit.
- iTunes metadata resolution runs in parallel with yt-dlp and is merged into
  `<stem>.info.json` so downstream steps see canonical artist/track.

### US-3 Cache-aware re-request

As a user, re-requesting a song I already have is near-instant: the library
does not re-run demucs, does not re-run whisper, and does not rewrite an
already-aligned `.ass`.

### US-4 Delete a song

As a user, deleting a song in the UI removes every artifact belonging to it
(media, info.json, VTT, ASS, and the stems cache directory) in a single pass.

### US-5 Sync library

As a user, I can trigger a library rescan that reconciles the on-disk state
with the DB without blocking playback.

## Processing pipeline (download → demucs → lyrics → alignment)

The background pipeline is fully parallel and event-driven. Queuing a
download must never freeze the UI; the user can keep browsing, searching,
and queueing other songs while processing runs.

### US-6 Split audio / video download

As a user, when I queue a YouTube URL and vocal removal is enabled, yt-dlp
downloads **audio and video as separate streams in parallel**. This matters
so that downstream stages can start as soon as audio is ready without
waiting for video.

- Audio lands first as a sibling `.m4a` (or equivalent); video lands as a
  silent `.mp4`. The pipeline treats the audio sibling as the source of
  truth for separation and alignment.
- **Gate**: split-download is skipped when `vocal_removal = False` — with
  Demucs disabled there's no need for a dedicated audio sibling, so
  `DownloadManager._execute_download` falls back to a merged-file yt-dlp
  invocation (see `download_manager.py:258-263`). This is intentional:
  skipping the split saves bandwidth and avoids the post-merge cost on
  CPU-only machines that can't realistically run Demucs anyway.

### US-7 Demucs starts on audio completion

As a user, the moment the audio stream finishes downloading (and if vocal
removal / demucs is enabled), demucs is prewarmed in the background — I do
not have to wait for video to finish.

- Demucs produces **full-song** stems (vocals + instrumental); there are no
  per-region stems. The song is either "stems ready" or "stems not ready".
- Completion emits a `stems_ready` event; the now-playing panel switches
  from single-slider to dual-slider (US-22) once stems for the song exist.
- While demucs is still running, the splash can still stream the partial
  output progressively (`vocals.wav.partial` → `vocals.wav` → `vocals.mp3`);
  that streaming progress drives the seek-bar buffering bar (US-23), not
  the slider mode.

### US-8 Lyrics lookup fires as early as possible

As a user, the subtitle lookup pipeline runs **as soon as the song is
downloaded** — it does not wait for demucs. LRCLib / iTunes / VTT only need
the canonical artist+track, and getting captions on screen sooner is
strictly better. Whisper alignment (US-9 step 2) is the only stage that
actually needs the demucs vocals stem; that stage waits for `stems_ready`
internally.

Lookup order:

1. LRCLib is queried first (synced lyrics).
2. When LRCLib misses, iTunes canonicalizes the noisy YouTube-derived
   artist/track and LRCLib is retried with the clean query.
3. YouTube `.vtt` captions act as a last-resort fallback.

Additional providers can be added later without changing the contract: the
order is LRCLib → iTunes-rescued LRCLib → YouTube VTT → none.

### US-9 Subtitles converted to ASS, then upgraded to word-level

As a user:

1. As soon as subtitles (from any source) are available, they are converted
   to a line-level `.ass` with the PiKaraoke auto-marker. The splash screen
   starts rendering captions immediately.
2. Whisper (when the aligner is configured) runs on the demucs vocals stem
   to upgrade the line-level `.ass` to per-word karaoke timing so words
   transition as they're sung. See US-25 for the visual contract (two-color
   scheme, smooth fill, tempo-aware pulse).

- Step 2 is a background thread and never blocks playback. If step 2 fails
  or finds no match, step 1's line-level captions remain on screen.

### US-10 Non-blocking, parallel execution

As a user, every stage above runs off the request thread:

- Download queue is serialized (one yt-dlp at a time to avoid rate
  limiting) but audio/video within one download run in parallel.
- Demucs, LRCLib, iTunes, YouTube VTT reads, and whisper all run in
  background threads.
- The UI stays responsive; progress is pushed via SocketIO events
  (`download_progress`, `demucs_progress`, `stems_ready`, `ffmpeg_progress`,
  `lyrics_upgraded`, etc.).

## Splash-screen progress and error feedback

### US-11 Splash notifications per stage

As a viewer, the splash screen shows a lightweight notification (toast,
status chip, or equivalent) for each pipeline milestone on the current or
incoming song — for example:

- "Downloading audio…" / "Downloading video…"
- "Separating vocals…" (with percent when demucs reports it)
- "Fetching lyrics…"
- "Lyrics ready"
- "Aligning words…"
- "Synced lyrics ready"
  Notifications auto-dismiss after a short interval and never cover the
  lyrics region.

### US-12 Splash error indicators

As a viewer, when any pipeline stage fails — lyrics not found, demucs
error, whisper alignment failed, download error — the splash shows a
**visual cue** (e.g. a warning icon in the corner) rather than a
show-stopping dialog. Hovering or focusing the icon reveals a tooltip with:

- What failed (e.g. "Word-level alignment failed").
- The reason / error message (e.g. `RuntimeError: CUDA out of memory`).
- Which song the warning is for.
  The karaoke experience continues with whatever output earlier stages did
  produce (e.g. line-level lyrics if whisper failed).

### US-13 Warnings survive until acknowledged

As a viewer / operator, warning icons persist for the duration of the
affected song (or until the operator dismisses them from the control
panel), so issues that happen mid-playback aren't missed if the viewer
looks away for a moment.

## Lyrics semantics

### US-14 Lyrics source precedence

As a user, when a new song is downloaded, synced lyrics are selected in this
order:

1. User-supplied `.ass` (Aegisub file without the auto-lyrics marker) — never
   overwritten.
2. LRCLib (direct, then iTunes-rescued retry).
3. YouTube `.vtt` captions (when present).
4. No lyrics (emits a `song_warning`).

Lyrics must match the **track's original language** — never a translation.
Concretely:

- For YouTube VTT, prefer manual uploads over auto-generated, and pick the
  language code that matches the track (not the UI locale). Auto-generated
  tracks labeled as translations (e.g. `en-orig`, `-auto`-suffixed
  languages pointing to a translation) are treated as last-resort and
  skipped when a non-translated option exists.
- For LRCLib, use the lyrics tied to the canonical artist+track; do not
  substitute a translated version from a different recording.
- The track's language is determined in this priority order, so the
  expensive step is skipped whenever possible:
  1. **Cached in DB** from a prior run.
  2. **Detected from the lyrics text** (the LRC we just fetched, or the
     selected VTT language code) — a fast text-based language ID pass.
  3. **Whisper's acoustic detection** only as a last resort when no
     lyrics-derived signal is available.
     The resolved language is persisted (US-16) so every subsequent replay
     reuses it.

### US-15 Cache-correct re-alignment

Whisper alignment is an expensive step and must run only when the output
would actually change. The cache invalidation hierarchy is:

1. **Audio fingerprint refresh**: if the source media's mtime or size changed,
   recompute its sha256. If the sha is unchanged, this is free — just refresh
   mtime/size in the DB.
2. **Demucs stems**: re-run when (and only when) the audio sha256 actually
   changes, or when the configured `demucs_model` changes.
3. **Whisper alignment**: re-run when demucs re-ran (either of the above),
   when the configured `aligner_model` changes, or when LRCLib returns
   different lyrics content (fingerprinted by `lyrics_sha`).

Nothing else triggers a re-run. Re-requesting the same URL or the user
scrubbing through the file is not a trigger.

### US-16 Language hint reuse

As a user, whisper's 30-second acoustic language-detection pass only runs
when no cheaper source can answer the question. Resolution order:

1. Language cached in the DB from a prior run.
2. Language detected from the **lyrics text** (LRC content or selected VTT
   language code) — a fast text-based pass.
3. Whisper's acoustic detection (fallback when the track has no lyrics
   available yet).

Whichever step produces the answer, it's persisted so subsequent
alignments skip detection entirely and jump straight to transcription
with the known language.

### US-17 Reprocess library on aligner install

As a user, when whisperx becomes available for the first time, existing
line-level `.ass` files are upgraded to word-level in the background, one
song at a time (so CPU/GPU is not thrashed).

## Playback

### US-18 Play a queued song

As a user, pressing "play" on a queued song starts playback on the splash
screen near-instantly: the downloaded mp4 is served directly to the
browser with HTTP byte-range seeking (no ffmpeg transcode, no HLS
segmentation) whenever the source is already a browser-native h264/aac
mp4. Files that aren't browser-native (CDG, exotic codecs, containers the
browser can't demux) still fall back to an ffmpeg transcode path, and
`--streaming-format hls` remains available as an explicit opt-in.

### US-19 Skip, pause, restart

As a user, I can skip, pause, and restart the current song from the control
panel and see the changes reflected on the splash screen in real time.

### US-20 Queue management

As a user, I can reorder and remove items in the queue. Now-playing state and
pending queue persist across app restarts.

## Now-playing panel (control panel)

### US-21 Now-playing display

As a user, the control panel shows a now-playing panel with the current
song's title, artist, seek position, and volume controls. The splash
screen is for the projector/TV; the now-playing panel is for whoever is
running the show.

### US-22 Volume: single vs. dual sliders

As a user, the volume control reflects whether demucs has produced stems for
the **current song** (stems are whole-song, not per-region):

- While the song's stems are **not yet ready** (or vocal removal is off),
  I see **one volume slider** that controls the original mix volume.
- Once stems for the song are ready, I see **two sliders**: vocal volume and
  instrument volume, controlled independently.
- The transition is a one-time flip per song, driven by the `stems_ready`
  event, not by the playhead.

### US-23 Seek bar with buffering progress

As a user, the seek bar:

- Lets me jump to any time position that has been processed and is ready
  to play. On the direct-mp4 path the full video is already on disk, so
  the only tail-behind signal is **stem audio availability during live
  Demucs** (plus ffmpeg transcode progress on the rare non-native fallback
  path).
- Visually shows **buffering / processing progress**: the portion of the
  song that has been demucs-processed is marked, so I can see how far
  ahead of the playhead it's safe to seek. When multiple stages are
  running, the slower one wins.
- **Blocks seeking into unprocessed regions**: I cannot jump to a position
  that isn't buffered yet; the seek bar treats that range as unavailable.
  The clamp is enforced server-side (the `seek` socket handler caps the
  broadcast position to the buffered ceiling), so pilots that haven't yet
  received a progress tick — or any other client emitting `seek` directly
  — still can't push the splash past unwritten stem bytes.

### US-24 Visual cues for processing progress

As a user, the now-playing panel shows clear visual cues — progress bar
shading, a separate processing indicator, or both — that make it obvious
when demucs is still catching up and how much of the song is ready.

## Splash screen

### US-25 Captions rendering

As a viewer, synced lyrics are rendered on the splash screen using the
existing ASS stack (SubtitlesOctopus). No UI changes are required when new
`.ass` files land — the renderer picks them up automatically.

**Visual treatment (auto-generated word-level captions):**

- Two-color scheme: upcoming words are **white**, already-sung words fade
  to **grey** (Apple Music / teleprompter style). No intermediate accent
  colour — the old yellow karaoke wipe is gone.
- The transition is a **smooth left-to-right fill** (`\kf`) across each
  word's own duration, not an instant flip at the word boundary.
- When a song's tempo can be measured at lyrics-generation time, the
  active word also does a subtle **scale pulse** that adapts to BPM —
  gentler on ballads, snappier on uptempo tracks. If tempo detection
  fails the pulse is simply omitted; the smooth fill always applies.

**User-supplied Aegisub (`.ass`) files are never restyled.** Authored
subtitles keep their original colours, fonts, and effects.

### US-26 Now-playing overlay and QR

As a viewer, the splash screen shows the current song info, a QR code to
join from mobile, and optional visualizer overlays.

### US-27 User-action introduction screen

As a viewer, the splash shows a brief user-action introduction screen on
first load — a click-to-start or tap-to-enable overlay that satisfies
browser autoplay-policy gestures.

- If the browser allows autoplay without a user gesture (e.g. the site is
  whitelisted, audio is muted, or the platform doesn't require it), the
  intro screen is **detected as unnecessary and hidden automatically** so
  playback begins seamlessly.
- Otherwise it stays visible until the viewer (or operator) clicks / taps,
  after which audio context is unlocked and the intro is dismissed for
  the rest of the session.
- The intro must never re-appear between songs in the same session — once
  the user has gestured, the audio context stays unlocked.

## Database as source of truth

### US-28 Track metadata persisted in DB

As a user, the DB stores canonical track metadata per song so downstream
stages and the UI don't need to re-query external services on every run:

- **Identity**: artist, title, album, year, genre, variant, duration,
  language.
- **External IDs**: YouTube id, iTunes id, MusicBrainz recording id, ISRC.
- **Provenance**: where each field came from (YouTube info.json, iTunes,
  MusicBrainz), so conflicts can be resolved and staler sources refreshed
  independently.
  When multiple sources agree, the field is authoritative; when they
  disagree, the highest-confidence source wins (MusicBrainz > iTunes >
  YouTube info.json for music identity; YouTube for media-specific fields).

### US-29 Artifact registry

As a user, every file that belongs to a song — media, info.json, VTT,
line-level ASS, word-level ASS, stems cache directory, cover art, sidecar
m4a audio — is registered in the DB under its role (`audio`, `video`,
`ass_auto`, `ass_user`, `stems_cache_dir`, …). The DB is the authoritative
list of what's on disk for a song; the filesystem is the backing store.

### US-30 Cache-file fingerprints

As a user, each significant cache file tracks its own fingerprint
(`sha256`, `size`, `mtime`) in the DB, not just the source audio. Examples:

- Source audio sha256 / size / mtime (already live).
- Demucs stems directory's model (`demucs_model`), so swapping the
  separator model invalidates stems without touching audio.
- `.ass` provenance: `lyrics_source`, `aligner_model`, `lyrics_sha` — so
  swapping the aligner, the demucs model, or the LRC content invalidates
  the aligned `.ass` without re-downloading the song.
  Fingerprints are refreshed cheaply on mtime/size change; full content
  hashes are recomputed only when those signals don't match.

### US-31 Waterfall invalidation

As a user, a change at one level of the pipeline cascades downstream
automatically, and no further:

```
source audio changed (sha256)
  ├─ invalidate demucs stems
  │    └─ invalidate whisper-aligned .ass
  └─ invalidate lyrics_sha (re-fetch LRCLib on next run)

demucs_model changed
  ├─ invalidate demucs stems
  └─ invalidate whisper-aligned .ass

aligner_model changed
  └─ invalidate whisper-aligned .ass

lyrics_sha changed (LRCLib returned different content)
  └─ invalidate whisper-aligned .ass
```

Nothing upstream is ever invalidated as a side effect of a downstream
change. Unrelated caches are never touched.

### US-32 Cache cleanup on song delete

As a user, deleting a song in the UI removes every artifact registered in
the DB — including the stems cache directory and any sidecar files — in
one pass, reference-counted so shared stems (two library entries pointing
at the same audio sha) are not deleted out from under the other entry.

## Caching and storage

### US-33 Stems cache

Demucs-separated stems are stored under `~/.pikaraoke-cache/<audio_sha256>/`
as WAV (intermediate) and MP3 (final). Cache is content-addressable so
re-encoded files with identical audio bytes hit the cache.

### US-34 Atomic writes

All generated artifacts (`.ass`, info.json rewrites, stems) are written to
temp files and atomically renamed into place, so a crash or concurrent read
never sees a partial file.

## Settings and preferences

### US-35 Preferences persist across restarts

As a user, my preferences (high-quality downloads, vocal removal, preferred
language, download path, proxy) survive app restarts.

### US-36 Toggle vocal removal

As a user, I can turn vocal removal on and off. When on, split download
(audio + silent video) is used so demucs can start processing audio before
the video finishes downloading.

## Admin and diagnostics

### US-37 Manual library sync

As an admin, I can trigger a library resync from the admin UI to pick up
files added out-of-band.

### US-38 Download error surface

As an admin, failed downloads are listed with their URL, title, user, and
yt-dlp error output; I can dismiss them one at a time.

### US-39 Structured warnings

As an admin, `song_warning` events (missing lyrics, failed alignment, demucs
failure) surface in the UI with `message`, `detail`, `song`, and `severity`
fields, so problems are diagnosable without tailing logs.

## Multi-device playback

### US-40 Pilots as playback satellites

As a user, any pilot (control panel) can opt in to playing the current
song locally on its own device, in parallel with the splash. The pilot
becomes an audio satellite: it streams the same media the splash is
rendering and stays in lock-step with it, so what comes out of the phone
matches what's on the TV.

- **Opt-in per pilot**: a toggle on the now-playing panel (default off)
  switches that pilot from "silent remote" to "satellite". Other pilots
  are unaffected — one phone can go satellite without turning the whole
  room into echoing speakers.
- **Per-pilot volume / stem sliders**: a satellite pilot exposes its own
  local mix controls (single volume before stems, vocal + instrumental
  sliders once `stems_ready` fires). These are independent of the splash
  mix — the TV can carry the karaoke mix (vocals ducked) while the singer
  monitors full vocals on headphones to stay on pitch.
- **Shared queue, shared transport**: satellites do not fork the session.
  They observe the same queue, the same play / pause / skip / restart
  commands, and the same seek position as every other client. Any pilot
  (satellite or not) can still drive the queue for everyone.
- **Sync with the splash**: satellite audio tracks the splash playhead
  using the same `playback_position` / `seek` / `stems_ready` signals
  described in earlier stories; drift is corrected continuously so the
  satellite never more than ~200 ms off.
- **Use cases**: the singer monitors vocals on in-ear headphones to nail
  pitch; friends on the patio listen through a phone with vocals up while
  the TV keeps the room mix. A satellite must never appear as a second
  splash (no captions, no QR, no intro screen) — it's strictly an audio
  follower of the canonical splash.

## Settings UI

### US-41 Settings panel

As a user, I have a dedicated Settings section in the UI that surfaces
the runtime toggles and environment info I'd otherwise have to dig for
on the command line or in a config file.

- **Vocal splitter toggle**: on / off switch for Demucs. Default is **on
  when a GPU accelerator (CUDA or Apple MPS) is detected**, off otherwise
  so CPU-only machines don't silently pay the separation cost. The
  underlying preference (`vocal_removal`) is the one already covered by
  US-35 and US-36 — Settings is just its canonical UI home.
- **Library versions**: the panel shows the versions of the key external
  libraries pikaraoke depends on — `yt-dlp`, `whisperx` (or the
  configured aligner), and `demucs` — read at runtime, not baked into
  the template, so "it works for me but not for you" bug reports start
  with a clear version footprint. Accelerator backend (CPU / CUDA /
  MPS) is shown alongside so the vocal-splitter default is explainable.
- **Other existing preferences**: high-quality downloads, preferred
  language, download path, proxy, buffer/transcoding flags, etc. — all
  the things US-35 says must persist — are exposed here instead of being
  CLI-only. Changes save immediately and broadcast via
  `preferences_update` so every pilot stays consistent without a page
  reload.
