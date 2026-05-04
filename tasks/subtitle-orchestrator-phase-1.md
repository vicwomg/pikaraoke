# Subtitle pipeline — Phase 1: orchestrator + per-source state

## Goal

After `song_downloaded`, fan out to ALL configured lyrics sources in
parallel and surface live state per (song × source). Operator stops
guessing what is queued, what is running, what failed.

Today only VTT + LRCLib (+ optional Whisper / consensus) auto-fetch.
`tekstowo-sync`, `spotify-sync`, `genius-sync` start only when the
operator clicks the picker. No per-source state machine — `metadata_status`
is one global value, `song_artifacts` only records successes.

## Design

### Data model — new `subtitle_jobs` table

One row per (song_id, source). Source of truth for UI; `song_artifacts`
stays but becomes derived (success → artifact row exists).

```
subtitle_jobs
  song_id          INTEGER  FK songs.id
  source           TEXT     'lrclib' | 'lrclib-sync' | 'genius-sync' |
                            'spotify-sync' | 'tekstowo-sync' |
                            'youtube-vtt' | 'AI'
  state            TEXT     'queued' | 'running' | 'success' |
                            'failed' | 'skipped' | 'rate_limited'
  tier             TEXT     'word' | 'line' | NULL  (set on success)
  started_at       TIMESTAMP NULL
  finished_at      TIMESTAMP NULL
  error_code       TEXT     NULL  ('not_found', 'http_429', 'parse_error', ...)
  error_message    TEXT     NULL  (one-line human summary)
  attempt_count    INTEGER  default 0
  next_retry_at    TIMESTAMP NULL  (Phase 4 uses; Phase 1 only writes for rate_limited)
  PRIMARY KEY (song_id, source)
```

Migration: create table; backfill rows from existing `song_artifacts`
(`role LIKE 'ass_%'`) with `state='success'`, leave the rest absent.

### `SubtitleOrchestrator`

Replaces ad-hoc orchestration in `LyricsService.fetch_and_convert`
(lyrics.py:874). Entry point: `orchestrator.kickoff(song_id)`.

Behavior:
- Read enabled sources from config (`SUBTITLE_SOURCES_AUTO`, defaults to
  the full `VARIANT_FILE_SOURCES` list minus `user`).
- For each source: insert/update `subtitle_jobs` row with `state='queued'`,
  emit `subtitle_job_update`.
- Submit each source to a per-source-pool executor. Pools:
  - `local` (VTT extract, LRCLib HTTP) — concurrency 4
  - `scrape` (genius, tekstowo) — concurrency 2
  - `spotify` — concurrency 1 (rate-limited token)
  - `align` (wav2vec2 word alignment) — concurrency 1 (CPU/GPU bound)
  - `whisper` — concurrency 1 (GPU bound)
- A worker transitions: `queued → running → success|failed|rate_limited`.
- HTTP 429 / token expired → `rate_limited` with `next_retry_at = now + 1h`
  (Phase 4 wires the retry; Phase 1 just records the timestamp).
- Existing `_register_ass` path keeps writing the artifact and emitting
  `song_event`; orchestrator additionally writes `subtitle_jobs`.

Existing `LYRICS_CONSENSUS_ENABLED` codepath is replaced — orchestrator
always fans out; consensus selection moves to Phase 3.

### Events

New SocketIO event `subtitle_job_update`:
```
{ song_id, youtube_id, source, state, tier?, error_code?, error_message?, ts }
```
Emitted on every state transition. `song_event` (Phase 0) keeps emitting
phase=`lyrics` info/error rows for the timeline; `subtitle_job_update` is
for live UI state.

### REST

`GET /api/songs/<song_id>/subtitles`:
```
{
  active_source: 'lrclib-sync',           // current subtitle_source_override or auto-pick
  sources: [
    { id: 'youtube-vtt', state: 'success', tier: 'line',  is_active: false },
    { id: 'lrclib',      state: 'success', tier: 'line',  is_active: false },
    { id: 'lrclib-sync', state: 'success', tier: 'word',  is_active: true  },
    { id: 'genius-sync', state: 'failed',  error_code: 'not_found' },
    { id: 'spotify-sync', state: 'rate_limited', next_retry_at: '...' },
    { id: 'tekstowo-sync', state: 'running' },
    { id: 'AI',          state: 'queued' },
  ]
}
```
Drives splash + remote chips (Phase 2 consumer).

### Quick wins shipped with Phase 1

- Add `tekstowo-sync` and `spotify-sync` to `LYRICS_SOURCE_LABELS` in
  `splash.js:156` so the existing badge renders for them.
- Toast in now-playing-bar when last queued source flips to `failed`
  with no successes — "no subtitles found".
- Fix `tasks/enrichment-trigger-bug.md` (orchestrator must not silently
  skip when enrichment didn't fire).

## Files touched

- `pikaraoke/karaoke_database.py` — `subtitle_jobs` schema + migration,
  getters: `get_subtitle_jobs(song_id)`, `upsert_subtitle_job(...)`.
- `pikaraoke/lib/subtitle_orchestrator.py` — new module.
- `pikaraoke/lib/lyrics.py` — `LyricsService.fetch_and_convert` becomes
  thin wrapper that calls orchestrator; per-source workers extracted as
  callables the orchestrator submits.
- `pikaraoke/karaoke.py` — wire `song_downloaded` → orchestrator.kickoff;
  forward `subtitle_job_update` events to SocketIO.
- `pikaraoke/routes/api.py` (or extend admin.py) — `/api/songs/<id>/subtitles`.
- `pikaraoke/static/js/splash.js` — `LYRICS_SOURCE_LABELS` additions.
- `pikaraoke/static/js/now-playing-bar.js` — failure toast.
- `tests/unit/test_subtitle_orchestrator.py` — new.
- `tests/unit/test_lyrics.py` — adjust for orchestrator delegation.
- `tests/unit/test_karaoke_database.py` — `subtitle_jobs` CRUD.

## Out of scope (deferred)

- UI chips on splash/remote/queue — Phase 2.
- Quality scoring and auto-promotion — Phase 3.
- Background retry of `failed`/`rate_limited` — Phase 4.
- Per-song lyrics offset and in-browser editor — Phase 4.
