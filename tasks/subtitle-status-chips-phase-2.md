# Subtitle pipeline — Phase 2: status chips on splash, remote, queue

## Goal

Show, in one glance, which subtitle source is active and what the rest
are doing — on splash (TV), remote (phone/desktop), queue list, and
edit view. Replace the dropdown picker with a chip row driven by the
live state from Phase 1.

Today the splash badge `#lyrics-source` shows only the active source;
the remote uses a `<select>`; queued songs show no subtitle status at
all. Edit view timeline (Phase 0) only shows history, not present state.

## Design

### Single chip-row component

One vanilla-JS module `static/js/subtitle-chips.js` reused by splash,
now-playing-bar, queue list, edit view. Inputs: payload from
`/api/songs/<id>/subtitles` (Phase 1) + `subtitle_job_update` socket
events. Outputs a `<div class="subtitle-chips">` with one chip per
source.

Chip visual states:
- `success` ready, inactive — solid color, source label
- `success` ready, **active** — solid color + highlighted border + active glyph
- `running` — pulsing spinner glyph
- `queued` — dim outline
- `failed` — amber outline + warn glyph, tooltip = `error_message`
- `rate_limited` — amber outline + clock glyph, tooltip = `retry in Xm`
- `skipped` — hidden by default; toggle "show all" reveals

Order is fixed (matches picker today): `user, youtube-vtt, lrclib,
lrclib-sync, genius-sync, spotify-sync, tekstowo-sync, AI`. Source
labels come from existing `LYRICS_SOURCE_LABELS` (extended in Phase 1).

Click on a `success` chip → POST `/subtitle_source` with that source
(reuse existing endpoint from karaoke.py:1432). Long-press on `failed`
chip → POST `/api/songs/<id>/subtitles/<source>/retry` (Phase 4 wires
the retry; Phase 2 stub returns 501 with friendly toast).

### Splash placement

Replace single badge in `splash.html:65-68` with the chip row. Two layouts:
- **Idle / between songs** — full chip row, large.
- **During playback** — collapsed: only active chip large, others as small
  dots. Hovering or remote action expands. Auto-collapse after 4s of no
  state changes.

When a chip flips to `success` mid-playback, briefly shimmer it (CSS
animation) so the operator notices a new option became available.

### Remote / now-playing-bar placement

Replace `<select>` in `base.html:528-532` with the chip row. Mobile:
horizontal scroll. Desktop: wraps. Tap = switch source; long-press =
context menu (retry / view error / view raw).

### Queue list placement

For each queued song, fetch `/api/songs/<id>/subtitles` (lazy / batched —
one bulk endpoint `POST /api/songs/subtitles/bulk` taking a list of ids).
Render a small "rosette": e.g. `4/7` with mini icons; click expands an
inline chip row.

Bulk endpoint avoids N HTTP calls when the queue has 30+ entries.

### Edit view live update

Replace the current "Odśwież" button on `edit.html:443-464` with
SocketIO subscription to `subtitle_job_update` filtered by the song's
youtube_id/basename. Existing event log keeps "Odśwież" since
`song_event` is denser. New section "Aktualny stan napisów" above the
timeline shows the chip row.

### Settings — operator policies

Add to admin/settings:
- `SUBTITLE_CHIPS_DENSITY`: compact / full (default compact on mobile,
  full on desktop)
- `SUBTITLE_CHIPS_AUTOCOLLAPSE_MS`: integer, 0 disables (default 4000
  on splash, 0 elsewhere)

## Files touched

- `pikaraoke/static/js/subtitle-chips.js` — new shared component.
- `pikaraoke/static/css/subtitle-chips.css` — new.
- `pikaraoke/templates/splash.html` — replace badge with chip row.
- `pikaraoke/static/js/splash.js` — wire socket + initial fetch;
  remove old `LYRICS_SOURCE_LABELS` rendering, delegate to component.
- `pikaraoke/templates/base.html` — replace `<select>` with chip row.
- `pikaraoke/static/js/now-playing-bar.js` — wire chip row, drop
  dropdown handlers.
- `pikaraoke/templates/queue.html` (or wherever queue rows render) —
  rosette + inline expansion.
- `pikaraoke/static/js/queue.js` — bulk fetch and per-row updates from
  socket events.
- `pikaraoke/routes/api.py` — `POST /api/songs/subtitles/bulk`.
- `pikaraoke/templates/edit.html` — chip row section + socket wiring.
- `pikaraoke/static/js/edit.js` (extract from inline if currently inline).
- `tests/js/subtitle-chips.test.js` — render states from fixture payloads.
- `tests/unit/test_api_routes.py` — bulk endpoint.

## Out of scope (deferred)

- Auto-promotion of best source — Phase 3.
- Long-press → retry actually retrying — Phase 4.
- Drift correction `<` / `>` buttons in chip context menu — Phase 4.
- A/B compare split-screen — Phase 3.

## Acceptance

- Operator on splash sees, mid-playback, that `spotify-sync` just
  finished and chip shimmers.
- Operator on phone taps `tekstowo-sync` chip → splash subtitle source
  switches without page reload.
- Queue row "Despacito" shows `5/7` rosette and expanding it reveals
  which two sources failed (with reasons in tooltip).
- Edit view auto-updates as enrichment progresses, no Refresh click
  needed for state (timeline still uses Refresh for log entries).
