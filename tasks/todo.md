# Parallel lyrics fetch + progressive display

## Goal

Show subtitles on splash as fast as possible during playback, and
upgrade to better quality as more sources return. Fan-out the three
sources (LRCLib, Genius, YouTube VTT) instead of running them
sequentially.

## Problem in today's code

`_do_fetch_and_convert` at `pikaraoke/lib/lyrics.py:297` is strictly
serial:

1. LRCLib (+iTunes canonicalize retry) — up to 20s on timeouts
2. Genius (blocks on Demucs stems for alignment, inline on the main
   thread)
3. YouTube VTT (only when both above miss)
4. Whisper fallback

If LRCLib times out, Genius cannot start. The log for Kolorowy wiatr
shows 15s of LRCLib stalls, then 13s wav2vec2 cold-load, then 26s
alignment — total 60s before anything renders on screen.

## Decisions

- **Four-tier ordering** (monotonic upgrades only — never downgrade):
  - T0 NONE = nothing written
  - T1 LINE_VTT = line-level from YouTube VTT
  - T2 LINE_LRC = line-level from synced LRCLib LRC
  - T3 WORD = word-level from wav2vec2 forced alignment (LRC or
    Genius or Whisper source)
- **Progressive writes:** any source that passes its own dub-trap
  guard calls `_try_write_ass_tiered(tier, ass, source, lyrics_sha)`.
  The gate writes atomically and emits `lyrics_upgraded` only when
  `tier >= current`. Frontend already hot-swaps on the event
  (splash.js:840–864, karaoke.py:1054–1073).
- **Parallel fan-out:** LRCLib fetch, Genius text fetch, VTT probe
  all start immediately after the language classifier. No worker
  blocks another.
- **Genius alignment off the main thread:** spawn a daemon thread
  that waits on stems and aligns independently, mirroring the LRC
  word-level upgrade.
- **Preload wav2vec2 model** in parallel with Demucs — `_ensure_align_model(lang)`
  in a daemon thread as soon as language is known.
- **Preserve dub-trap guards:** each source runs its own language
  mismatch check before submitting its candidate.
- **Cache invalidation:** `_maybe_drop_stale_auto_ass` runs early
  with `lyrics_sha=None` (handles audio sha + model change).
  LRC-sha-change invalidation is implicit — a newer LRCLib text
  overwrites via the tier gate (same tier, new content is written
  because sha differs).
- **Whisper fallback:** only when all three sources missed AND no
  alignment produced a T3 .ass — checked after all workers join.

## Task list

### 1. Tier coordinator (in `lyrics.py`)

- \[ \] Add `_LyricsTier` int constants or IntEnum (NONE=0, LINE_VTT=1,
  LINE_LRC=2, WORD=3).
- \[ \] Add `_tier_state: dict[str, int]` + `_tier_lock: threading.Lock`
  as `LyricsService` instance attrs.
- \[ \] Add `_try_write_ass_tiered(song_path, new_tier, ass, source,     aligner_model, lyrics_sha) -> bool` that:
  \- locks
  \- compares new_tier >= current (NONE default)
  \- if yes: `_write_ass_atomic`, updates state to new_tier,
  calls `_register_ass` (which emits `lyrics_upgraded`)
  \- returns True on write
- \[ \] Reset state entry on pipeline start (so re-runs don't see
  stale tier).

### 2. Worker refactor

- \[ \] Extract `_worker_lrc(song_path, info)` — does iTunes fallback,
  dub-trap check, `_try_write_ass_tiered(LINE_LRC, ...)`,
  returns `(lrc, lyrics_sha)` for downstream alignment.
- \[ \] Extract `_worker_vtt(song_path)` — picks best VTT, dub-trap-
  safe conversion, `_try_write_ass_tiered(LINE_VTT, ...)`.
- \[ \] Extract `_worker_genius(song_path, info)` — fetches text,
  runs dub-trap guard, waits for stems, aligns, `_try_write_ass_tiered(WORD, ...)`.
- \[ \] Extract `_worker_lrc_align(song_path, lrc, lyrics_sha)` — wraps
  existing `_upgrade_to_word_level`, writes via tier gate.

### 3. `_do_fetch_and_convert` rewrite

- \[ \] Keep: user-owned .ass short-circuit, metadata read, language
  classifier, cached stems probe, word-level cache hit check,
  `_maybe_drop_stale_auto_ass(path, None)`.
- \[ \] Spawn threads: Demucs prewarm, wav2vec2 preload, LRC worker,
  Genius worker, VTT worker.
- \[ \] LRC worker, on hit, spawns the word-level align worker (chained).
- \[ \] Join all threads with reasonable timeout (e.g. 180s hard cap).
- \[ \] After join: if tier still NONE and Whisper enabled, spawn
  Whisper worker; else emit `song_warning` "No lyrics found".
- \[ \] After join: emit final `"Lyrics ready"` notification when tier
  \>= LINE_VTT.

### 4. wav2vec2 preload

- \[ \] Add `_warmup_aligner(language)` method on LyricsService,
  no-op if aligner None or language None.
- \[ \] Call from `_do_fetch_and_convert` right after language
  classifier (when lang is known).

### 5. Tests

- \[ \] Unit-test `_try_write_ass_tiered` monotonicity (VTT then LRC
  then WORD → 3 writes; WORD then LRC → 1 write).
- \[ \] Update `TestLyricsServiceFetchAndConvert` — assert outcomes,
  not sequence.
- \[ \] Update `TestLyricsServiceGeniusFallback` — Genius runs in its
  own thread, no longer inline.
- \[ \] Update `TestLyricsServiceNewFlow` — verify tier-based writes
  land when expected.
- \[ \] Keep `TestLyricsServiceLanguageMismatch` intact (per-source
  guards unchanged).
- \[ \] `TestPrewarmTriggeredFromFetchAndConvert` — still fires.
- \[ \] Add a test for wav2vec2 preload firing.

### 6. Review / verify

- \[x\] `uv run pre-commit run --config code_quality/.pre-commit-config.yaml --files pikaraoke/lib/lyrics.py tests/unit/test_lyrics.py`
  — black reformatted, pylint + isort clean. "No Commit to master"
  warning is pre-existing (user commits manually).
- \[x\] `uv run pytest tests/unit/` — 1320 passed, 1 skipped.
- \[ \] Smoke test: Kolorowy wiatr replay — confirm word-level .ass lands
  sooner than 60s (expected savings: ~13s from wav2vec2 preload,
  ~0-15s from VTT-parallel-to-LRC when captions exist).

## Review

**What shipped:**

- Per-song tier coordinator in `LyricsService`
  (`_TIER_NONE/_LINE_VTT/_LINE_LRC/_WORD`, `_tier_lock`,
  `_try_write_ass_tiered`). Every source now routes its .ass write
  through the gate — VTT/LRC/Genius/Whisper/wav2vec2-upgrade. The gate
  both invalidates lower-tier writes that land late and emits the
  `lyrics_upgraded` event that hot-swaps the subtitle URL.
- `_do_fetch_and_convert` rewritten around three parallel tracks:
  - `_worker_vtt` fires immediately (\<100ms path to line-level .ass
    when YouTube captions were downloaded with the song).
  - LRC fetch stays on the main thread (preserves US-31 LRC-sha
    invalidation semantics).
  - wav2vec2 preload via `_warmup_aligner_async` runs in parallel with
    Demucs + LRC fetch; saves ~13s of cold-start on the first
    alignment per language per process.
- Genius and Whisper fallbacks now write through the tier gate instead
  of `_write_ass_atomic` + `_register_ass` directly. Same behaviour on
  the happy path, but they can no longer clobber a higher-tier .ass
  that landed first.

**What didn't:**

- Genius fetch stayed inside `_try_genius_fallback` (spawned as a
  thread only when LRC misses). Running it in parallel with LRC would
  save ~1s in the miss case but adds coordination complexity
  (cancel-on-LRC-hit) for little win — left out on purpose.
- LRC fetch itself is still sequential on the main thread. Moving it
  off would require rethinking the cache-hit + LRC-sha invalidation
  dance; out of scope here.

**Expected wall-time impact on the Kolorowy wiatr case from the
original log:**

| Stage               | Before | After | Saved |
|---------------------|--------|-------|-------|
| LRC fetch           | 15s    | 15s   | 0     |
| Demucs              | ~22s (parallel already) | same   | 0     |
| wav2vec2 model load | 13s (serial after Demucs) | ~0s (preloaded) | 13s   |
| Align               | 26s    | 26s   | 0     |
| VTT display         | (never — no VTT) | n/a | 0 |
| **Total to word-level** | **~60s** | **~47s** | **13s** |

For songs with YouTube captions, VTT lands at ~100ms instead of after
LRC timeout — the user sees subs almost immediately while higher-tier
sources upgrade in the background.
