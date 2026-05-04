# Subtitle pipeline — Phase 4: self-healing + drift fix + in-browser editor

## Goal

Close the loop. The system retries failed/rate-limited sources on its
own, learns from operator feedback when subtitles are wrong, lets the
operator nudge timing on the fly, and lets them edit lyrics in the
browser without leaving the app.

Phase 1–3 build the pipeline, the UI, and the auto-pick. Phase 4 makes
it durable and editable.

## Design

### Background retry

Single retry worker (`SubtitleRetryWorker`) loop, 60s tick:

- Query `subtitle_jobs WHERE state IN ('failed', 'rate_limited')
  AND (next_retry_at IS NULL OR next_retry_at <= now())
  AND attempt_count < MAX_ATTEMPTS`.
- Re-submit each via `SubtitleOrchestrator.retry(song_id, source)`.
- Backoff schedule per `error_code`:
  - `http_429` / `rate_limited`: 1h, 4h, 24h, then give up.
  - `not_found`: 7d, 30d, then give up — covers cases where lyrics get
    added later by upstream provider.
  - `parse_error` / `network`: 5m, 30m, 2h, 24h, give up.
- Set `next_retry_at` from the schedule.
- Worker is opt-out: `SUBTITLE_AUTO_RETRY_ENABLED=1` default.

Manual retry (chip long-press from Phase 2): bypasses backoff, resets
`attempt_count` to 0.

### "Report broken" feedback

Chip context menu: "Mark as broken". Effect:
- `subtitle_jobs` gets `quality_flag='bad'`.
- `subtitle_source_trust` table accumulates per-source bias (rolling
  count of bad / total) — feeds Phase 3 score component.
- Selector (Phase 3) re-runs immediately; next-best source becomes
  active.
- Audit row in `song_event` for traceability.

Symmetric "This is correct" thumbs-up adds positive bias. Pure
optional; nice for training the trust signal.

### Drift correction (per-song offset)

`songs.lyrics_offset_ms` (new column, INTEGER, default 0). On the
remote during playback, two buttons `<` / `>` adjacent to the chip row:
each tap shifts subtitles by 100ms (configurable) in the active
direction. Long-press = 500ms steps.

Splash applies offset live without reloading the ASS — render layer
shifts timestamps in-flight (existing renderer already supports this
or grows a `setOffsetMs(ms)` method).

Persisted per-song: next play of the same file uses the saved offset.

### In-browser lyrics editor

Edit view gets an "Edit lyrics" tab with:
- Monaco editor showing the active variant ASS (raw).
- Waveform view (existing if there is one; otherwise
  `wavesurfer.js`-style visualization of the audio file).
- Per-line timing nudgers and word-timing handles for word-tier ASS.
- Save → writes a new variant `<stem>.user.ass`, sets
  `subtitle_source_override='user'`, audit event in `song_event`.

Scope guardrail for v1: line-level editing only (text + line timing).
Word-level handle drag is a stretch goal — split into a follow-up doc
if it grows.

### Optional: community DB (opt-in)

Behind `SUBTITLE_COMMUNITY_UPLOAD=1` (off by default), submit
operator-edited `user.ass` files to a shared endpoint keyed by ISRC or
youtube_id. Symmetric: orchestrator queries the shared endpoint as one
more source (`community-sync`).

This is the riskiest part of the doc. If it slips out of v1.0,
implement the upload toggle as no-op stub so the setting page is
already in place.

## Files touched

- `pikaraoke/karaoke_database.py` — `lyrics_offset_ms` on `songs`,
  `quality_flag` on `subtitle_jobs`, new `subtitle_source_trust` table,
  migrations.
- `pikaraoke/lib/subtitle_retry_worker.py` — new background loop.
- `pikaraoke/lib/subtitle_orchestrator.py` — `retry(song_id, source)`
  entry point that resets `next_retry_at` and re-submits.
- `pikaraoke/lib/subtitle_scoring.py` — read trust signal.
- `pikaraoke/karaoke.py` — start/stop retry worker; wire offset apply
  on playback start; emit feedback events.
- `pikaraoke/static/js/subtitle-chips.js` — long-press context menu
  (Retry / Mark broken / Mark correct).
- `pikaraoke/static/js/now-playing-bar.js` — drift `<` / `>` buttons.
- `pikaraoke/templates/edit.html` — Edit lyrics tab.
- `pikaraoke/static/js/lyrics-editor.js` — new module, Monaco + waveform.
- `pikaraoke/static/css/lyrics-editor.css` — new.
- `pikaraoke/routes/api.py` — endpoints:
  - `POST /api/songs/<id>/subtitles/<source>/retry`
  - `POST /api/songs/<id>/subtitles/<source>/feedback` (`broken|correct`)
  - `PATCH /api/songs/<id>/lyrics-offset`
  - `PUT /api/songs/<id>/subtitles/user` (save edited ASS)
- `tests/unit/test_subtitle_retry_worker.py` — backoff schedule, query.
- `tests/unit/test_lyrics_editor_routes.py` — save endpoint validation.
- `tests/js/subtitle-chips.test.js` — context menu actions.

## Out of scope (deferred / explicit cut)

- Word-handle drag-to-retime in the editor — follow-up if v1 lands well.
- Multi-user concurrent editing of the same song — single editor lock
  via `subtitle_jobs.state='editing'` is enough; CRDT etc is overkill.
- Community DB hosting infra — Phase 4.5 if at all; ship the toggle
  as a stub.
- Vocal-removal / Demucs improvements — different pipeline.

## Acceptance

- A song where Spotify hits 429 at download time shows
  `rate_limited`; one hour later, without operator action, chip flips
  to `success` and (if it scores higher) auto-promotes.
- Operator long-presses a chip → "Mark as broken" → next-best source
  becomes active within seconds; trust score for that source on that
  song lowers.
- During playback, two taps of `>` shift subtitles 200ms later; next
  play of the same file already has the corrected offset.
- Operator opens Edit lyrics tab, fixes a typo on line 7, saves; on
  next playback the corrected text shows on splash and the chip row
  marks `user` as active.
