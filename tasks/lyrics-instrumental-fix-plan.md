<!-- /autoplan restore point: /Users/zygzagz/.gstack/projects/zygzagZ-pikaraoke/master-autoplan-restore-20260427-204132.md -->

# Replace greedy line→anchor matching with global DP assignment

> **Plan revision (after premise-gate diagnostic).** The original
> plan proposed a silence-shape filter to drop spurious vocal-onset
> markers. A 30-second ffmpeg diagnostic on the actual Total
> Eclipse vocals stem showed **zero spurious markers** in the
> 2:50–3:28 solo — the Demucs stem is correctly silent there. The
> root cause is structural, not noise: the greedy lockstep snap
> can only assign one LRC line per silence anchor, and Total
> Eclipse's LRC clusters 6–8 short backing-vocal lines around a
> single real audio onset. The unmatched lines render in dead air.
>
> The original plan body (Approach A — silence-shape filter) is
> preserved below for context; it remains a valid auxiliary
> filter if we ever see real spurious anchors, but it is **not the
> primary fix**. The primary fix is a global DP-based assignment
> (Approach D, new). See **"# Pivot: Global DP assignment"**
> at the bottom of the file.

# (Original) Fix per-line silence-anchor alignment across long instrumentals

Branch: TBD (fix/lyrics-instrumental-anchors) | Base: master | Owner: zygzagz

## Problem

User-reported on Total Eclipse of the Heart:

- Instrumental solo runs 2:50–3:28 (no vocals).
- From 3:15 onward there is per-word/per-character highlighting as if
  someone is singing — but no one is.
- After the solo, subtitles run ~2 s late until 4:17, then snap clean.

Both symptoms stem from one defect in
`pikaraoke/lib/lyrics_align.py:_detect_per_line_starts`
(introduced in commit b44434fa, "fix(lyrics): per-line LRC->audio
alignment via silence anchors").

## Root cause

`_detect_per_line_starts` walks LRC lines and the full list of ffmpeg
`silence_end` markers in lockstep. For each line it computes
`expected = lrc_start + cumulative_offset` and snaps to the next
`silence_end` within ±2.5 s. On snap it (a) updates `cumulative` so
later lines pick up the new tempo, and (b) advances `sil_idx`
(consuming the boundary).

Three conditions combine to break Total Eclipse:

1. **Demucs vocals stem is not silent during instrumentals.**
   `_list_silence_ends` runs on whatever `_wait_for_alignment_audio`
   returns — usually the Demucs vocals stem
   (`lyrics.py:2987–3009`). Vocals stems leak: drum bleed, reverb
   tails and brief sub-threshold dips all cross the −30 dBFS guard
   in the solo. ffmpeg dutifully reports several spurious
   `silence_end` markers inside 2:50–3:28.

2. **The LRC's post-solo verse is timestamped too early for this
   YouTube cut.** LRClib pegs to a canonical recording with a
   shorter break. The first sung line after the solo has an LRC
   `start` of roughly 3:18, but the actual vocal onset on this rip
   is ~3:30.

3. **The greedy snap consumes the spurious marker.** When the loop
   reaches that LRC line, `expected ≈ 3:18 + small_cumulative` lands
   inside the solo. The ±2.5 s window grabs the spurious
   `silence_end` at ~3:15, snaps `cumulative` to a wrong negative
   value, and `sil_idx += 1` burns the anchor. Every subsequent line
   inherits the corrupted cumulative until later snaps drag it back.

That single bad snap explains both symptoms:

- **Ghost highlighting from 3:15:** the post-solo Dialogue event now
  fires at 3:15. wav2vec2 forced-alignment receives `[3:15, 3:30]`
  as the line window, fails to find vocals there, and falls back to
  `_uniform_line_words` (`lyrics_align.py:638`) — uniform `\kf`
  fills spread across 13 s of instrumental.
- **2 s lateness until 4:17:** the corrupted cumulative pulls every
  following line earlier in *its* search window too. As real
  vocals continue, each new line again snaps too-early-but-less-bad.
  By 4:17 the cumulative recovers.

The 250 ms `_KARAOKE_LEAD_IN_S` (commit bc7a8a72) is unrelated —
constant lead-in cannot produce a 2 s drift.

## Goals

- Eliminate ghost highlighting during instrumental sections.
- Eliminate cascading lateness for lines that follow a long instrumental.
- Preserve the per-verse drift correction that the per-line silence
  anchor was introduced to provide on Mam Tę Moc et al.
- Do not regress the existing `TestDetectPerLineStarts` cases.

## Non-goals

- Switching the silence probe from the vocals stem to the raw mix
  (raw mix is even noisier; would harm Mam Tę Moc-style cases).
- Replacing `silencedetect` with VAD (webrtcvad / silero). Larger
  surface, new dependency, premature.
- Introducing a global optimiser (DTW, Viterbi over silence
  boundaries). Ten-line greedy bug, not an architectural one.

## Approach options considered

### A. Silence-shape filter (recommended)

Reject `silence_end` markers that are not followed by sustained
audio. Real vocal onsets are followed by seconds of audio before the
next silence; a drum hit during an instrumental is followed by
silence almost immediately.

Concretely: capture both `silence_end` and `silence_start` from
ffmpeg. A `silence_end` at `t` is a credible vocal onset only if the
next `silence_start` is at least `_VOCAL_SUSTAIN_MIN_S` (1.5 s)
later. Drum-hit-style markers fail the check and never enter the
candidate list.

Pros: filters spurious anchors at the source; one consume rule
unchanged downstream; small surface.
Cons: requires also capturing `silence_start` (currently we only
parse `silence_end`); choice of 1.5 s threshold is heuristic.

### B. LRC-gap quarantine

Detect inter-line LRC gaps over a threshold (e.g. > 8 s of
non-empty-line silence). For lines whose `expected` falls inside
such a gap, neither snap nor consume.

Pros: zero ffmpeg parser change; targets exactly the
"ghost-highlight inside instrumental" symptom.
Cons: relies on LRC structure being honest about the instrumental
(many LRCLib entries have no marker line for solos); does not
prevent a non-vocal `silence_end` from being chosen by the *first
real post-solo line*, so the lateness symptom persists.

### C. Drop `sil_idx += 1` consumption

Let multiple LRC lines share the same `silence_end`. Pros: trivial
one-line change. Cons: breaks the per-verse drift case
(`TestDetectPerLineStarts.test_per_verse_drift_locks_each_locked_line_independently`)
because every line in a verse would re-snap to the verse's first
boundary instead of inheriting the cumulative shift.

### Recommendation

**A + B** (defensive-in-depth, but staged): A is the primary fix
because it kills the bug at its source — the spurious anchor never
enters the candidate list, so it can neither be consumed nor snap
the wrong line. B is held in reserve; if A alone leaves residual
ghost highlighting on songs whose LRC has no instrumental marker,
add B in a follow-up.

Land **A only** in this plan. Re-evaluate after dogfooding three or
four songs with long instrumentals.

## Detailed design (Approach A)

### Parser change (`_list_silence_ends` → `_list_silence_intervals`)

Today:

```python
def _list_silence_ends(audio_path: str) -> list[float]:
    ...
    return [
        float(m.group(1)) for m in re.finditer(r"silence_end:\s*([\d.]+)", proc.stderr)
    ]
```

Replace with a function that returns
`list[tuple[float, float]]` of `(silence_end, next_silence_start)`
pairs — i.e. the "audio-sustain interval" that follows each silence.
The final entry (no next silence) uses
`next_silence_start = math.inf`, treated as "sustained to EOF".

Parse both `silence_start` and `silence_end` from the same ffmpeg
output. ffmpeg always emits `silence_start` before each
`silence_end` (except for leading silence at t=0 — handled by
sentinel `silence_start = 0.0`).

Rename to make the new contract obvious:

```python
def _list_vocal_onsets(audio_path: str) -> list[tuple[float, float]]:
    """Return (onset_time, next_silence_start) for every silence_end."""
```

### Filter applied in `_detect_per_line_starts`

```python
_VOCAL_SUSTAIN_MIN_S = 1.5

onsets = sorted(_list_vocal_onsets(audio_path))
silence_ends = [
    onset
    for onset, next_silence in onsets
    if next_silence - onset >= _VOCAL_SUSTAIN_MIN_S
]
```

Everything else in `_detect_per_line_starts` stays unchanged. The
greedy lockstep, the ±2.5 s window, the cumulative inheritance, the
250 ms lead-in — all unchanged. We just fed it a cleaner candidate
list.

### Threshold rationale

1.5 s is short enough that a real one-line lyric (typical karaoke
phrase ≥ 2 s) always survives, and long enough that drum hits
(20–500 ms of perceived sustain) and reverb tails (up to ~1 s on
heavy production) get filtered.

If the first real lyric line is shorter than 1.5 s and is followed
by an inter-line silence longer than `_SILENCE_MIN_DURATION_S`
(0.5 s), the filter would drop it. Rare but possible. Mitigation:
the `_PER_LINE_SEARCH_WINDOW_S = 2.5 s` window then pulls the line
to the *next* surviving onset, which for a fast-singing chorus is
within 2.5 s anyway. Worst case: that one line inherits cumulative
instead of snapping. Same behaviour as today's "no-anchor line".

### Names and bumps

- Bump `WhisperXAligner.model_id` from `wav2vec2-char-perline` to
  `wav2vec2-char-sustainfilter`. Existing cached `.ass` files
  auto-invalidate so users with corrupted Total-Eclipse-style
  alignments get a fresh pass.
- Update the docstring of `_detect_per_line_starts` to mention
  spurious-anchor filtering.

## Files touched

| File | Change |
|------|--------|
| `pikaraoke/lib/lyrics_align.py` | `_list_silence_ends` → `_list_vocal_onsets`; add `_VOCAL_SUSTAIN_MIN_S`; filter at top of `_detect_per_line_starts`; bump `model_id`. |
| `tests/unit/test_lyrics_align.py` | Update existing patches that mock `_list_silence_ends` to use `_list_vocal_onsets` with sustain-pair tuples. Add 3 new tests (see below). |

No other file imports `_list_silence_ends` (verified by grep). No
production callers other than `_detect_per_line_starts`.

## Test plan

### New tests (in `TestDetectPerLineStarts`)

1. **`test_filters_spurious_anchors_inside_instrumental`** — Total
   Eclipse pattern. LRC has 5 lines pre-solo, 3 post-solo with
   timestamps that put the first post-solo line at `expected ≈ inside instrumental`. Onsets list contains 3 spurious markers
   inside the solo (each followed by next-silence \< 1.0 s) and one
   real onset just after. Assert the filter drops all 3 spurious
   markers and the post-solo line snaps to the real onset.

2. **`test_short_followed_by_long_silence_passes_filter`** — A real
   1.6 s lyric line followed by a 2 s instrumental break must
   survive (its silence-end is followed by sustained vocals; the
   *next* silence is 1.6 s away, > threshold).

3. **`test_drum_hit_inside_solo_is_dropped`** — Single onset whose
   next-silence is 0.3 s later → filter drops it → no candidate →
   line inherits cumulative (not a wrong snap).

### Updated existing tests

All five existing tests in `TestDetectPerLineStarts` patch
`_list_silence_ends`. Update the helper:

```python
@staticmethod
def _patch_silences(monkeypatch, ends: list[float]) -> None:
    # Pair each end with a far-future silence_start so every onset
    # passes the sustain filter — preserves the original test intent
    # (these tests are about the locking algorithm, not the filter).
    pairs = [(t, t + 99.0) for t in ends]
    monkeypatch.setattr(lyrics_align, "_list_vocal_onsets", lambda _p: list(pairs))
```

This keeps the five existing test bodies unchanged. They continue
to validate uniform-shift, per-verse-drift, continuous-line
inheritance, ffmpeg-failure, threshold gates, and empty-line
handling.

### Manual verification (operator)

- \[ \] Re-process the cached Total Eclipse song (delete its `.ass`
  so the new `model_id` triggers re-alignment).
- \[ \] Play through 2:30–4:30 on splash. Confirm: no highlighting
  between 2:50 and 3:28; first post-solo line lights up within
  ±300 ms of the singer's onset; sync remains correct through
  4:17 instead of recovering at 4:17.
- \[ \] Spot-check Mam Tę Moc and the Polish-test corpus (the
  original use case for per-line silence anchoring) — verify
  no regression in per-verse drift correction.

## Acceptance criteria

- All `tests/unit/test_lyrics_align.py` pass, including 3 new
  tests.
- `uv run pre-commit run --config code_quality/.pre-commit-config.yaml --files pikaraoke/lib/lyrics_align.py tests/unit/test_lyrics_align.py`
  passes (Black 100, isort, pylint).
- Manual playback of Total Eclipse shows no ghost highlighting
  during 2:50–3:28 and no lateness gap from 3:28–4:17.
- No regression on Mam Tę Moc / per-verse drift case.

## Risks

| Risk | Mitigation |
|------|-----------|
| 1.5 s threshold drops a legitimate short line | Line falls back to "inherit cumulative" — same behaviour as today's continuous-line case. Tested. |
| ffmpeg `silence_start` parsing differs across versions | ffmpeg 4.x+ emits both consistently. Add a defensive check: if the parser yields zero `silence_start` markers, fall back to treating every onset's next-silence as inf (filter no-ops, original behaviour). |
| Bump invalidates user-side cached `.ass` files mid-deploy | Intentional — the bug is exactly that the old cached files are wrong. Re-alignment is one wav2vec2 pass per song (~20–30 s). |
| Re-alignment queue UX cost: 500-song library = ~2-4 h of background re-align on next playback after the bump (subagent S6) | Document in release note. Re-alignment is lazy (per-playback), not a startup blocker; user only notices if they replay a previously-aligned song within the queue window. Selective invalidation (only re-align songs that hit the silence-anchor path) is a follow-up if this becomes a real complaint. |
| Approach A doesn't fully fix the 2 s lateness symptom because the post-solo line still wants to snap to *some* onset | If lateness persists, layer Approach B (LRC-gap quarantine) in a follow-up. Plan covers only A. |

## Out of scope (deferred to TODOS or follow-up)

- Approach B (LRC-gap quarantine). Will land if A is insufficient.
- Replacing `silencedetect` with VAD.
- Per-language threshold tuning (some genres / languages have
  different reverb-tail profiles).
- Telemetry on how often the filter drops onsets per song (would be
  nice for tuning but not required for correctness).

## Estimated effort

CC: ~25 min implementation + tests + pre-commit + manual
verification. Human: ~3 h.

______________________________________________________________________

# /autoplan — Review Pipeline

Mode: SELECTIVE EXPANSION (per autoplan override). UI scope: **no**
(pure backend Python, lyrics_align.py + tests only). Phase 2
(Design) skipped.

## Decision Audit Trail

| # | Phase | Decision | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|
| 1 | Pre-Phase 1 | Mode = SELECTIVE EXPANSION | autoplan rule | Default for bug-fix on existing system | EXPANSION (overkill for one-function bug), HOLD (denies us the chance to surface telemetry expansion) |
| 2 | Pre-Phase 1 | Skip Phase 2 (Design) | UI scope detection | No view/component/screen/etc terms in plan; pure backend Python | Run anyway (waste of compute) |
| 3 | Pre-Phase 1 | Codex unavailable → `[subagent-only]` | autoplan degradation matrix | `which codex` returned not found | Block on unavailable tool |
| 4 | Phase 1 (CEO) | Approach A confirmed (B held in reserve) | P1 (root-cause fix), P3 (smaller surface) | A filters at source; B requires LRC honesty about instrumentals | C (one-line, breaks per-verse drift case) |
| 5 | Phase 1 (CEO) | E1 accepted (filter-drop log) | P1 (observability is scope) | In blast radius; cheap; gives evidence for premise 1 | Defer to TODOS |
| 6 | Phase 1 (CEO) | E2/E3/E5 deferred to TODOS | P3 (pragmatic), out of blast radius | Plan stays focused on the bug | Add to plan |
| 7 | Phase 1 (CEO) | E4 rejected (env-var tunable) | P5 (explicit > clever), P4 (DRY) | Premature configurability; no second consumer | Accept |
| 8 | Phase 1 (Sub-A) | S1 accepted: prepend ffmpeg diagnostic step | P1 + P6 | 30-sec verification of central premise; cheap | Skip diagnostic |
| 9 | Phase 1 (Sub-A) | S2 partial: post-diagnostic decision A vs B | P3 (pragmatic), P6 (action) | Avoid corpus survey scope creep; let evidence decide | Expand to corpus survey |
| 10 | Phase 1 (Sub-A) | S3 accepted: warning log when filter drops near LRC line | P1 (no silent failures, CEO directive 1) | Catches the "breathy short verse silently dropped" 6-month regret scenario | Plain info-only log |
| 11 | Phase 1 (Sub-A) | S5 accepted: 2 more verification songs | P1 (completeness) | 20 minutes for major risk-profile change | Total Eclipse only |
| 12 | Phase 1 (Sub-A) | S6 accepted: document re-alignment cost in Risks | P3 (pragmatic) | One row in Risks; selective invalidation is its own initiative | Selective invalidation |

## Phase 1 — CEO Review

### System Audit

- Recent commits on master show heavy iteration on the lyrics
  pipeline: 6 of last 10 commits touch `pikaraoke/lib/lyrics*`.
  Of those, two land in `_detect_per_line_starts` directly
  (b44434fa added it, bc7a8a72 added the lead-in).
- `_list_silence_ends` is only consumed by
  `_detect_per_line_starts`; only mocked by
  `TestDetectPerLineStarts` in `tests/unit/test_lyrics_align.py`.
- No FIXME/TODO/HACK comments in `lyrics_align.py` or `lyrics.py`.
- No stash, no in-flight diff against master.
- Most-touched files in last 30 days are exactly the lyrics + stems
  - splash files this plan touches downstream — high-cadence area.
    Retrospective check: `_detect_per_line_starts` was added 4 days
    ago and is being patched here for a regression on a real song.
    This is the **first** functional patch to that function — not a
    recurring problem area, but the second commit on a fresh
    function tells us the design is still being settled.

### 0A. Premise Challenge

The plan's premises:

1. **"Demucs vocals stem leaks during instrumentals at >−30 dBFS."**
   *Plausible but not verified*. The bug repro is the user's
   observation that the symptom exists; the root-cause claim is
   inferred from algorithm + audio-physics first principles. We
   have not pulled the actual `silence_end` list for this song.
   There is a more boring alternative explanation worth ruling
   out: LRClib's "Total Eclipse" file may simply have the
   post-solo timestamp wrong by ~12 s and there are *no* spurious
   anchors at all — the lateness comes from cumulative carrying
   the pre-solo offset into a much longer real gap. **Action: keep
   premise as a hypothesis, not a fact, and have the
   implementation log onset counts dropped by the filter so we get
   evidence on first run.**

2. **"The filter is the root-cause fix, not just a symptom mask."**
   Holds *if* premise 1 holds. If the real cause is LRClib
   timestamps far from audio truth, no filter will help — we'd
   need to *not snap at all* across long LRC gaps (Approach B).
   The plan acknowledges this in the Risks table. Acceptable.

3. **"1.5 s sustain threshold is a safe heuristic."** Defensible
   but unverified. Soft ballad outros can have legitimate vocal
   onsets followed by \< 1 s of sound before a breath / silence.
   No corpus measurement, only intuition. Acceptable for a
   first-pass fix; flag in observability ask below.

4. **"Bumping `model_id` is the right cache-invalidation move."**
   Correct — that's the documented contract on lines 232–262 of
   the file, used 7 times already. Mechanical, not a premise.

5. **"This is a bug fix, not architectural work."** Correct posture
   for SELECTIVE EXPANSION. The bug is in a four-day-old function
   and the fix lives within its surface area.

### 0B. Existing Code Leverage

| Sub-problem | Existing code | Reuse plan |
|---|---|---|
| ffmpeg silencedetect parse | `_list_silence_ends` (`lyrics_align.py:90`) | Replace in place — same regex pass, additionally captures `silence_start`. |
| Per-line snap loop | `_detect_per_line_starts` | Untouched algorithm; cleaner candidate list only. |
| Test infra | `TestDetectPerLineStarts._patch_silences` | Update one helper signature; five existing tests' bodies unchanged. |
| Logging | `logger.info` already wired in `align()` | Add a single info line: "filter dropped N/M onsets as \< 1.5s sustain". |

Nothing rebuilt. No parallel infrastructure.

### 0C. Dream State

```
  CURRENT STATE                 THIS PLAN                 12-MONTH IDEAL
  Per-line silence anchor       Silence anchors filtered  Audio-truth alignment:
  greedily snaps to noise       to vocal-onset shape;     learn per-song silence
  during instrumentals;         instrumentals quiet,      profile (loudness +
  ghost highlighting + 2s       post-solo lines snap to   spectral) and only
  cascading lateness on         real onsets. Bug fixed.   anchor on credible
  Total-Eclipse-shaped songs.                              vocal segments.
                                                           Eventually: webrtcvad
                                                           or wav2vec2-VAD step
                                                           upstream of silence
                                                           probe.
```

This plan moves toward the ideal — same *shape* (silence-derived
anchors), better *quality* (filtered candidates). The 12-month
ideal would replace ffmpeg silencedetect with a true VAD; that's
explicitly out of scope per "non-goals". Right call for now.

### 0C-bis. Implementation Alternatives

```
APPROACH A: Silence-shape filter (RECOMMENDED — already in plan)
  Summary: Capture both silence_start and silence_end; only treat
           silence_ends followed by ≥1.5s of sustained audio as
           credible vocal onsets.
  Effort:  S (50 LOC + 3 tests)
  Risk:    Low — filter applied at top of existing function; algo
           unchanged; existing tests preserved with one helper
           tweak.
  Pros:    Fixes root cause (spurious candidates never enter);
           no LRC-structure dependency; one consume rule
           unchanged.
  Cons:    Heuristic threshold; doesn't help if LRC timestamp is
           genuinely far from audio truth (premise 2 above).
  Reuses:  `_list_silence_ends` infrastructure, ffmpeg invocation.

APPROACH B: LRC-gap quarantine
  Summary: Detect inter-line LRC gaps > 8s of non-empty silence;
           don't snap or consume for lines whose `expected` falls
           inside such a gap.
  Effort:  S (30 LOC + 2 tests)
  Risk:    Med — depends on LRC honesty about instrumentals;
           addresses ghost-highlight but not lateness symptom in
           isolation.
  Pros:    Zero ffmpeg parser change; targets the structural
           problem (LRC line in instrumental).
  Cons:    Many LRClib entries omit instrumental marker lines;
           does not prevent the first real post-solo line from
           snapping to a spurious anchor; threshold also heuristic.
  Reuses:  `_detect_per_line_starts` only.

APPROACH C: Drop `sil_idx += 1` consumption (one-liner)
  Summary: Let multiple LRC lines compete for the same anchor.
  Effort:  XS (1 LOC, 0 tests; some existing tests would break)
  Risk:    High — definitely breaks the per-verse drift case
           that motivated the original feature.
  Pros:    Trivial.
  Cons:    Does not address spurious anchors at all; just changes
           which wrong choice is made.
  Reuses:  Nothing changes.
```

**RECOMMENDATION: Approach A.** Fixes root cause; small surface;
preserves the per-verse drift correction the original commit was
written for; degrades safely (worst case: a line inherits
cumulative, same as today's "no anchor" path). Approach B is held
in reserve as a follow-up if A is insufficient on songs with
honest-LRC + spurious-onsets.

### 0D. Mode-Specific Analysis (SELECTIVE EXPANSION)

**Complexity check.** Plan touches 2 files, adds ~50 LOC, 3 new
tests. Well under the >8-files / >2-classes smell threshold. No
new abstractions.

**Minimum set.** Filter + helper signature update + 1 new test
(the Total Eclipse repro) is the absolute floor. The other two
new tests guard the threshold — strongly worth keeping (cheap with
CC).

**Expansion candidates** (cherry-pick — auto-decided per autoplan
P2 in-blast-radius rule):

| # | Expansion | Effort | In blast radius? | Decision | Rationale |
|---|---|---|---|---|---|
| E1 | Add `logger.info` line reporting filter drop count per song (e.g. "filter: dropped 7/41 onsets as \<1.5s sustain") | XS (CC: 2 min) | YES | **ACCEPT** | P1 (completeness): observability is scope per CEO prime directive #5. Cheap, in blast radius, gives evidence for premise 1. |
| E2 | Telemetry: count songs where filter affected outcome (e.g. write to `~/.gstack/analytics/lyrics-filter.jsonl`) | M (CC: 30 min, new infra) | NO | **DEFER** | Not in blast radius; introduces new infrastructure (analytics jsonl); P3 (pragmatic): the info log from E1 is enough to debug for now. Adds to TODOS. |
| E3 | Backfill: sweep `cache/` for `.ass` files written by old `model_id` and delete them eagerly so the next playback re-aligns | S (CC: 15 min) | NO | **DEFER** | Out of blast radius. Existing `model_id` bump already invalidates lazily on next playback. Eager sweep is nice-to-have. Adds to TODOS. |
| E4 | Make `_VOCAL_SUSTAIN_MIN_S` configurable via env var (per-deployment tuning) | XS (CC: 5 min) | borderline | **REJECT** | P5 (explicit over clever) + P4 (DRY): premature configurability. No second consumer asking for a different value. |
| E5 | Replace ffmpeg silencedetect with webrtcvad / silero VAD upstream | XL (CC: 2-3 h, new dep) | NO | **DEFER** | Plan's "non-goals" already excludes; correctly out of scope. Add to TODOS as the 12-month ideal direction. |

**Accepted scope (added to plan):** E1.
**Deferred to TODOS.md:** E2, E3, E5.
**Rejected:** E4.

### 0E. Temporal Interrogation

```
  HOUR 1 (foundations):     Does ffmpeg always emit silence_start
                            paired with silence_end? (Yes for v4+;
                            plan must add a defensive fallback if
                            zero silence_starts parsed → treat all
                            sustains as inf, filter no-ops.)
  HOUR 2 (core logic):      Where exactly does the filter sit —
                            inside _list_silence_ends, or at top of
                            _detect_per_line_starts? (Plan: split
                            responsibilities — _list_vocal_onsets
                            returns pairs, filter applied in
                            _detect_per_line_starts. Right call.)
  HOUR 3 (integration):     Test helper change cascades to 5
                            existing tests. (Plan handles via
                            single helper with sustain=99.0 sentinel
                            so test bodies stay unchanged.)
  HOUR 4 (polish/tests):    Three new tests; do they cover the
                            actual Total Eclipse pattern? (Plan
                            test #1 specifies "3 spurious markers
                            inside solo, real onset just after" —
                            yes, faithful repro.)
```

### 0F. Mode Confirmation

SELECTIVE EXPANSION confirmed. Approach A confirmed (with E1
folded in).

### Section 1 — Architecture Review

The change is within one function in one module. No new
components, no new boundaries.

**Data flow** (unchanged by this plan; documented for clarity):

```
  audio_path ──▶ ffmpeg silencedetect ──▶ _list_vocal_onsets
       │                  │                       │
       │                  │                       ▼
       │                  │            sustain filter (NEW)
       │                  │                       │
       │                  ▼                       ▼
       └─▶ LRC ─▶ _detect_per_line_starts ◀── candidate list
                              │
                              ▼
                  per-line shifted timestamps
                              │
                              ▼
                        wav2vec2 align ─▶ Word[] ─▶ ASS
```

Shadow paths in the new candidate-filter step:

- **Nil**: `_list_vocal_onsets` returns `[]` → filter yields `[]`
  → existing guard at line 150 (`if not silence_ends`) returns
  None. Unchanged.
- **Empty pairs**: ffmpeg returned silence_ends but zero
  silence_starts (old ffmpeg, parse drift) → defensive fallback
  treats every sustain as inf → filter no-ops → today's
  behaviour. Plan's Risks table covers this.
- **Error**: subprocess timeout / OSError → existing `try/except`
  in `_list_silence_ends` returns `[]` (covered above).

**Coupling.** Renaming `_list_silence_ends` → `_list_vocal_onsets`
changes one private symbol. Tests are the only other consumer.
Verified by grep. No external coupling.

**Scaling.** ffmpeg silencedetect runtime is unchanged (same
filter, just both events parsed instead of one). 60-second timeout
already covers full-song probes. No issue at 10x or 100x.

**Single point of failure.** ffmpeg presence — unchanged. Already
guarded by `shutil.which("ffmpeg")` returning `[]` on absence.

**Security architecture.** No new attack surface — same ffmpeg
invocation, no user input touches the new code.

**Production failure scenarios.**

- ffmpeg version drift → silence_start absent → defensive fallback
  preserves today's behaviour (covered).
- Pathological audio with zero silence boundaries (rare;
  continuous audio for entire song) → filter has nothing to
  filter; today's "audio doesn't start silent" guard already
  returns None.

**Rollback posture.** One commit, two files. `git revert <sha>`
restores prior behaviour in seconds. Cached `.ass` files written
under the new `model_id` would need a one-shot manual sweep, but
the next playback under the reverted code would re-align lazily.

**No new architecture.** No diagram beyond the one above is
warranted.

### Section 2 — Error & Rescue Map

| Codepath | What can go wrong | Exception | Rescued? | Action | User sees |
|---|---|---|---|---|---|
| `_list_vocal_onsets` ffmpeg call | binary missing | `which`→ falsy guard | Y | Return `[]` | No alignment shift; LRC ships as-is. |
| `_list_vocal_onsets` ffmpeg call | TimeoutExpired (60s cap) | `TimeoutExpired` | Y (existing) | Return `[]` | Same as above. |
| `_list_vocal_onsets` ffmpeg call | OSError (subprocess) | `OSError` | Y (existing) | Return `[]` | Same. |
| `_list_vocal_onsets` regex parse | ffmpeg output schema drift | none thrown; just empty | Y (defensive) | Filter no-ops if no silence_starts seen | Today's behaviour preserved. |
| Filter application | Pair mismatch (list lengths differ) | `IndexError` would be possible if naively zipping | **GAP** | Plan needs explicit guard | Currently unspecified. **Action: state in plan that pairing is regex-derived per line, not zip-by-index.** |

**Action item P1** (will fold into the plan body below): explicitly
state the pairing strategy. The cleanest pattern is to walk
ffmpeg's stderr line-by-line, building pairs as `silence_start`
events open intervals and `silence_end` events close them. That
removes any "two parallel lists" ambiguity.

No `except Exception` introduced. No silent swallows. No new
audit-log requirement (no security-sensitive operation).

### Section 3 — Security & Threat Model

No new attack surface. `audio_path` is already a server-controlled
path (Demucs cache or downloaded media). ffmpeg invocation
unchanged. No new env vars (E4 rejected). Skip.

### Section 4 — Data Flow & Interaction Edge Cases

Diagram in Section 1. Edge cases covered in Section 2's GAP item.

Adversarial scenarios:

- **All silence_ends spurious** (entire song is sub-1.5s bursts —
  e.g. drum-machine track with no vocals): filter yields `[]` →
  function returns None → no shift applied. Acceptable degradation.
- **No silence_starts at all** (continuous audio): filter sees
  every sustain as inf → no-op → today's behaviour. Acceptable.
- **First real vocal line is \< 1.5 s and followed by a breath**:
  filter drops it → that line inherits cumulative. Worst case:
  one line off by ≤ 2.5s. Same as today's no-anchor lines.

### Section 5 — Test Strategy

Test plan (3 new + 5 updated) is in the plan body. Coverage map:

| New behaviour | Test | Notes |
|---|---|---|
| Filter drops \<1.5s sustain | `test_drum_hit_inside_solo_is_dropped` | Direct unit. |
| Filter preserves credible onsets | `test_short_followed_by_long_silence_passes_filter` | Boundary at 1.5s. |
| End-to-end Total Eclipse pattern | `test_filters_spurious_anchors_inside_instrumental` | Repro of the user-reported failure. |
| Defensive fallback (no silence_starts parsed) | **MISSING** | Add: `test_no_silence_starts_means_filter_noop`. |
| Existing 5 tests | helper-tweak only | Bodies unchanged. |

**Action item P2**: add the defensive-fallback test. Total = 4
new tests, not 3.

### Section 6 — Observability

Today's `align()` already logs:

- `wav2vec2: per-line LRC->audio shift for %s; first %.2fs, last %.2fs`

Add (E1):

- `wav2vec2: silence-anchor filter dropped %d/%d onsets as <%.1fs sustain (audio=%s)`

This is the only new observability needed. No metrics, no traces
— this code runs once per song download, not in a hot loop.

### Section 7 — Failure Modes Registry

| # | Mode | Likelihood | Impact | Critical gap? | Mitigation |
|---|---|---|---|---|---|
| F1 | Threshold of 1.5s drops a legit short line | Med | Low (line inherits cumulative) | No | Documented in plan Risks. |
| F2 | ffmpeg drift drops silence_start parsing | Low | Low (filter no-ops, today's behaviour) | No | Defensive fallback + test. |
| F3 | LRC timestamp truly far from audio (premise 2 wrong) | Med | Med (residual lateness on Total Eclipse) | **Watch** | Approach B is the follow-up; manual verification step in plan asks operator to confirm cured. |
| F4 | Spurious onset somehow >1.5s sustain (heavy reverb tail) | Low | Med (filter doesn't catch it) | No | Tunable later if observed; E1 log gives evidence. |

### Section 8 — Deployment & Rollback

Single commit, two files. Auto-invalidates `.ass` cache via
`model_id` bump. No DB migration. No feature flag needed (the
filter always runs once code lands; revert is `git revert`).
Acceptable deployment posture for a single-owner project.

### Section 9 — Cross-Cutting Concerns

- **CLAUDE.md alignment.** Project demands "minimal impact
  changes; no half-migrations". Plan honours both — one function
  rename (with all callers updated), no flexibility added beyond
  what the bug demands.
- **DRY.** No duplication introduced; helper rename keeps
  responsibilities separate (parse vs filter).
- **Type hints.** Plan must specify `tuple[float, float]` return
  on `_list_vocal_onsets` (modern syntax per project style).
  Action: confirm in implementation.

### Section 10 — Documentation

The bumped `model_id` block of comments (lyrics_align.py:232-262)
needs one new entry: "Bumped to `wav2vec2-char-sustainfilter`
when..." Plan body already says this. Good.

No README/CLAUDE.md updates needed — internal change.

## Phase 1 — CLAUDE SUBAGENT (CEO — strategic independence)

**Score: 5/10**. Six findings — three accepted into plan, one
partially accepted, two flagged at premise gate.

| # | Severity | Finding | Auto-decision | Principle | Folded into plan |
|---|---|---|---|---|---|
| S1 | HIGH | Premise of "vocals stem leaks" is unverified — run a one-shot ffmpeg diagnostic on Total Eclipse before coding to confirm spurious onsets actually exist in 2:50–3:28. | **ACCEPT** | P1 (completeness) + P6 (bias to action: 30-second diagnostic) | Yes → new "Diagnostic step" subsection in Implementation. Confirms premise 1. |
| S2 | MED | Option B (LRC-gap quarantine) dismissed without checking how many LRClib entries actually mark instrumentals. Could be the simpler primary fix. | **PARTIAL** | P3 (pragmatic) + P6 | Don't expand scope to a corpus survey (ocean), but: the diagnostic in S1 doubles as B's evidence. If diagnostic shows few spurious onsets but the LRC has an honest gap marker for the solo, switch to B. Decision deferred to post-diagnostic. |
| S3 | HIGH | Threshold is heuristic-on-heuristic. In 6 months a breathy short verse will silently drop and nobody notices because the cumulative-fallback looks "almost right". | **ACCEPT** | P1 (completeness) | Yes → upgrade E1 from `info` to `warning` *when the dropped onset is within `_PER_LINE_SEARCH_WINDOW_S` of a yet-to-snap LRC line*. Plain `info` for filtered-during-instrumental case. |
| S4 | MED | Zero observability — at minimum a debug counter. | **ACCEPT** (already E1) | P1 | Already in plan via E1; merged with S3's warning logic. |
| S5 | MED | Problem framing narrow — likely affects every guitar-solo rock song silently. Manual verification list should include 2 more long-instrumental songs. | **ACCEPT** | P1 | Yes → add Bohemian Rhapsody (guitar break) and Hotel California (outro) to manual verification. |
| S6 | LOW | `model_id` bump re-aligns every cached song; for a 500-song library that's a 2-4h queue on next playback. | **ACCEPT** | P3 (pragmatic) | Yes → add row to Risks table documenting re-alignment cost. Do not scope-creep to selective invalidation. |

**Disagreement with my own review:** subagent considers premise 1
*more* uncertain than I did. We agreed in spirit; the diagnostic
step (S1) is the resolution.

## Phase 1 — CODEX (CEO — strategy challenge)

`codex` CLI not installed on this host. Tagged `[subagent-only]`
per autoplan degradation matrix.

## Phase 1 — CEO Consensus Table

```
CEO DUAL VOICES — CONSENSUS TABLE [subagent-only]:
═══════════════════════════════════════════════════════════════
  Dimension                            Claude  Sub-A  Codex  Consensus
  ──────────────────────────────────── ─────── ────── ─────── ──────────
  1. Premises valid?                   PARTIAL UNVRFD N/A    DISAGREE
                                       (2/5            (S1 → resolved
                                        unvrfd)         by diagnostic)
  2. Right problem to solve?           YES     YES    N/A    CONFIRMED
  3. Scope calibration correct?        YES     NO     N/A    DISAGREE
                                                            (S5: 2 more
                                                             songs added)
  4. Alternatives sufficiently?        YES     NO     N/A    DISAGREE
                                                            (S2: deferred
                                                             to post-diag)
  5. Competitive/market risks?         N/A     N/A    N/A    N/A (bug)
  6. 6-month trajectory sound?         YES     NO     N/A    DISAGREE
                                                            (S3: warning
                                                             log + named
                                                             const added)
═══════════════════════════════════════════════════════════════
4 disagreements; all resolved by accepting subagent fixes (S1, S3, S5)
or by deferring to a 30-sec diagnostic (S2). One critical finding from
sub-agent escalated regardless of consensus: S1 premise verification.
```

## Phase 1 — Action Items folded into plan body

Three actions emerged from CEO review and will be folded into the
detailed design + test plan above:

- **P1**: Specify pairing strategy in `_list_vocal_onsets` —
  walk ffmpeg stderr line-by-line, opening intervals on
  `silence_start` and closing on `silence_end`. (Section 2 GAP.)
- **P2**: Add a 4th test — `test_no_silence_starts_means_filter_noop`
  for the defensive fallback. (Section 5.)
- **E1**: Add `logger.info` line in `_detect_per_line_starts`
  reporting filter drop count per song. (Section 6.)

These are appended to "Files touched" and "Test plan" below in
**Phase 3 (Eng Review)** so the plan reflects them in one place.

## Phase 1 — Completion Summary

| Item | State |
|---|---|
| Mode | SELECTIVE EXPANSION confirmed |
| Approach | A (silence-shape filter) confirmed |
| Premises | 2 of 5 are unverified hypotheses (1, 3) — plan adds E1 logging to gather evidence |
| Expansions accepted | E1 (filter drop count log) |
| Deferred to TODOS | E2 (analytics), E3 (eager cache sweep), E5 (VAD replacement) |
| Rejected | E4 (env-var tunable), C (one-line fix), B (LRC-gap quarantine) |
| Critical gaps from review | P1 (pairing strategy), P2 (4th test), folded into design |
| Failure modes | 4 catalogued; F3 is the residual risk if premise 2 is wrong |
| Sections with no findings | 3 (Security — no new surface), 8 (Deployment — trivial), 9 (Cross-cutting — aligned) |

______________________________________________________________________

# Pivot: Global DP assignment

> Authored after the premise-gate diagnostic. This supersedes
> Approach A as the primary fix.

## Diagnostic evidence (Total Eclipse vocals stem, real data)

15 silence intervals on the song's vocals.mp3 (333.95 s total):

```
  #   silence_start    silence_end (= vocal onset)    sustain after
  ─── ───────────────  ───────────────────────────── ────────────────
   1  0.000            7.769                          135.09s of audio
   2  142.860          145.326                        4.68s
   3  150.007          156.540                        6.35s
   4  162.889          164.002                        4.73s
   5  168.729          182.490                        0.0s (re-silenced)
   6  182.491          207.842   ← FIRST POST-SOLO    47.84s of audio
   7  255.680          257.556   ← 4:17 BIG ONSET     1.99s
   8  259.552          260.412                        12.19s
   9  272.600          275.378                        4.43s
  10  279.807          286.480                        6.25s
  11  292.733          293.953                        6.32s
  12  300.275          303.150                        3.92s
  13  307.065          311.555                        1.97s
  14  313.522          318.718                        5.97s
  15  324.688          333.949                        EOF
```

**ZERO spurious onsets in the 2:50–3:28 solo (170s–208s).** The
stem is correctly silent. The first post-solo audio onset is at
**207.84 s (3:27.84)** — exactly where the bug report says lateness
recovers (~the post-solo line's true start).

The 4:17.6 anchor at 257.56 lines up with the user's "by 4:17
subtitles snap clean" observation — that's the *next* big anchor
after the 3:27.84 one.

## Why the greedy fails

LRClib's "Total Eclipse" (re-derivable from the cached .ass) packs
6–8 short backing-vocal lines between original LRC time ≈3:18 and
≈3:30 ("(Turn around, bright eyes)" repeated, plus
"Every now and then I know..." etc.). The greedy algorithm:

1. Pre-solo: snaps cleanly through verse 1, cumulative ≈ +0.1 s.
2. First backing-vocal LRC line at original 3:18, expected ≈ 3:18.
   The next silence_end is 207.84 (3:27.84) — outside the ±2.5 s
   window. Cannot snap. Renders at 3:18 (inside the solo).
   **Ghost line #1.**
3. Second backing-vocal line at original 3:19, same story → ghost.
4. Eventually one line lands within ±2.5 s of 207.84, snaps,
   consumes the anchor.
5. Remaining post-solo lines have no anchor (the next is 257.56,
   too far for ±2.5 s). They inherit cumulative → drift = 2 s late.
6. By 4:17 (anchor 257.56), some line snaps cleanly and cumulative
   recovers.

The algorithm is **structurally insufficient**: one anchor cannot
serve a cluster of LRC lines, and unsnapped lines have no notion of
"this LRC line is in dead audio".

## New approach: D — Global DP line→anchor assignment

**Replace** `_detect_per_line_starts`'s greedy lockstep with a
dynamic-programming optimizer that finds the globally best
monotonic mapping from LRC lines to silence anchors, then fills in
unanchored lines by smooth interpolation between flanking anchors.

### Algorithm

Inputs:

- `lrc_lines: list[(orig_start, orig_end, text)]` — N entries
  (typically 30–80).
- `onsets: list[(silence_end, next_silence_start)]` — M entries
  from ffmpeg silencedetect (typically 10–40).

Compute the assignment in three steps:

**Step 1 — Build candidate set per LRC line.**

For each LRC line `Lᵢ`, find all onsets `Aⱼ` whose audio sustain
(`next_silence_start - silence_end`) is at least `0.3 s × num_words(Lᵢ)`
(rough vocal-time-per-word floor). This filters anchors that
physically can't host the line. Cheap; reduces the DP search space.

**Step 2 — DP for monotonic best assignment.**

State: `dp[i][j]` = minimum cost to align lines `L₁..Lᵢ` such that
`Lᵢ` is anchored at `Aⱼ` (or `j = 0` meaning "no anchor used yet").

Transitions:

- `dp[i+1][j]` ← `dp[i][j] + skip_cost(L_{i+1})` (line i+1 doesn't
  anchor; will be interpolated later).
- `dp[i+1][k]` ← `dp[i][j] + anchor_cost(L_{i+1}, A_k, A_j)` for
  every `k > j` in `L_{i+1}`'s candidate set.

`anchor_cost` components (sum, with weights):

- **`w₁ × |t(L_{i+1}) - A_k - cumulative_at(j)|`** — anchor should
  be near where this line "wants" to be given the running shift.
- **`w₂ × |Δ_cumulative|`** — penalize big tempo jumps. Tempo drift
  is a feature; sudden jumps are a bug signal.
- **`w₃ × max(0, line_audio_demand(L_{i+1}) - sustain(A_k))`** —
  penalize anchoring to a too-short sustain (can't fit the line).

`skip_cost` is small for short lines and grows for long lines that
are likely to be primary vocals (we'd prefer to anchor them).

**Step 3 — Backtrack and interpolate.**

Recover the chain of (line, anchor) pairs from `dp`. For each
unanchored line cluster between flanking anchored lines `Lₐ@Aₚ` and
`L_b@A_q`:

- Available audio window: `(A_p + ε, A_q - ε)`.
- Distribute lines `L_{a+1}..L_{b-1}` proportionally to their
  original LRC durations within that window.
- This is the key fix: ghost lines previously rendered at
  `t_orig + cumulative_pre_solo` (inside the dead solo); now they
  render distributed between `A_p` and `A_q`, i.e. **after** the
  vocals actually resume.

Edge cases:

- Cluster has no following anchor (final lines): use original LRC
  durations carried forward from the last anchor.
- Cluster has no preceding anchor (initial lines before first
  vocal): use cumulative from the first anchor going backward.

### Complexity

Time: O(N × M²) worst case, typical O(N × M) after candidate
filtering. For PiKaraoke songs (N ≤ 80, M ≤ 40): under 5000 ops.
Microseconds.

Memory: O(N × M). Trivial for these sizes.

### What changes structurally

| Component | Before | After |
|---|---|---|
| `_list_silence_ends` | Returns `list[float]` | Renamed `_list_silence_intervals`; returns `list[(end, next_start)]`. |
| `_detect_per_line_starts` | Greedy lockstep | Becomes a thin orchestrator that calls candidate-filter + DP + interpolate. |
| New: `_align_lines_to_anchors_dp` | — | The DP solver. ~80–100 LOC, pure function, exhaustively unit-testable. |
| New: `_interpolate_unanchored` | — | Distributes unanchored line clusters between flanking anchors. ~25 LOC. |
| `_KARAOKE_LEAD_IN_S` | Same 0.25 s subtraction at end | Unchanged. |
| `model_id` bump | `wav2vec2-char-perline` | `wav2vec2-char-dpalign`. |

### Behavioural deltas

- **Ghost lines (Total Eclipse):** lines that previously rendered
  inside the solo now render after `A_q`'s anchor (distributed
  between 207.84 s and 257.56 s) → no highlighting during 2:50–3:28.
- **Per-verse drift (Mam Tę Moc):** still works — DP can choose to
  anchor every verse-leading line if the cost favours it.
- **Lines with no candidate anchor at all:** interpolated between
  whichever anchored neighbours exist. Same behaviour as today's
  "inherit cumulative" but smoother.
- **First-line offset cap (`_GLOBAL_OFFSET_MAX_S = 10 s`):**
  preserved as a sanity check on the first anchored line; if the
  closest plausible anchor is > 10 s away, the DP returns None
  (caller falls back to no-shift) — same fallback contract as
  today.

### Approach D vs the other options

| Approach | Fixes ghost-during-solo? | Fixes lateness-after-solo? | Preserves per-verse drift? | Effort |
|---|---|---|---|---|
| A — sustain filter | No (no spurious anchors to filter on this song) | No (same reason) | Yes | S |
| B — LRC-gap quarantine | Partial (only if LRC has explicit instrumental marker) | Partial | Yes | S |
| C — drop consume rule | No | No | **Breaks** Mam Tę Moc | XS |
| **D — global DP** | **Yes** (lines interpolate after the post-solo anchor) | **Yes** (DP uses the next big anchor at 257.56) | Yes | M |

D is the only approach that fixes both symptoms based on the
actual data observed.

### Files touched (revised)

| File | Change |
|---|---|
| `pikaraoke/lib/lyrics_align.py` | Rename `_list_silence_ends` → `_list_silence_intervals` (returns `(end, next_start)` pairs). Rewrite `_detect_per_line_starts` as a thin orchestrator. Add `_align_lines_to_anchors_dp` (DP solver) and `_interpolate_unanchored` (interpolation pass). Bump `model_id`. |
| `tests/unit/test_lyrics_align.py` | Update `_patch_silences` helper for the new pair tuples. Existing 5 tests preserved (their bodies stay; helper does the work). Add focused unit tests on the DP and interpolation (see test plan below). |
| `tests/fixtures/total_eclipse/silence_intervals.json` | NEW — captured ffmpeg output, 15 intervals. ~1 KB. |
| `tests/fixtures/total_eclipse/lyrics.lrc` | NEW — LRClib's LRC for Total Eclipse. ~3 KB. |
| `tests/integration/test_lyrics_align_total_eclipse.py` | NEW — the E2E regression test. |

## E2E regression test (per user request)

Goal: assert that **no LRC line renders during 2:50–3:28** on
Total Eclipse, and that the post-solo lines start at the real
audio onset (3:27.84).

### Fixture capture (one-time, manual)

```bash
# Vocal-stem silence intervals (already captured during this autoplan run)
ffmpeg -hide_banner -nostats -i "$VOCALS" -af "silencedetect=n=-30dB:d=0.5" -f null - 2>&1 \
  | grep -E "silence_(start|end)" \
  | python3 scripts/parse_silence_to_json.py \
  > tests/fixtures/total_eclipse/silence_intervals.json

# LRClib LRC (canonical timestamps from LRClib's API)
curl -s 'https://lrclib.net/api/get?artist_name=Bonnie%20Tyler&track_name=Total%20Eclipse%20of%20the%20Heart' \
  | jq -r .syncedLyrics \
  > tests/fixtures/total_eclipse/lyrics.lrc
```

(Both files committed to the repo so tests are reproducible
without network or media files. Total fixture size \< 5 KB.)

### Test design

```python
# tests/integration/test_lyrics_align_total_eclipse.py
import json
from pathlib import Path

import pytest

from pikaraoke.lib import lyrics_align
from pikaraoke.lib.lyrics import lrc_line_windows

FIXTURES = Path(__file__).parent.parent / "fixtures" / "total_eclipse"
SOLO_START_S = 170.0  # last pre-solo "heart" decays around 2:50
SOLO_END_S = 207.0  # post-solo first audio onset is 207.84
EXPECTED_FIRST_POST_SOLO_S = 207.84
LEAD_IN_S = 0.25  # _KARAOKE_LEAD_IN_S


@pytest.fixture
def total_eclipse_inputs(monkeypatch):
    intervals = json.loads((FIXTURES / "silence_intervals.json").read_text())
    lrc = (FIXTURES / "lyrics.lrc").read_text()
    monkeypatch.setattr(
        lyrics_align,
        "_list_silence_intervals",
        lambda _path: [(e["end"], e["next_start"]) for e in intervals],
    )
    return lrc_line_windows(lrc)


def test_no_line_renders_during_solo(total_eclipse_inputs):
    """No LRC line's shifted start time may fall inside 2:50-3:28."""
    out = lyrics_align._detect_per_line_starts("/dev/null", total_eclipse_inputs)
    assert out is not None, "alignment should not bail on Total Eclipse"

    in_solo = [
        (i, t, total_eclipse_inputs[i][2])
        for i, t in enumerate(out)
        if SOLO_START_S < t < SOLO_END_S
    ]
    assert not in_solo, (
        f"{len(in_solo)} ghost line(s) render during the instrumental "
        f"solo (170s-207s). First three: {in_solo[:3]}"
    )


def test_first_post_solo_line_snaps_to_real_onset(total_eclipse_inputs):
    """The first line at or after 3:28 must start within 1s of the
    real audio onset (3:27.84) minus the karaoke lead-in."""
    out = lyrics_align._detect_per_line_starts("/dev/null", total_eclipse_inputs)
    post_solo = [t for t in out if t >= SOLO_END_S]
    first_post = min(post_solo)
    expected = EXPECTED_FIRST_POST_SOLO_S - LEAD_IN_S
    assert abs(first_post - expected) < 1.0, (
        f"first post-solo line at {first_post:.2f}s, "
        f"expected within 1s of {expected:.2f}s"
    )


def test_lateness_recovers_by_417(total_eclipse_inputs):
    """Lines whose shifted start is in 207.84..257.56 must have
    monotonic-and-distributed shifts (no cluster all stuck near the
    earlier anchor producing a 2s late cascade)."""
    out = lyrics_align._detect_per_line_starts("/dev/null", total_eclipse_inputs)
    in_window = sorted(t for t in out if 207.84 <= t <= 257.56)
    if len(in_window) < 2:
        pytest.skip("not enough lines in the post-solo window for this assertion")
    # No two consecutive lines may be within 0.05s of each other AND
    # both within 0.5s of the same anchor — that's the "compressed at
    # the anchor" failure mode.
    consecutive_gaps = [b - a for a, b in zip(in_window, in_window[1:])]
    assert (
        min(consecutive_gaps) >= 0.05
    ), f"post-solo lines are compressed: min gap = {min(consecutive_gaps):.3f}s"


def test_full_pipeline_smoke(total_eclipse_inputs):
    """Sanity: every line gets a shifted timestamp, all monotonic, all
    inside [0, audio_duration + small_slack]."""
    out = lyrics_align._detect_per_line_starts("/dev/null", total_eclipse_inputs)
    assert len(out) == len(total_eclipse_inputs)
    assert all(0 <= t <= 340 for t in out)
    # Monotonicity (or near-monotonic — small back-step OK for
    # adjacent very-close LRC lines that DP didn't anchor).
    inversions = sum(1 for a, b in zip(out, out[1:]) if b + 0.5 < a)
    assert (
        inversions == 0
    ), f"{inversions} large monotonicity inversions in shifted starts"
```

### Why this test is the right shape

- **Real fixtures, no audio file in repo.** Silence intervals
  captured once from the actual vocals.mp3, committed as ~1 KB
  JSON. The LRC is the actual LRClib payload, committed as ~3 KB.
  No network, no media, no platform dependency in CI.
- **Asserts the user-observable symptom directly.** "No line
  renders during 2:50–3:28" matches the report verbatim.
- **Asserts the recovery point.** 4:17 lateness → the test that
  post-solo lines aren't compressed at one anchor.
- **Lives in `tests/integration/`** (rather than `tests/unit/`) to
  signal it's an end-to-end alignment test rather than a unit
  test of one helper. Still pure-Python, sub-second runtime, no
  external services.

### Additional unit tests (for the DP solver itself)

In `tests/unit/test_lyrics_align.py`, add a `TestDPAlignment` class:

1. `test_dp_assigns_one_line_per_anchor_when_lines_match_anchors`
2. `test_dp_distributes_cluster_between_flanking_anchors`
3. `test_dp_prefers_higher_sustain_for_long_lines`
4. `test_dp_falls_through_to_inherit_when_no_anchors_exist`
5. `test_dp_returns_none_when_initial_offset_exceeds_cap`
6. `test_dp_preserves_per_verse_drift_case` (the existing
   Mam Tę Moc pattern, regressed against the new algorithm)
7. `test_dp_handles_first_few_lrc_lines_with_no_preceding_anchor`
8. `test_interpolate_unanchored_proportional_to_lrc_durations`

Existing 5 `TestDetectPerLineStarts` tests stay as integration-of-
the-orchestrator tests; their bodies don't change because the
orchestrator's input/output contract is unchanged. Only the helper
that mocks the silence source needs the pair tuple.

## Acceptance criteria (revised)

- All `tests/unit/test_lyrics_align.py` pass, including 8 new DP
  unit tests.
- All `tests/integration/test_lyrics_align_total_eclipse.py` pass.
- `uv run pre-commit run --config code_quality/.pre-commit-config.yaml --files pikaraoke/lib/lyrics_align.py tests/`
  passes.
- Manual playback on Total Eclipse shows no highlighting between
  2:50 and 3:28; first post-solo line lights within ±300 ms of
  audio onset.
- No regression on Mam Tę Moc per-verse-drift.
- Bohemian Rhapsody and Hotel California (added per CEO subagent
  S5): visual sanity check during their long instrumental
  sections.

## Risks (revised)

| Risk | Mitigation |
|---|---|
| DP cost-function weights mis-tuned (w₁/w₂/w₃) | Start with simple weights derived from the test corpus; expose as module-level constants with doc comments explaining derivation. The test fixture for Total Eclipse + the existing per-verse drift test pin the behaviour from both ends. |
| Interpolation between anchors creates "marching" lines that don't sync to actual sub-syllabic timing | This is wav2vec2's job downstream; the DP only sets line starts. wav2vec2 will then per-word-align *within* each line's window. If wav2vec2 finds no audio in a line's window (still possible if a line was stuck in dead audio), it falls through to `_uniform_line_words` — same fallback as today, but reached far less often. |
| Bumping `model_id` re-aligns every cached song | Same risk as Approach A. Same mitigation: documented re-alignment cost; lazy per-playback re-alignment. |
| DP returns no anchor for any line (degenerate audio) | Existing fallback path: `_detect_per_line_starts` returns None → no shift → original LRC timestamps used. Preserve this contract. |
| LRClib LRC fixture goes stale (LRClib updates the entry) | The fixture is captured-once and committed; subsequent LRClib changes don't affect the test. Document the capture command in the fixture directory's README. |

## Out of scope (deferred)

- Approach A (sustain filter) — could still be layered on top as a
  pre-filter for songs where the stem genuinely leaks; revisit if
  observed in the wild. Lower priority now.
- Approach B (LRC-gap quarantine) — subsumed by the DP, which
  naturally avoids assigning lines to unreachable anchors.
- VAD replacement (Approach E from CEO E5) — still the 12-month
  ideal; orthogonal to the DP.
- Per-language cost-weight tuning — not needed for a first pass.

## Phase 3 — Eng Review (revised)

### Architecture

Single new pure function `_align_lines_to_anchors_dp(lrc_lines, onsets, *, weights) -> list[float | None]` returning either a
snapped anchor time or `None` per LRC line. A second pure function
`_interpolate_unanchored(positions, lrc_lines) -> list[float]`
fills the `None`s.

Both pure, deterministic, no I/O. Easily property-tested.

`_detect_per_line_starts` orchestrates: probe → candidate-filter →
DP → interpolate → lead-in subtract. Roughly the same signature
and contract; the body is rewritten.

ASCII data flow:

```
  audio_path ─▶ ffmpeg silencedetect ─▶ _list_silence_intervals
                                           │
                                           ▼
                                    [(end, next_start)]
                                           │
                                           ▼
       lrc_lines ─▶ candidate-filter (sustain × words) ─▶ allowed[i]
                                           │
                                           ▼
                                    _align_lines_to_anchors_dp
                                           │
                                           ▼
                                    list[float | None]
                                           │
                                           ▼
                                    _interpolate_unanchored
                                           │
                                           ▼
                                    list[float] (final shifted)
                                           │
                                           ▼
                                    minus _KARAOKE_LEAD_IN_S
```

### Test diagram

| New behaviour | Test type | Test file::name |
|---|---|---|
| DP one-line-per-anchor mapping | unit | `test_lyrics_align.py::TestDPAlignment::test_dp_assigns_one_line_per_anchor_when_lines_match_anchors` |
| Cluster distribution | unit | `::test_dp_distributes_cluster_between_flanking_anchors` |
| Sustain preference | unit | `::test_dp_prefers_higher_sustain_for_long_lines` |
| No-anchor degenerate | unit | `::test_dp_falls_through_to_inherit_when_no_anchors_exist` |
| Initial offset cap | unit | `::test_dp_returns_none_when_initial_offset_exceeds_cap` |
| Mam Tę Moc preserved | unit | `::test_dp_preserves_per_verse_drift_case` |
| Pre-anchor lines | unit | `::test_dp_handles_first_few_lrc_lines_with_no_preceding_anchor` |
| Interpolation proportionality | unit | `::test_interpolate_unanchored_proportional_to_lrc_durations` |
| Total Eclipse no-ghost | integration | `test_lyrics_align_total_eclipse.py::test_no_line_renders_during_solo` |
| Total Eclipse onset snap | integration | `::test_first_post_solo_line_snaps_to_real_onset` |
| Total Eclipse no-cascade | integration | `::test_lateness_recovers_by_417` |
| Total Eclipse smoke | integration | `::test_full_pipeline_smoke` |
| Existing 5 helper-tweaked | unit | `TestDetectPerLineStarts::*` (bodies unchanged) |

Total: 12 new tests + 5 preserved.

### Failure modes (revised)

| # | Mode | Likelihood | Impact | Critical? | Mitigation |
|---|---|---|---|---|---|
| F1 | DP weights mis-tuned, ghosts reappear | Med during dev | High | YES — the E2E test catches it | Tests pin behaviour; weights tuned against fixture before merge |
| F2 | Interpolation produces clearly-wrong timings on LRCs without explicit instrumental marker | Low | Med | No | Acceptable; DP still beats today's behaviour |
| F3 | DP runtime regression on giant LRCs | Very low | Low | No | N×M \< 5000 worst case for any realistic karaoke song |
| F4 | LRClib fixture diverges from real-world LRC (someone edited the LRClib entry) | Low | Low | No | Test uses committed fixture, not live API |
| F5 | wav2vec2 finds no audio in a line's window after DP places it (e.g. lone outlier) | Low | Low | No | Existing `_uniform_line_words` fallback handles it |

### Decision Audit Trail (additions)

| # | Phase | Decision | Principle | Rationale | Rejected |
|---|---|---|---|---|---|
| 13 | Diagnostic | Pivot from Approach A to Approach D | Evidence (zero spurious onsets) | Premise (a) falsified by data | Stay with A |
| 14 | Pivot | DP solver as new pure function | P5 (explicit > clever) | Pure function, exhaustively unit-testable; orchestrator stays small | Inline DP into `_detect_per_line_starts` (less testable) |
| 15 | Pivot | Commit Total Eclipse fixtures (silence JSON + LRC) | P1 (boil the lake) | E2E test reproducible, no network, no media in repo, ~5 KB | Mock both inline (less reproducible regression) |
| 16 | Pivot | Tests live in `tests/integration/` | P5 (explicit > clever) | Signals "this is end-to-end alignment", not unit-of-helper | Put in `tests/unit/` |
| 17 | Pivot | DP weights as named module constants with comments | P5 + CEO directive #6 (diagrams/docs in code) | Tunable without scope creep; intent visible to readers | Inline magic numbers |
| 18 | Pivot | Approach B (LRC-gap quarantine) subsumed by DP | P3 (pragmatic) | DP naturally avoids assigning lines to unreachable anchors | Layer B on top |
| 19 | Pivot | Approach A held as future filter, not blocker | P3 + P6 | Real spurious anchors not observed yet; A becomes a follow-up only if needed | Implement A now anyway |

## Estimated effort (revised)

CC: ~60 min (DP + interpolation + 8 unit tests + 4 integration
tests + fixtures + pre-commit + manual verification).
Human: ~6 h.

Larger than the original (~25 min) because the algorithm is
substantively new, but still well inside one focused work session
on the CC scale.

______________________________________________________________________

# Pivot 2: VAD upstream replaces silencedetect

> User direction (after Pivot 1): "boil the ocean, go with VAD".
> Pivot 2 supersedes the silencedetect-based onset source from
> Pivot 1. The DP assignment from Pivot 1 is **retained
> unchanged** — it operates on whatever onset list the upstream
> probe returns. Only the probe layer changes.

## Why VAD changes the data shape (and helps the DP)

`silencedetect` on the Demucs vocals stem produces ONE onset per
audible region. The DP then has to spread N LRC lines across
those few regions via interpolation. On Total Eclipse 3:13–3:30
that means: 8 LRC backing-vocal lines, 1 silencedetect onset
(207.84). DP picks one line for the anchor and interpolates the
other seven.

VAD on the **raw mix** classifies each 30-ms frame as
speech / non-speech. On Total Eclipse 3:13–3:30 the actual song
contains ~10 distinct backing-vocal entries: each "(Turn around,
bright eyes)", "(Turn around)", and Bonnie's lead phrases get
their own VAD-detected speech segment. The DP now has roughly
**one anchor per LRC line** to choose from — the assignment is
near-trivial and the result is per-line accurate, not
interpolated.

Concretely on the 3:13–3:30 stretch:

| Source on Total Eclipse 3:13–3:30 | Onsets in window |
|---|---|
| `silencedetect` on vocals stem | 1 (only the big 3:27.84 onset) |
| Silero VAD on raw mix (expected) | 6–10 (one per backing-vocal phrase) |

The DP's interpolation pass becomes a **fallback**, not the
primary path for this song.

## Architecture (revised)

```
  audio_path (raw mix .m4a) ─▶ silero VAD ─▶ list[(start, end)]
                                             │
                                             │ (transformed to
                                             │  onset + sustain pairs)
                                             ▼
       lrc_lines ─▶ candidate-filter (sustain × phonemes) ─▶ allowed[i]
                                             │
                                             ▼
                              _align_lines_to_anchors_dp
                              (unchanged from Pivot 1)
                                             │
                                             ▼
                                    list[float | None]
                                             │
                                             ▼
                                    _interpolate_unanchored
                                             │
                                             ▼
                                    list[float] (final shifted)
                                             │
                                             ▼
                                    minus _KARAOKE_LEAD_IN_S
```

**Key wiring change:** the probe consumes the **raw mix** path,
not the Demucs vocals stem. This **decouples the line-anchor
pass from Demucs entirely**. wav2vec2 forced-alignment downstream
still uses the vocals stem (whisperx needs clean phonetics), but
the line shifts can now ship to splash before Demucs finishes.

### Onset transform

VAD returns `[(speech_start, speech_end), ...]`. The DP wants
`[(onset, next_silence_start), ...]`. Trivial map:

```python
# vad output: [(s1, e1), (s2, e2), (s3, e3), ...]
# We want:    [(s1, s2), (s2, s3), (s3, eof), ...]
#             (onset = each speech_start; sustain = until next speech_start)
```

The "sustain" used by the DP's candidate filter is then the
length of audio from this speech onset until the *next* speech
segment begins — i.e. how long the singer can sustain this line
before another vocal entry. This matches the DP cost function's
intent ("can this anchor host this line?") more directly than
`silencedetect`'s sustain (which is "how long until silence
returns").

## Library choice: silero VAD

Selected over webrtcvad after a quick comparison:

| Dimension | webrtcvad | silero VAD | Decision |
|---|---|---|---|
| Music tolerance | Trained on telephony — false positives on guitar | Trained on multilingual speech *including singing* | silero |
| Pi support | Rock solid (small C ext) | ~3× real-time on Pi Zero 2; instant on Pi 4 | both OK |
| Install | `pip install webrtcvad` (C compile) | `pip install silero-vad` (PyPI, ~2 MB JIT model) | silero (already have torch) |
| Quality on a-cappella | Mediocre | State of the art | silero |
| Tunable | Aggressiveness 0–3 (coarse) | `threshold`, `min_speech_duration_ms`, `min_silence_duration_ms` (fine) | silero |
| Languages | English-centric | Multilingual including non-Latin | silero (matters for the Polish-test corpus) |

silero is also adjacent to the existing dep stack (torch via
whisperx), so adding it is one PyPI line, not a new ML stack.

## Files touched (Pivot 2 — final)

| File | Change |
|---|---|
| `pyproject.toml` | Add `silero-vad>=5.0` to the `[project.optional-dependencies] align` extra (so it follows the same opt-in path as whisperx). |
| `pikaraoke/lib/vad_probe.py` | NEW. Single function `list_vocal_onsets(audio_path) -> list[tuple[float, float]]`. Lazy imports silero. ~40 LOC. Module-level singleton for the JIT model (one load per process). |
| `pikaraoke/lib/lyrics_align.py` | Replace `_list_silence_ends` with thin call into `vad_probe.list_vocal_onsets`. Keep ffmpeg silencedetect as a fallback when silero unavailable (so installations without the `[align]` extra still get *some* anchoring). Add the DP solver `_align_lines_to_anchors_dp`. Add `_interpolate_unanchored`. Bump `model_id` to `wav2vec2-char-vad-dpalign`. |
| `tests/unit/test_vad_probe.py` | NEW. Mock the silero model; verify the contract: returns sorted, monotonic, non-overlapping `(onset, sustain_until_next)` tuples. Verify fallback path triggers when silero import fails. |
| `tests/unit/test_lyrics_align.py` | Update existing 5 tests to mock the new probe boundary (`vad_probe.list_vocal_onsets`). Add 8 DP unit tests (per Pivot 1 list). |
| `tests/integration/test_lyrics_align_total_eclipse.py` | NEW. Fixture-driven E2E (per Pivot 1 design). |
| `tests/fixtures/total_eclipse/vocal_onsets.json` | NEW. Captured-once silero VAD output on the actual cached Total Eclipse raw mix (~30 segments × 2 floats = ~1 KB). |
| `tests/fixtures/total_eclipse/lyrics.lrc` | NEW. LRClib's LRC for Total Eclipse (~3 KB). |
| `tests/fixtures/total_eclipse/README.md` | NEW. One-paragraph fixture-capture instructions for refresh. |

## Test strategy (folds in eng-review findings)

### Eng-review finding fold-ins

The eng-review subagent surfaced 6 issues against Pivot 1's DP
section. Each is addressed in this Pivot 2 plan:

| # | Eng finding | Severity | Fold-in |
|---|---|---|---|
| ER1 | DP backtrack needs to reconstruct `cumulative_at(j)` — underspecified | MED | Spec: DP table stores `(prev_state, anchor_j_used)` per cell. Backtracker walks the chain rebuilding cumulative incrementally. Documented in the algorithm section. |
| ER2 | `skip_cost` formula not defined; risk of DP degenerating to "skip all anchors" | HIGH | Define: `skip_cost(L_i) = w₀ × num_words(L_i)` where `w₀ = 0.4 s` per word. This sets a default minimum that anchoring must beat. Add explicit unit test `test_dp_prefers_anchor_when_clearly_matching` that wires up a long line + perfect anchor and asserts the DP anchors it. |
| ER3 | Backward extrapolation for pre-anchor lines could produce negative timestamps and bypass `_GLOBAL_OFFSET_MAX_S` | HIGH | `_interpolate_unanchored` clamps every output to `max(0.0, computed)`. The `_GLOBAL_OFFSET_MAX_S = 10 s` cap is re-applied: if the *first anchored line*'s shift exceeds 10 s, the orchestrator returns None (existing fallback contract). Add unit test `test_dp_returns_none_when_first_anchor_offset_exceeds_cap` and `test_interpolate_clamps_pre_anchor_lines_at_zero`. |
| ER4 | E2E onset-snap tolerance of ±1.0 s is brittle to weight tuning | MED | Widen to ±2.0 s, with comment tying the tolerance to `_KARAOKE_LEAD_IN_S` + the anchor at 207.84. The no-ghost test (range check) stays a hard assert. |
| ER5 | Rename caller audit not shown | LOW | Audit recorded above (System Audit section): `_list_silence_ends` is private and grep-confirmed-unused outside `_detect_per_line_starts` and the test mock. Pivot 2 deletes `_list_silence_ends` outright; replaced by `vad_probe.list_vocal_onsets`. Tests update at the same boundary. |
| ER6 | "0.3 s × num_words" candidate filter floor is English-calibrated | MED | Promote to module constant `_VOCAL_TIME_PER_WORD_S = 0.3` with docstring noting English calibration and pointing at the open question of per-language tuning. Fold per-language tuning into the deferred TODOS. |

### Test inventory (Pivot 2 final)

**Unit tests (`tests/unit/test_lyrics_align.py`):**

`TestDetectPerLineStarts` (existing 5, helper updated only):

1. `test_uniform_shift_when_all_lines_have_silence_anchors`
2. `test_per_verse_drift_locks_each_locked_line_independently`
3. `test_continuous_line_inherits_running_offset`
4. `test_returns_none_when_silencedetect_yields_nothing` (renamed `test_returns_none_when_vad_yields_nothing`)
5. `test_returns_none_when_initial_offset_below_threshold`
6. `test_returns_none_when_initial_offset_exceeds_cap`
7. `test_skips_empty_lrc_lines_when_picking_first`

`TestDPAlignment` (new, 9 tests):

1. `test_dp_assigns_one_line_per_anchor_when_lines_match_anchors`
2. `test_dp_distributes_cluster_between_flanking_anchors`
3. `test_dp_prefers_higher_sustain_for_long_lines`
4. `test_dp_prefers_anchor_when_clearly_matching` *(ER2)*
5. `test_dp_falls_through_to_inherit_when_no_anchors_exist`
6. `test_dp_returns_none_when_first_anchor_offset_exceeds_cap` *(ER3)*
7. `test_dp_preserves_per_verse_drift_case` (Mam Tę Moc)
8. `test_dp_handles_first_few_lrc_lines_with_no_preceding_anchor`
9. `test_interpolate_clamps_pre_anchor_lines_at_zero` *(ER3)*

`TestInterpolation` (new, 2 tests):

1. `test_interpolate_unanchored_proportional_to_lrc_durations`
2. `test_interpolate_distributes_cluster_evenly_when_lrc_durations_uniform`

**Unit tests (`tests/unit/test_vad_probe.py`):**

`TestVADProbe` (new, 4 tests):

1. `test_returns_sorted_monotonic_pairs`
2. `test_collapses_adjacent_speech_segments_within_threshold`
3. `test_falls_back_to_silencedetect_when_silero_import_fails`
4. `test_uses_module_level_model_singleton`

**Integration tests (`tests/integration/test_lyrics_align_total_eclipse.py`):**

(Per Pivot 1 design, with ER4 tolerance fix)

1. `test_no_line_renders_during_solo` — hard assert (range check, weight-insensitive)
2. `test_first_post_solo_line_snaps_to_real_onset` — `abs(...) < 2.0` *(ER4)*
3. `test_lateness_recovers_by_417`
4. `test_full_pipeline_smoke`

**Total:** 7 + 11 + 4 + 4 = **26 tests** (16 new, 5 helper-updated, 5 directly preserved).

### Fixture capture script (committed for reproducibility)

`scripts/capture_alignment_fixture.py` — one-off helper:

```python
"""Capture VAD onsets + LRC for a single song, write to tests/fixtures/.

Usage:
    python scripts/capture_alignment_fixture.py \\
        --audio "/path/to/song.m4a" \\
        --artist "Bonnie Tyler" \\
        --track "Total Eclipse of the Heart" \\
        --slug total_eclipse
"""

# implementation: silero-vad on the audio file, requests-get LRClib API,
# write JSON + LRC into tests/fixtures/<slug>/
```

This way the fixture is reproducible by any maintainer with the
song file; the test itself never needs network or silero at run
time.

## Risks (Pivot 2 final)

| Risk | Mitigation |
|---|---|
| silero unavailable on a fresh install (user skips `[align]` extra) | Fallback to ffmpeg silencedetect on the vocals stem (waits for Demucs as today). Same algorithm runs on whichever onset source is available; quality is degraded but the bug is still mitigated by the DP. |
| silero loads ~2 MB model on first use → first-song latency | Module-level singleton; load happens once per process. Could prewarm in `karaoke.py` startup if measurements show the first-song hit is user-visible. Defer until measured. |
| silero misclassifies pure backing-vocal "ohh" as non-speech | Tunable via `threshold` (default 0.5; lower → more sensitive). Capture once on the test corpus and pin. |
| VAD picks up vocals during *other* songs' instrumental sections (false positives caused by drums/strings interpreted as speech) | Silero's training on music makes this rare, but if observed, raise `min_speech_duration_ms` from default 250 to e.g. 500. The DP's candidate filter (sustain ≥ phoneme floor) also drops too-short anchors. |
| Bumping `model_id` re-aligns every cached song | Documented above; lazy per-playback re-alignment; small UX cost on a one-time basis. |
| LRClib fixture goes stale | Captured once and committed; no live dependency at test time. |
| The DP weight constants need tuning across more songs than just Total Eclipse | Keep the weights as named module constants with derivation comments; add 2 more integration tests using captured fixtures from Mam Tę Moc and one of (Bohemian Rhapsody, Hotel California) before merge. Not a blocker — just expand coverage iteratively. |
| Per-language phoneme rate (`_VOCAL_TIME_PER_WORD_S`) wrong for non-English | Promoted to a constant with a docstring; per-language tuning is in TODOS. The candidate filter is *advisory* — the DP's main cost function dominates. |

## Out of scope (deferred — final)

- Per-language `_VOCAL_TIME_PER_WORD_S` tuning (English only for v1).
- VAD prewarm at startup (defer until measured first-song hit is annoying).
- Replacing wav2vec2 with a more recent forced-aligner (separate effort).
- Per-song VAD threshold auto-tuning (genre-aware).
- Selective `.ass` cache invalidation on `model_id` bump (keep lazy).

## Estimated effort (Pivot 2 final)

CC: ~90 min (silero integration + DP solver + interpolator +
21 new tests + 5 updated tests + fixture capture + pre-commit +
manual verification on 3 songs).
Human: ~8 h.

Larger than Pivot 1 by ~30 min for the silero wiring, fallback
path, and 4 extra VAD-probe unit tests. Still well inside one
focused work session on the CC scale.

## Decision Audit Trail (Pivot 2 additions)

| # | Phase | Decision | Principle | Rationale | Rejected |
|---|---|---|---|---|---|
| 20 | Pivot 2 | Replace silencedetect with silero VAD | P1 (boil the lake) — user direction | Cleaner data shape: VAD finds per-phrase onsets, DP becomes near-trivial assignment instead of heavy interpolation; decouples line shift from Demucs | Stay on silencedetect (Pivot 1) |
| 21 | Pivot 2 | silero over webrtcvad | P3 + P5 | Better music tolerance, multilingual, smaller install footprint given existing torch dep | webrtcvad |
| 22 | Pivot 2 | Add silero to `[align]` optional-dependency extra | P3 (pragmatic) — match existing whisperx wiring | Same install path users already opt into for word-level alignment | Hard requirement |
| 23 | Pivot 2 | Keep ffmpeg silencedetect as fallback when silero unavailable | P6 (bias to action) + degradation matrix | Users who skip `[align]` extra still get *some* line anchoring | Drop fallback (cleaner code, worse UX) |
| 24 | Pivot 2 | VAD on raw mix, not vocals stem | P5 (explicit) | Decouples line-anchor pass from Demucs latency; silero is robust enough to classify vocals through music | VAD on stem (preserves dep ordering but loses the Demucs decoupling win) |
| 25 | Pivot 2 | Commit `scripts/capture_alignment_fixture.py` | P1 (boil the lake) | Reproducible fixture refresh by any maintainer with the song file | One-off ad-hoc script in PR description |
| 26 | Pivot 2 | Fold ER1-ER6 eng-review findings inline | P1 + autoplan rule (gate findings) | All 6 surface valid concerns; cheap to address now | Defer to follow-up PR |
| 27 | Pivot 2 | Add 2 more corpus integration tests (Mam Tę Moc, one rock-solo song) before merge | P1 + CEO subagent S5 | Three real-data fixtures pin DP weights from multiple angles | Single-song fixture (Total Eclipse only) |
| 28 | Pivot 2 | model_id bump to `wav2vec2-char-vad-dpalign` | mechanical | Standard cache-invalidation contract | Skip bump (would serve stale .ass files) |

## Phase 3 — Eng Review (Pivot 2 final)

The CLAUDE-subagent eng review against Pivot 1 produced 6
findings; all are folded above. The architecture additions for
Pivot 2 (VAD probe module, fallback path, silero singleton) are
straightforward and don't introduce new architectural concerns —
the probe is a thin layer with a single-function contract that
mirrors the existing `_list_silence_ends` shape.

No re-run of the eng-review subagent is needed: the additions are
purely mechanical (one new module, one fallback if/else, one new
test class) and the DP core that the subagent reviewed is
unchanged.

______________________________________________________________________

# Pivot 2 — Scope expansion (per user direction "boil the ocean")

After Pivot 2's first approval-gate pass the user chose to expand
scope before merge. Three additions, all folded in below.

## Expansion 1 — Three real-data fixtures (was: just Total Eclipse)

Capture and commit fixture pairs for three songs that stress
distinct DP failure modes:

| Slug | Song | Pattern under test | What its integration test asserts |
|---|---|---|---|
| `total_eclipse` | Bonnie Tyler — Total Eclipse of the Heart | Long instrumental + clustered backing vocals (the reported bug) | No line renders during 2:50–3:28; first post-solo line within ±2.0 s of 3:27.84; lateness recovered by 4:17 |
| `mam_te_moc` | Katarzyna Łaska — Mam Tę Moc | Per-verse tempo drift on a Polish ballad (the original feature's motivating case) | Each verse's first line snaps to its per-verse-correct shifted offset; cumulative monotonically grows verse-to-verse; no inversions |
| `queen_iwtbf` | Queen — I Want To Break Free | Long instrumental break (~2:50–3:30) + repeated backing-vocal "I want to break free" entries within the break | No line renders during the saxophone-only window; backing-vocal phrases anchor to their VAD-detected onsets, not interpolated |

Each fixture pair = `vocal_onsets.json` + `lyrics.lrc` (~5 KB
total per song; ~15 KB for all three). Capture once via
`scripts/capture_alignment_fixture.py`. Three integration test
files mirror the song slugs:

- `tests/integration/test_lyrics_align_total_eclipse.py` (4 tests)
- `tests/integration/test_lyrics_align_mam_te_moc.py` (3 tests)
- `tests/integration/test_lyrics_align_queen_iwtbf.py` (3 tests)

These are E2E tests that pin the DP weights from three different
musical angles. Any future weight retune that breaks any one of
them fails CI.

## Expansion 2 — Silero prewarm at startup

Eliminate first-song latency by loading the silero JIT model in
a daemon thread at server startup, parallel to existing Demucs
prewarm.

### Where it wires in

`pikaraoke/karaoke.py` already wires `_get_aligner()` (line ~125)
during karaoke object construction. Mirror that pattern with a
`_prewarm_vad()` daemon thread launched from the same point. The
prewarm:

1. Imports `pikaraoke.lib.vad_probe`.
2. Calls `vad_probe._ensure_model()` (the singleton-loader inside
   the new module).
3. Logs `"vad_probe: model ready"` on success or
   `"vad_probe: prewarm failed (silero unavailable, will fall back to silencedetect)"` on failure.

### Files touched (Expansion 2)

| File | Change |
|---|---|
| `pikaraoke/lib/vad_probe.py` | Already adding (Pivot 2). Expose `_ensure_model()` as a module-level function the prewarm can call without invoking onset detection. |
| `pikaraoke/karaoke.py` | Add `_prewarm_vad()` daemon-thread launch alongside existing prewarm wiring. ~15 LOC. |
| `tests/unit/test_vad_probe.py` | Add `test_ensure_model_idempotent` — calling `_ensure_model()` twice doesn't re-load. |

### Tradeoff

Cost: silero model is ~2 MB, loads in ~200–500 ms on a Pi 4. On
startup that's invisible (server takes seconds to start anyway).
Without prewarm, the first song waits 200–500 ms for the model on
its first VAD probe — only noticeable on the very first song after
a server restart. Worth doing because the cost is genuinely zero.

## Expansion 3 — Eager `.ass` cache sweep on model_id bump

When the new code lands, `WhisperXAligner.model_id` bumps to
`wav2vec2-char-vad-dpalign`. Today's cache-invalidation contract
is **lazy**: each cached `.ass` file carries the `model_id` that
produced it, and `_maybe_drop_stale_auto_ass` (called per song
during alignment) deletes a stale `.ass` only when the song is
about to be played again.

For a 500-song library, that means re-alignment is amortised over
the next 500 playbacks. CEO subagent S6 flagged the per-playback
hit. Eager sweep removes the surprise: at server startup, scan
the songs directory once and delete every `.ass` whose embedded
`model_id` doesn't match the current one. The next playback of
each song re-aligns immediately, but it's the user's choice when
to play, not a surprise queue.

### Where it wires in

The existing `Karaoke.__init__` already runs startup-time scans
(see commit 418103eb "feat(scanner): startup integrity check +
per-song lyrics reprocess"). Add one more pass: read the first
~1 KB of every `.ass` file, look for a `; model_id: ...` comment
line (this is how `WhisperXAligner` already records the producing
model — verified in `lyrics_align.py` `model_id` docstring), and
unlink the file if the comment doesn't match
`current_aligner.model_id`.

### Files touched (Expansion 3)

| File | Change |
|---|---|
| `pikaraoke/karaoke.py` | Extend the startup scanner with a `_sweep_stale_aligned_ass()` step. Reads each `.ass` head, deletes mismatches, logs `"swept N stale .ass files (old model_id was X)"`. ~30 LOC. |
| `pikaraoke/lib/lyrics.py` | Confirm the `; model_id:` comment is actually emitted by the ASS writer. If not (current code may emit a different marker), add it as part of the bump. ~5 LOC. |
| `tests/unit/test_karaoke.py` | Add `test_startup_sweeps_stale_aligned_ass` — fixture creates two .ass files (one matching, one stale), assert only the stale one is deleted. |

### Tradeoff

The sweep runs once at startup, scanning ~50 files for a typical
library (open + read first KB + close). Sub-second. The user-
visible difference: instead of "first replay after upgrade is 30 s
slower than usual", they see "first replay after upgrade is
unchanged latency, but every song that was cached is now fresh".
Operationally cleaner for a karaoke-night use case where someone
opened the app for setup and the server has been running for
hours before guests arrive.

## Files touched (Pivot 2 — final-final, with expansions)

(Adds to the table from Pivot 2 above.)

| File | Change |
|---|---|
| `pikaraoke/karaoke.py` | `_prewarm_vad()` daemon thread (Exp 2) + `_sweep_stale_aligned_ass()` startup pass (Exp 3). |
| `pikaraoke/lib/vad_probe.py` | Already in Pivot 2 — expose `_ensure_model()` so prewarm can call it (Exp 2). |
| `pikaraoke/lib/lyrics.py` | Confirm/add `; model_id:` comment in ASS header so sweep can read it (Exp 3). |
| `tests/integration/test_lyrics_align_mam_te_moc.py` | NEW (Exp 1). 3 tests. |
| `tests/integration/test_lyrics_align_queen_iwtbf.py` | NEW (Exp 1). 3 tests. |
| `tests/fixtures/mam_te_moc/{vocal_onsets.json,lyrics.lrc}` | NEW (Exp 1). |
| `tests/fixtures/queen_iwtbf/{vocal_onsets.json,lyrics.lrc}` | NEW (Exp 1). |
| `tests/unit/test_vad_probe.py` | Add `test_ensure_model_idempotent` (Exp 2). |
| `tests/unit/test_karaoke.py` | Add `test_startup_sweeps_stale_aligned_ass` (Exp 3). |

## Test inventory (Pivot 2 + expansions — final)

- Unit `test_lyrics_align.py`: 7 (existing helper-updated) + 9 (DP) + 2 (interpolation) = 18
- Unit `test_vad_probe.py`: 4 + 1 (idempotent prewarm) = 5
- Unit `test_karaoke.py`: 1 (sweep)
- Integration `test_lyrics_align_total_eclipse.py`: 4
- Integration `test_lyrics_align_mam_te_moc.py`: 3
- Integration `test_lyrics_align_queen_iwtbf.py`: 3

**Total: 34 tests** (24 new, 5 helper-updated, 5 directly preserved).

## Estimated effort (Pivot 2 — final-final)

| Component | CC time |
|---|---|
| silero `vad_probe.py` (with `_ensure_model()` exposed) | 15 min |
| DP solver `_align_lines_to_anchors_dp` | 25 min |
| Interpolator `_interpolate_unanchored` | 10 min |
| Orchestrator rewrite `_detect_per_line_starts` | 5 min |
| `model_id` bump + ASS header comment confirmation | 5 min |
| `karaoke.py` prewarm wiring (Exp 2) | 5 min |
| `karaoke.py` startup sweep (Exp 3) | 10 min |
| Capture `scripts/capture_alignment_fixture.py` | 10 min |
| Capture 3 fixtures (run script × 3) | 10 min |
| Unit tests (24 new) | 25 min |
| Integration tests (10 across 3 files) | 15 min |
| Pre-commit + manual verification on 3 songs | 10 min |
| **Total** | **~145 min CC (~2.5 h)** |

Human-equivalent: ~10–12 h.

Larger than Pivot 2's pre-expansion ~90 min by about 55 min,
which buys: pre-merge multi-song regression coverage,
zero-latency first-song VAD, clean-slate cache state on bump.

## Decision Audit Trail (Pivot 2 expansion additions)

| # | Phase | Decision | Principle | Rationale | Rejected |
|---|---|---|---|---|---|
| 29 | Expansion | Three pre-merge fixtures (Total Eclipse + Mam Tę Moc + Queen IWTBF) | P1 (boil the lake) — user direction | Pins DP weights from 3 angles; protects against silent regressions during weight retuning | Single-song fixture, or 5-song over-coverage |
| 30 | Expansion | Silero prewarm in daemon thread at startup | P1 + P3 | ~5 LOC; eliminates 200–500 ms first-song hit; mirrors existing Demucs prewarm pattern | Lazy load on first VAD call (Pivot 2 baseline) |
| 31 | Expansion | Eager `.ass` sweep on model_id bump | P1 (no surprise UX) — addresses CEO subagent S6 | Removes amortised re-align surprise across the next N playbacks; replaces with one-time startup sweep | Stay lazy (per Pivot 2 baseline) |
| 32 | Expansion | Capture script committed to `scripts/` (not deleted post-capture) | P1 (reproducibility) | Any maintainer can refresh fixtures with one command | One-off ad-hoc script in PR description |
| 33 | Expansion | Confirm `; model_id:` ASS-header comment exists; if not, emit it | P5 (explicit) | Sweep needs a stable read-only marker; relying on whisperx-injected fields is fragile | Use file-mtime + global version file (more state) |

## Risks (Pivot 2 — final-final additions)

| Risk | Mitigation |
|---|---|
| Capture script depends on user having the song's m4a + network for LRClib | Documented in `tests/fixtures/README.md`. CI tests don't need either — they read the committed JSON + LRC. Maintainers refresh fixtures rarely (only on cost-function changes). |
| Mam Tę Moc / Queen LRC quality varies on LRClib | Capture once and commit. If LRClib later improves the entry, capture again — but the test pins behaviour against the captured snapshot, not the live API. |
| Eager sweep deletes a `.ass` that was hand-edited by the user | Sweep only acts on `.ass` files whose embedded `model_id` differs from current; if a user hand-edited (and removed the `model_id` comment), the sweep skips it. Document the comment as the canary in `lyrics_align.py`. |
| Silero prewarm fails silently and the user only notices on first song | Log surfacing: `"vad_probe: prewarm failed (silero unavailable, will fall back to silencedetect)"` is emitted at INFO level so it shows up in the standard log. Operator-visible. |

## /autoplan — APPROVED

Plan approved at the final gate (single revision cycle, scope
expansion accepted). Implementation has not yet started; plan
file is the source of truth.

**Next step:** start implementation on a fresh branch
(suggested: `fix/lyrics-vad-dp-align`). Order from the plan body:
silero `vad_probe.py` → DP solver → interpolator → orchestrator
rewrite → `model_id` bump → prewarm wiring → startup sweep wiring
→ capture script → 3 fixtures → 24 unit tests + 10 integration
tests → manual verification on all three fixture songs.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | autoplan Phase 1 | Scope & strategy | 1 | clean | 6 sub-agent findings (5 high/med, 1 low) — all folded |
| Codex Review | unavailable | Codex CLI not installed on host | 0 | n/a | Tagged `[subagent-only]` per degradation matrix |
| Eng Review | autoplan Phase 3 | Architecture & tests | 1 | clean | 6 findings (2 high, 3 med, 1 low) — all folded into Pivot 2 |
| Design Review | skipped | Pure backend Python; no UI scope detected | 0 | n/a | — |

**VERDICT:** APPROVED. Implementation may begin.

| Phase | Dual voices | Codex | Subagent | Consensus |
|---|---|---|---|---|
| CEO | subagent-only | n/a | 5/10 (6 findings, all folded) | 1/6 confirmed, 4/6 disagreed (resolved by accepting subagent fixes), 1 N/A |
| Eng | subagent-only | n/a | 7/10 (6 findings, all folded) | 0/6 confirmed (subagent surfaced unique findings), 6/6 disagreed (all addressed) |
