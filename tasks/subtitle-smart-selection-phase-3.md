# Subtitle pipeline — Phase 3: smart selection + A/B compare

## Goal

Stop forcing the operator to manually pick the best source. Score every
successful subtitle artifact, auto-promote the winner per song, and
respect per-language / per-user preferences. Provide an A/B compare
view to validate close calls.

Phase 1 fans out everything; Phase 2 shows it. Phase 3 picks the right
one by default.

## Design

### Quality scoring

Computed at orchestrator success time (or on-demand via batch
recompute). Stored in `subtitle_jobs.quality_score` (new column, FLOAT).

Score components, weighted:
- **Tier**: word=1.0, line=0.6 — biggest factor
- **Language match**: source `language` (where exposed — LRCLib, Spotify,
  Genius) matches `songs.language`: +0.15
- **Length sanity**: ratio of subtitle duration to audio duration in
  `[0.7, 1.05]`: +0.1; outside: -0.2 (catches truncated lyrics)
- **Whisper confidence**: median per-word confidence above 0.6: +0.1;
  below 0.4: -0.2 (only AI source)
- **Source trust**: per-source bias from history of user overrides
  (Phase 4 feeds this; Phase 3 starts at neutral 0.0)
- **User trust**: per-source `quality=bad` flags from "Report broken"
  (Phase 4): -0.5

Score tie-breakers (in order): tier preference, source order in
`SUBTITLE_PREFERENCE_ORDER` setting, finished_at desc.

### Selection policy

Setting `SUBTITLE_SELECTION_POLICY`:
- `auto-best` (default) — pick highest quality_score among `success`
  jobs. Re-evaluate when a new source completes.
- `prefer-word-level` — only word-tier sources eligible; fallback to
  line-tier if none.
- `manual-only` — never auto-promote; respect existing
  `subtitle_source_override` only.

When auto promotes a new source mid-queue, emit `subtitle_source_changed`
event so chips update + edit view logs it. Operator's manual override
(`subtitle_source_override`) always wins until cleared.

### Per-language preferences

Setting `SUBTITLE_LANGUAGE_PREFERENCES`: map of `lang → ordered source list`.
Default seeded with:
- `pl` → `tekstowo-sync, lrclib-sync, genius-sync, AI`
- `en` → `lrclib-sync, genius-sync, spotify-sync, AI`
- `*`  → `lrclib-sync, genius-sync, spotify-sync, tekstowo-sync, AI`

Used as a tie-breaker boost: source position in the language list adds
`(N - position) * 0.05` to the score.

### A/B compare in edit view

Below the chip row, a "Compare" button opens a split-pane:
- Left: source A picker, renders ASS preview (existing renderer reused).
- Right: source B picker, same.
- Shared timeline scrubber synchronizing both panes.
- Audio player drives both.

Each pane shows tier + score + provenance (`lyrics_provenance` field).
Buttons: "Use A" / "Use B" set `subtitle_source_override`.

### Score recompute

CLI: `pk-tools recompute-subtitle-scores [--song-id N | --all]`. Useful
after weight changes. Idempotent.

### Score visibility

Chip tooltip (Phase 2) gains: `tier · score 0.84 · lang pl`. Helps the
operator understand auto-pick.

## Files touched

- `pikaraoke/karaoke_database.py` — add `quality_score`, `language`,
  `confidence` columns to `subtitle_jobs`; migration.
- `pikaraoke/lib/subtitle_scoring.py` — new; `score(job, song) -> float`
  pure function for unit testing.
- `pikaraoke/lib/subtitle_orchestrator.py` — call scorer on success;
  trigger re-selection.
- `pikaraoke/lib/subtitle_selector.py` — new; given all `success` jobs +
  policy + language prefs → returns winner source id.
- `pikaraoke/karaoke.py` — wire selector to set
  `subtitle_source_override` when auto-promoting; emit
  `subtitle_source_changed`.
- `pikaraoke/routes/admin.py` — settings for policy + language prefs.
- `pikaraoke/templates/edit.html` — Compare button + split-pane.
- `pikaraoke/static/js/edit.js` — split-pane wiring, shared scrubber.
- `pikaraoke/static/js/subtitle-chips.js` — extended tooltip with score.
- `pikaraoke/cli/tools.py` (or new) — `recompute-subtitle-scores`.
- `tests/unit/test_subtitle_scoring.py` — score components, weights,
  tie-breakers.
- `tests/unit/test_subtitle_selector.py` — policies, language prefs,
  override precedence.
- `tests/js/subtitle-chips.test.js` — score in tooltip.

## Out of scope (deferred)

- Background retry / mark-broken feedback loop into trust score —
  Phase 4 wires it in; Phase 3 leaves the column at neutral.
- Drift correction (per-song offset) — Phase 4.
- In-browser editor — Phase 4.

## Acceptance

- New song downloads, all 7 sources fan out, 4 succeed; `auto-best`
  picks the highest-scoring word-tier source without operator action.
- Polish song with `language=pl`: tekstowo-sync wins over lrclib-sync
  even when both have equal tier and finished close together.
- Operator opens edit view, clicks Compare, picks B → override sticks
  across reloads and is reflected on splash.
- `pk-tools recompute-subtitle-scores --all` runs idempotently and
  changes selections only where new scores cross thresholds.
