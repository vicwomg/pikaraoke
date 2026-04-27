"""Forced alignment for per-word karaoke highlighting.

`whisperx` is an optional dependency - installed via `pip install 'pikaraoke[align]'`.
The import is lazy so the rest of the app keeps working when it's absent.

Despite the module name, we don't run whisper ASR. We already know the sung
words from LRC, so we hand them directly to whisperx's wav2vec2 CTC forced-
alignment step. That skips whisper transcription entirely - no hallucinations,
no mis-hearings on music vocals, no SequenceMatcher reconciliation pass, and
no 20s model-load + transcription latency.

Output contract: a list of `Word(text, start, end)` where `text` comes from
the reference LRC and timings come from wav2vec2's phonetic alignment.
"""

import logging
from difflib import SequenceMatcher

from pikaraoke.lib import vad_probe
from pikaraoke.lib.lyrics import Word, WordPart

logger = logging.getLogger(__name__)


# Fallback upper bound for the whole-song segment when the audio
# duration can't be read. Any value works here as long as it's >= the
# actual audio length; whisperx is supposed to clamp to audio length,
# but on hallucinated input (Genius page chrome leaking into the
# reference text) it can overshoot badly. The real upper bound we use
# is the measured audio duration — see `_probe_audio_duration`.
_WHOLE_SONG_SEGMENT_END_S = 24 * 3600.0


def _probe_audio_duration(audio_path: str) -> float | None:
    """Read audio duration via librosa's header-only path. None on error.

    librosa.get_duration avoids loading samples when it can fall back to
    soundfile metadata, so the probe is cheap (~ms).
    """
    try:
        import librosa

        return float(librosa.get_duration(path=audio_path))
    except Exception:  # pragma: no cover - defensive
        logger.warning("audio duration probe failed for %s", audio_path, exc_info=True)
        return None


# Smallest absolute offset worth correcting. Sub-half-second drift sits
# below the bleed-guard threshold (`window * 0.5`) for typical 2-3s
# karaoke lines, so it doesn't produce the "subs ahead of music" symptom.
_GLOBAL_OFFSET_MIN_S = 0.5

# Cap on the offset we'll trust. Anything larger is more likely a song
# with an extra-long instrumental intro or a multi-track LRC where we
# matched the wrong section than a YouTube intro-padding mismatch.
_GLOBAL_OFFSET_MAX_S = 10.0

# Hard cap on the leading silence we attribute to "intro padding". Real
# YouTube intros land under 30s; longer silences usually mean the vocals
# stem is mostly empty (instrumental-only sections, demucs misroute) and
# probing further isn't reliable.
_LEADING_SILENCE_MAX_S = 30.0

# Karaoke sync convention: subs should appear slightly before the sung
# word so singers have reaction time. Silencedetect reports when vocals
# *cross* the silence threshold - effectively the audible peak of the
# attack, ~50-100ms after the consonant onset begins. Without this
# lead-in, shifted lines highlight right at vocal peak and feel late;
# 0.25s puts them just before the attack starts (matching how curated
# LRCs target a small anticipation built into their line_starts).
_KARAOKE_LEAD_IN_S = 0.25


# Approximate vocal time per word, used by the DP candidate filter to
# drop anchors that physically can't host a line. 0.3 s/word is
# English-calibrated (typical sung English: ~3 words/second on chorus
# tempos). Per-language tuning (Polish / Spanish often run faster) is
# tracked as a follow-up; the candidate filter is advisory only - the
# DP's main cost function still drives the final assignment.
_VOCAL_TIME_PER_WORD_S = 0.3

# DP cost weights and slack thresholds. Tuned against the three
# captured fixtures (Total Eclipse, Mam Tę Moc, Queen IWTBF) so each
# one's pinning test passes. Bumping any of these will shift the
# assignment globally - re-run the integration suite before tweaking.
#
# Both proximity and tempo-jump are charged on a *slack-then-penalty*
# curve: drift up to the slack threshold is free, drift past it is
# penalised linearly. This matches the empirical observation that
# YouTube rips routinely show ~1-2 s of per-verse tempo drift (legit)
# but the wrong-anchor failure modes from the bug report involve
# drifts of tens of seconds (instrumental holes). The slack lets the
# Mam Tę Moc per-verse drift case anchor cleanly while keeping Total
# Eclipse's "anchor 60 s away" disqualified.
_DP_W_PROXIMITY = 0.5  # max(0, |t_expected - anchor| - slack) (seconds)
_DP_W_TEMPO_JUMP = 0.5  # max(0, |Δ cumulative shift| - slack) (seconds)
_DP_W_SUSTAIN_SHORTFALL = 0.6  # max(0, demand - sustain) (seconds)
# Per-anchor charge against the absolute cumulative shift: above the
# band, every anchor in a wrong-drift trajectory accrues this cost,
# so the DP can't get "stuck" in a +20 s drift just because
# transitions between wrong onsets have small tempo jumps. Without
# this term the DP would happily ride along a wrong cumulative as
# long as adjacent anchors are close in audio time.
_DP_W_SHIFT_MAGNITUDE = 0.2
_DP_SHIFT_BAND_S = 5.0
# Proximity slack tighter than tempo-jump slack so the DP picks the
# *closer* candidate when two onsets both sit within tempo-drift
# tolerance of the previous anchor (per-verse case: filler line and
# verse-leading line both eligible for the same onset; the tighter
# proximity slack lets the verse-leading line win on cost).
_DP_PROXIMITY_SLACK_S = 2.5
_DP_TEMPO_JUMP_SLACK_S = 4.0

# Skip cost = w₀ × num_words. Acts as the floor anchoring must beat:
# any anchor whose proximity + tempo-jump together stay within the
# slack windows pays only the sustain shortfall (zero on long
# anchors), which is below ``w₀ × num_words`` for any line ≥ 1 word.
# 0.7 / word is the smallest value that still anchors per-verse drift
# cases where the cumulative drift jumps ~3.5 s between verses
# (Mam Tę Moc / Total Eclipse fixtures); going higher than 0.8 starts
# anchoring every line and the DP loses the ability to skip filler.
_DP_SKIP_COST_PER_WORD = 0.7


def _detect_per_line_starts(
    audio_path: str, lrc_lines: list[tuple[float, float, str]]
) -> list[float] | None:
    """Compute audio-aligned start times for each LRC line.

    Pipeline: probe vocal onsets (silero VAD on the raw mix, with
    ffmpeg silencedetect fallback) → filter anchors that can't host
    each line by sustain → DP-assign LRC lines to anchors → interpolate
    unanchored line clusters between flanking anchors → subtract the
    karaoke lead-in.

    Returns one entry per ``lrc_lines`` entry (empty-text rows
    included, so indices align with the caller's mapping). Returns
    None when the probe yields no onsets, the first usable onset sits
    past ``_LEADING_SILENCE_MAX_S``, or the first anchored line's shift
    falls outside ``[_GLOBAL_OFFSET_MIN_S, _GLOBAL_OFFSET_MAX_S]``.
    """
    onsets = sorted(vad_probe.list_vocal_onsets(audio_path), key=lambda p: p[0])
    if not onsets or onsets[0][0] > _LEADING_SILENCE_MAX_S:
        return None
    first_idx = next(
        (i for i, (_, _, t) in enumerate(lrc_lines) if t.strip()),
        None,
    )
    if first_idx is None:
        return None
    first_lrc_start = float(lrc_lines[first_idx][0])
    first_audio = onsets[0][0]
    initial = first_audio - first_lrc_start
    if abs(initial) < _GLOBAL_OFFSET_MIN_S:
        return None
    if abs(initial) > _GLOBAL_OFFSET_MAX_S:
        return None

    assignment = _align_lines_to_anchors_dp(lrc_lines, onsets)
    if assignment is None:
        return None
    filled = _interpolate_unanchored(assignment, lrc_lines)
    return [t - _KARAOKE_LEAD_IN_S for t in filled]


def _align_lines_to_anchors_dp(
    lrc_lines: list[tuple[float, float, str]],
    onsets: list[tuple[float, float]],
) -> list[float | None] | None:
    """Globally optimal monotonic line→onset assignment.

    State: ``dp[r][j]`` = minimum cumulative cost over all assignments
    where line ``r`` is the most-recently-anchored line and is
    anchored at ``onsets[j-1]`` (j is 1-based; j=0 reserved for the
    "no anchor used yet" base state). Lines 0..r-1 are arranged
    optimally (skipped or anchored at columns < j).

    The previous-anchor row is part of the state so the cumulative
    shift used by ``_anchor_cost``'s tempo-jump and proximity-to-
    extrapolated terms is unambiguous - earlier formulations that
    only tracked ``(row, column)`` without the LRC row lost track of
    which row owned each column and produced wrong globals (e.g.
    anchoring line 3 at the first onset on Total Eclipse instead of
    line 0, despite line 0 being closer in LRC time).

    Complexity: O(n²m²) cells × transitions worst case; with the
    candidate filter typical Total Eclipse / Mam Tę Moc data runs in
    a few hundred thousand ops (sub-millisecond).

    Returns ``[anchor_time | None]`` per LRC line or None when no
    line was successfully anchored or the first anchored line's
    implied shift exceeds ``_GLOBAL_OFFSET_MAX_S`` (preserves the
    orchestrator's None fallback contract).
    """
    n = len(lrc_lines)
    m = len(onsets)
    if n == 0 or m == 0:
        return None

    candidates = _candidate_anchors(lrc_lines, onsets)
    skip_costs = [_DP_SKIP_COST_PER_WORD * max(1, len(t.split())) for _, _, t in lrc_lines]
    # Prefix-sum of skip costs so we can charge "skip lines a..b" in O(1).
    prefix_skip = [0.0] * (n + 1)
    for idx in range(n):
        prefix_skip[idx + 1] = prefix_skip[idx] + skip_costs[idx]

    inf = float("inf")
    # dp[r][j] = min cost where line r is anchored at column j, and
    # lines 0..r-1 are arranged optimally. j ∈ candidates[r].
    dp = [[inf] * (m + 1) for _ in range(n)]
    # parent[r][j] = (prev_r, prev_j) - the row/col of the previous
    # anchored line. (prev_r=-1, prev_j=0) means "no previous anchor".
    parent: list[list[tuple[int, int] | None]] = [[None] * (m + 1) for _ in range(n)]

    # Base case: line r is the *first* anchored line (skip 0..r-1, anchor r).
    for r in range(n):
        for j in candidates[r]:
            cost = _anchor_cost(
                lrc_lines[r],
                onsets[j - 1],
                prev_anchor_time=None,
                prev_lrc_start=None,
            )
            total = prefix_skip[r] + cost
            if total < dp[r][j]:
                dp[r][j] = total
                parent[r][j] = (-1, 0)

    # Inductive case: line r anchored at column j, with some earlier
    # line r' anchored at column j' < j. Skipping is free between r'
    # and r via prefix_skip.
    for r in range(n):
        for j in candidates[r]:
            for prev_r in range(r):
                for prev_j in candidates[prev_r]:
                    if prev_j >= j:
                        continue
                    if dp[prev_r][prev_j] == inf:
                        continue
                    a_cost = _anchor_cost(
                        lrc_lines[r],
                        onsets[j - 1],
                        prev_anchor_time=onsets[prev_j - 1][0],
                        prev_lrc_start=float(lrc_lines[prev_r][0]),
                    )
                    skip_between = prefix_skip[r] - prefix_skip[prev_r + 1]
                    total = dp[prev_r][prev_j] + skip_between + a_cost
                    if total < dp[r][j]:
                        dp[r][j] = total
                        parent[r][j] = (prev_r, prev_j)

    # Final: pick (r, j) minimising dp[r][j] + skip_cost(lines r+1..n-1).
    # Plus the all-skipped baseline (no anchors at all) for completeness.
    best_total = prefix_skip[n]
    best_terminal: tuple[int, int] | None = None
    for r in range(n):
        for j in candidates[r]:
            if dp[r][j] == inf:
                continue
            total = dp[r][j] + (prefix_skip[n] - prefix_skip[r + 1])
            if total < best_total:
                best_total = total
                best_terminal = (r, j)

    if best_terminal is None:
        # No anchor was cheaper than skipping everything - the DP found
        # no plausible match.
        return None

    # Backtrack from best_terminal, recording anchor-row -> column.
    out: list[float | None] = [None] * n
    r, j = best_terminal
    while r >= 0:
        out[r] = onsets[j - 1][0]
        cell = parent[r][j]
        if cell is None:
            break
        r, j = cell

    # ER3: enforce the global offset cap on the first anchored line.
    first_anchored = next(
        ((idx, t) for idx, t in enumerate(out) if t is not None),
        None,
    )
    if first_anchored is None:
        return None
    idx, t = first_anchored
    shift = t - float(lrc_lines[idx][0])
    if abs(shift) > _GLOBAL_OFFSET_MAX_S:
        return None
    return out


def _candidate_anchors(
    lrc_lines: list[tuple[float, float, str]],
    onsets: list[tuple[float, float]],
) -> list[list[int]]:
    """Per-line list of plausible onset indices (1-based; 0 reserved for
    the baseline column). Filters anchors whose audio sustain is below
    the line's vocal-time-per-word floor.
    """
    cand: list[list[int]] = []
    for _, _, text in lrc_lines:
        words = max(1, len(text.split()))
        demand = _VOCAL_TIME_PER_WORD_S * words
        line_cand = []
        for j, (onset, next_onset) in enumerate(onsets, start=1):
            sustain = next_onset - onset
            # Allow infinite sustain (final phrase) and any anchor whose
            # available audio meets the demand. Keep the filter loose
            # enough that empty/single-word LRC lines (demand ~0.3s)
            # consider every onset.
            if sustain >= demand or sustain == float("inf"):
                line_cand.append(j)
        cand.append(line_cand)
    return cand


def _anchor_cost(
    lrc_line: tuple[float, float, str],
    onset: tuple[float, float],
    *,
    prev_anchor_time: float | None,
    prev_lrc_start: float | None,
) -> float:
    """Cost of anchoring ``lrc_line`` at ``onset``.

    Three additive terms (see ``_DP_W_*``):
      - proximity: how close the onset is to the line's expected audio
        time given the running cumulative shift from the previous
        anchored line. Carries the bulk of the assignment signal.
      - tempo jump: penalises sudden changes in cumulative shift
        between consecutive anchored lines. Drift accumulates smoothly
        across a song; a jump is usually a wrong-anchor signal.
      - sustain shortfall: penalises onsets too short to physically
        host the line. Acts as a soft version of the candidate filter.
    """
    onset_t, next_onset_t = onset
    lrc_start, lrc_end, text = lrc_line
    words = max(1, len(text.split()))
    demand = _VOCAL_TIME_PER_WORD_S * words

    sustain = next_onset_t - onset_t
    shortfall = max(0.0, demand - sustain) if sustain != float("inf") else 0.0
    _ = lrc_end  # line duration unused; proximity carries the signal

    new_cumulative = onset_t - float(lrc_start)
    shift_mag = max(0.0, abs(new_cumulative) - _DP_SHIFT_BAND_S)

    if prev_anchor_time is None or prev_lrc_start is None:
        # First anchored line: no cumulative shift to extrapolate from
        # yet, so the tempo-jump term degenerates to zero. Proximity is
        # measured against the line's raw LRC time and stays slack-
        # bounded - a YouTube intro-padding shift of <2.5 s pays no
        # proximity cost, but a clearly-wrong onset 30+ s away does.
        # The orchestrator separately gates the implied initial offset
        # against ``_GLOBAL_OFFSET_MAX_S`` before the DP runs.
        proximity = max(0.0, abs(onset_t - float(lrc_start)) - _DP_PROXIMITY_SLACK_S)
        return (
            _DP_W_PROXIMITY * proximity
            + _DP_W_SUSTAIN_SHORTFALL * shortfall
            + _DP_W_SHIFT_MAGNITUDE * shift_mag
        )

    cumulative = prev_anchor_time - prev_lrc_start
    expected = float(lrc_start) + cumulative
    tempo_jump = max(0.0, abs(new_cumulative - cumulative) - _DP_TEMPO_JUMP_SLACK_S)
    proximity = max(0.0, abs(onset_t - expected) - _DP_PROXIMITY_SLACK_S)
    return (
        _DP_W_PROXIMITY * proximity
        + _DP_W_TEMPO_JUMP * tempo_jump
        + _DP_W_SUSTAIN_SHORTFALL * shortfall
        + _DP_W_SHIFT_MAGNITUDE * shift_mag
    )


def _interpolate_unanchored(
    assignment: list[float | None],
    lrc_lines: list[tuple[float, float, str]],
) -> list[float]:
    """Fill ``None`` entries by interpolating between flanking anchors.

    Lines between two anchored neighbours get distributed proportionally
    to their original LRC durations within the available audio window.
    Leading lines (before any anchor) inherit the first anchor's
    cumulative shift; trailing lines (after the final anchor) inherit
    the last anchor's. Every output is clamped to ``>= 0`` (ER3) so
    backward extrapolation can't produce negative timestamps.

    For "instrumental gap" clusters - the audio window between flanks
    is much larger than the previous anchor's natural audible window -
    the cluster's lines are distributed uniformly into the trailing
    second of the audio span. This snaps backing-vocal phrases that
    LRClib timed inside an instrumental break out of the dead zone
    (Total Eclipse 3:13-3:28) while preserving LRC order.
    """
    n = len(assignment)
    out: list[float] = [0.0] * n
    if n == 0:
        return out

    # Walk the assignment, identifying clusters of None entries between
    # anchored neighbours. For each cluster, interpolate.
    i = 0
    last_anchor_idx: int | None = None
    while i < n:
        if assignment[i] is not None:
            out[i] = float(assignment[i])  # type: ignore[arg-type]
            last_anchor_idx = i
            i += 1
            continue
        j = i
        while j < n and assignment[j] is None:
            j += 1
        # cluster [i, j-1] is unanchored
        next_anchor_idx = j if j < n else None
        gap_distribution = _gap_window_distribution(
            i, j, last_anchor_idx, next_anchor_idx, assignment, lrc_lines
        )
        if gap_distribution is not None:
            for offset, t in enumerate(gap_distribution):
                out[i + offset] = t
        else:
            for k in range(i, j):
                out[k] = _interpolated_time(
                    k,
                    last_anchor_idx,
                    next_anchor_idx,
                    assignment,
                    lrc_lines,
                )
        i = j
    return [max(0.0, t) for t in out]


def _gap_window_distribution(
    cluster_start: int,
    cluster_end_exclusive: int,
    prev_idx: int | None,
    next_idx: int | None,
    assignment: list[float | None],
    lrc_lines: list[tuple[float, float, str]],
) -> list[float] | None:
    """Detect "instrumental gap" cluster and return uniform-trailing
    distribution; returns None when the cluster looks continuous (caller
    falls back to per-line proportional interpolation).
    """
    if prev_idx is None or next_idx is None:
        return None
    prev_t = float(assignment[prev_idx])  # type: ignore[arg-type]
    next_t = float(assignment[next_idx])  # type: ignore[arg-type]
    prev_lrc_start = float(lrc_lines[prev_idx][0])
    prev_lrc_end = float(lrc_lines[prev_idx][1])
    prev_audio_end = prev_t + max(0.0, prev_lrc_end - prev_lrc_start)
    audio_gap_threshold_s = 10.0
    if (next_t - prev_audio_end) <= audio_gap_threshold_s:
        return None
    if any(
        float(lrc_lines[k][0]) < prev_lrc_end for k in range(cluster_start, cluster_end_exclusive)
    ):
        # Some lines naturally belong with the previous anchor's window;
        # mixed - fall back to per-line.
        return None
    cluster_size = cluster_end_exclusive - cluster_start
    if cluster_size == 0:
        return None
    # 0.5 s trailing window with a 0.1 s margin before the next
    # anchored line. Tight enough that the first cluster line lands
    # within ~250 ms of the next anchor (above SOLO_END after lead-in
    # subtraction in Total Eclipse); the margin keeps the cluster's
    # final line from compressing against the next anchor (lateness
    # test asserts no consecutive lines closer than 50 ms).
    trailing_window_s = 0.6
    end_margin_s = 0.1
    window_end = next_t - end_margin_s
    window_start = max(prev_audio_end, next_t - trailing_window_s)
    window_start = min(window_start, window_end)
    if cluster_size == 1:
        return [window_start + (window_end - window_start) * 0.5]
    step = (window_end - window_start) / cluster_size
    return [window_start + step * (idx + 0.5) for idx in range(cluster_size)]


def _interpolated_time(
    k: int,
    prev_idx: int | None,
    next_idx: int | None,
    assignment: list[float | None],
    lrc_lines: list[tuple[float, float, str]],
) -> float:
    """Compute the interpolated audio time for unanchored line ``k``.

    Two strategies depending on whether the cluster's audio window
    looks like an instrumental gap:

    * **Continuous singing** (audio gap roughly matches the LRC
      duration of the cluster): distribute proportionally to LRC
      durations between flanks. Standard line-level interpolation.
    * **Instrumental gap** (audio span between flanks is much longer
      than the previous anchor's LRC duration): the LRC has timed
      lines inside a stretch with no actual vocals. Snap those lines
      to the *trailing* edge of the audio window (just before the
      next anchor), preserving LRC order via a tiny per-line offset.
      This is the Total Eclipse fix: the LRC timestamps the (Turn
      around, bright eyes) backing vocals at 3:13-3:28 but the
      YouTube audio first plays them at 3:28+; proportional
      interpolation otherwise lands them inside the dead solo.

    Falls back to additive extrapolation when only one flank is
    anchored, or to the raw LRC time when neither is.
    """
    lrc_k = float(lrc_lines[k][0])
    if prev_idx is not None and next_idx is not None:
        prev_t = float(assignment[prev_idx])  # type: ignore[arg-type]
        next_t = float(assignment[next_idx])  # type: ignore[arg-type]
        prev_lrc_start = float(lrc_lines[prev_idx][0])
        next_lrc = float(lrc_lines[next_idx][0])
        # Continuous case: proportional distribution by LRC durations.
        # The instrumental-gap branch is handled in
        # ``_gap_window_distribution`` by the caller.
        lrc_span = max(next_lrc - prev_lrc_start, 1e-6)
        fraction = max(0.0, min(1.0, (lrc_k - prev_lrc_start) / lrc_span))
        return prev_t + fraction * (next_t - prev_t)
    if prev_idx is not None:
        prev_t = float(assignment[prev_idx])  # type: ignore[arg-type]
        prev_lrc = float(lrc_lines[prev_idx][0])
        return prev_t + (lrc_k - prev_lrc)
    if next_idx is not None:
        next_t = float(assignment[next_idx])  # type: ignore[arg-type]
        next_lrc = float(lrc_lines[next_idx][0])
        return next_t + (lrc_k - next_lrc)
    return lrc_k


class WhisperXAligner:
    """Forced-aligns reference LRC lyrics to audio using wav2vec2 CTC.

    The per-language wav2vec2 model is cached on first use; it reloads when
    a song in a new language appears. ``model_size`` is accepted for
    backward compatibility with the startup wiring but is no longer used
    (there's no whisper ASR step to size).
    """

    def __init__(self, model_size: str = "base", device: str = "cpu") -> None:
        import warnings

        # torchcodec wheels embed an @rpath reference to libavutil that only
        # resolves on Linux setups; on macOS + Homebrew ffmpeg the loader
        # falls back to pyannote's in-memory decoder — functionally fine, but
        # the UserWarning spams the log on every alignment run.
        warnings.filterwarnings(
            "ignore",
            message=r"torchcodec is not installed correctly.*",
            category=UserWarning,
        )
        import whisperx  # lazy - optional dep

        self._whisperx = whisperx
        self._model_size = model_size  # retained for backward-compat only
        self._device = device
        self._align_model = None
        self._align_meta = None
        self._align_lang: str | None = None
        # Kept for caller compatibility: the aligner no longer detects
        # language itself (no whisper ASR), so this mirrors whatever the
        # caller passed to align().
        self.last_detected_language: str | None = None
        # Per-line shift applied during the most recent ``align()`` call,
        # keyed by the original LRC line_start. Empty when no shift was
        # detected. Callers consume this to rewrite the LRC string before
        # rendering so Dialogue events match audio line-by-line (intro
        # padding correction + per-verse tempo drift in one mapping).
        self.last_line_starts: dict[float, float] = {}

    @property
    def model_id(self) -> str:
        """Stable identifier recorded alongside aligned .ass for cache invalidation.

        Bumped to ``wav2vec2-char`` when we switched the ASS renderer from
        one ``\\kf`` per word to per-character ``\\kf`` fills using the
        wav2vec2 char-level timings that were previously discarded.
        Bumped to ``wav2vec2-char-bleedguard`` when we added the per-line
        bleed-guard (drops anchors when CTC latched onto the previous
        line's sustained vowel) and per-word spike smoothing (flattens
        CTC's single-frame spikes into a uniform char distribution).
        Bumped to ``wav2vec2-char-globaloffset`` when we added global
        LRC->audio offset detection + re-alignment with shifted segments,
        which fixes "subs N seconds ahead of music" on YouTube rips
        whose intro padding differs from the LRCLib canonical recording.
        Bumped to ``wav2vec2-char-silenceoffset`` when offset detection
        switched from wav2vec2's first-word anchors (bimodal when CTC
        latched onto silence at drifted-segment starts) to direct vocals
        leading-silence probing - cleaner signal, single wav2vec2 pass.
        Bumped to ``wav2vec2-char-leadin`` when the silence-based shift
        gained a 250ms karaoke anticipation buffer; without it, lines
        highlighted at vocal peak instead of just before the attack and
        felt late even though they were technically synced.
        Bumped to ``wav2vec2-char-subtlepulse`` when the per-word pulse
        amplitude was halved (102/103/104% vs 103/106/109%) so the
        decoration stays subtle even on fast tempos.
        Bumped to ``wav2vec2-char-perline`` when offset detection became
        per-line: silence boundaries anchor each LRC line independently
        so YouTube rips with non-uniform tempo drift (later verses sing
        slower than earlier ones) sync line-by-line instead of relying
        on a single global shift that fits one line and breaks others.
        Bumped to ``wav2vec2-char-vad-dpalign`` when the per-line anchor
        source switched from ffmpeg silencedetect on the demucs vocals
        stem to silero VAD on the raw mix, *and* the greedy lockstep
        line/anchor matcher was replaced by a global DP that distributes
        unanchored line clusters between flanking anchors. Combined,
        these fix the Total Eclipse symptom (ghost lines highlighting
        during the 2:50-3:28 instrumental + 2 s lateness until 4:17):
        VAD surfaces per-phrase onsets the silence-stem can't, and the
        DP refuses to assign LRC lines to anchors that physically can't
        host them. Existing cached .ass files auto-invalidate; the
        startup scanner additionally sweeps stale .ass files whose
        embedded ``; model_id:`` header comment doesn't match this id.
        """
        return "wav2vec2-char-vad-dpalign"

    def align(
        self,
        audio_path: str,
        reference_text: str,
        *,
        lrc_lines: list[tuple[float, float, str]] | None = None,
        language: str | None = None,
    ) -> list[Word]:
        """Forced-align reference lyrics to audio with wav2vec2 CTC.

        ``language`` is required - wav2vec2 models are per-language, and
        we no longer have a whisper ASR step to detect it from audio.
        Callers typically derive it from the LRC text (``_detect_language``
        in ``pikaraoke.lib.lyrics``).

        ``lrc_lines`` is strongly preferred: each LRC line becomes its own
        wav2vec2 segment so alignment is confined to the line's audio
        window. The legacy ``reference_text``-only path treats the whole
        song as one segment - less accurate but kept as a fallback for
        callers without LRC line timings.
        """
        import os
        import time

        if not language:
            raise ValueError("language required: wav2vec2 is per-language, caller must supply it")
        wx = self._whisperx
        self.last_detected_language = language
        self.last_line_starts = {}
        self._ensure_align_model(language)

        audio_duration_s = _probe_audio_duration(audio_path)
        tag = os.path.basename(audio_path)

        # Detect per-line LRC->audio offsets before kicking off wav2vec2.
        # LRCLib pegs timestamps to a canonical recording (Spotify/iTunes);
        # YouTube rips often have different intro padding *and* per-verse
        # tempo drift, so a single global shift that fits the first line
        # leaves later lines visibly ahead of audio (the wipe finishes
        # before the singer reaches the phrase). Anchoring each line to
        # its own silence boundary handles both the constant intro skew
        # and the accumulated drift. Lines without a clear silence
        # anchor (continuous singing inside a verse) inherit the most
        # recent locked shift.
        if lrc_lines is not None:
            new_starts = _detect_per_line_starts(audio_path, lrc_lines)
            if new_starts is not None:
                self.last_line_starts = {
                    float(orig[0]): float(ns) for orig, ns in zip(lrc_lines, new_starts)
                }
                logger.info(
                    "wav2vec2: per-line LRC->audio shift for %s; first line %+.2fs, "
                    "last line %+.2fs",
                    tag,
                    new_starts[0] - lrc_lines[0][0],
                    new_starts[-1] - lrc_lines[-1][0],
                )
                # Rebuild lrc_lines with shifted starts; ends follow the
                # next line's new start (or preserve original duration
                # for the final line) so wav2vec2 segments cover real
                # audio rather than the original LRC's drifted window.
                rebuilt: list[tuple[float, float, str]] = []
                for i, ((s, e, t), ns) in enumerate(zip(lrc_lines, new_starts)):
                    if i + 1 < len(new_starts):
                        ne = new_starts[i + 1]
                    else:
                        ne = ns + (e - s)
                    rebuilt.append((ns, ne, t))
                lrc_lines = rebuilt

        segments = self._build_segments(reference_text, lrc_lines, audio_duration_s)
        if not segments:
            logger.info(
                "wav2vec2: no segments to align for %s (lang=%s)",
                tag,
                language,
            )
            return []

        logger.info(
            "wav2vec2: align start %s lang=%s segments=%d lrc_lines=%s shifted=%s",
            tag,
            language,
            len(segments),
            "yes" if lrc_lines is not None else "no",
            "yes" if self.last_line_starts else "no",
        )
        t0 = time.monotonic()
        aligned = wx.align(
            segments,
            self._align_model,
            self._align_meta,
            audio_path,
            self._device,
            return_char_alignments=True,
        )

        aligned_words = _words_with_char_parts(aligned)
        logger.info(
            "wav2vec2: align done %s lang=%s words=%d elapsed=%.2fs",
            tag,
            language,
            len(aligned_words),
            time.monotonic() - t0,
        )
        # wav2vec2 can silently drop tokens it couldn't align phonetically
        # (weak onsets, overlapping instruments). Route through the mapper
        # so missing reference tokens get interpolated within their line
        # window rather than vanishing from the output.
        if lrc_lines is not None:
            mapped = map_whisper_to_reference_by_lines(aligned_words, lrc_lines)
        else:
            mapped = map_whisper_to_reference(aligned_words, reference_text)
        # Safety net: if the reference text contained hallucinated junk
        # that made the aligner overshoot, drop words whose timings are
        # past the audio. Without this, libass exits on createTrack.
        if audio_duration_s:
            cutoff = audio_duration_s + 2.0
            clean = [w for w in mapped if w.start < audio_duration_s and w.end <= cutoff]
            if len(clean) < len(mapped):
                logger.warning(
                    "wav2vec2: dropped %d/%d words whose timings exceeded "
                    "audio length %.1fs (hallucinated reference text?)",
                    len(mapped) - len(clean),
                    len(mapped),
                    audio_duration_s,
                )
            return clean
        return mapped

    def _ensure_align_model(self, language: str) -> None:
        if self._align_model is None or self._align_lang != language:
            logger.info(
                "wav2vec2: loading align model lang=%s device=%s (previous lang=%s)",
                language,
                self._device,
                self._align_lang,
            )
            self._align_model, self._align_meta = self._whisperx.load_align_model(
                language_code=language, device=self._device
            )
            self._align_lang = language
            logger.info("wav2vec2: align model ready lang=%s", language)

    @staticmethod
    def _build_segments(
        reference_text: str,
        lrc_lines: list[tuple[float, float, str]] | None,
        audio_duration_s: float | None = None,
    ) -> list[dict]:
        if lrc_lines is not None:
            return [
                {"start": float(s), "end": float(e), "text": text}
                for (s, e, text) in lrc_lines
                if text.strip()
            ]
        text = reference_text.strip()
        if not text:
            return []
        # Cap the segment at the actual audio length when we have it.
        # whisperx is supposed to clamp automatically, but on hallucinated
        # reference text (e.g. Genius page chrome like "4 Contributors"
        # leaking in) it can overshoot and produce timestamps hours past
        # the song — libass then crashes on createTrack.
        end = (
            audio_duration_s
            if audio_duration_s and audio_duration_s > 0
            else _WHOLE_SONG_SEGMENT_END_S
        )
        return [{"start": 0.0, "end": end, "text": text}]


def _words_with_char_parts(aligned: dict) -> list[Word]:
    """Assemble ``Word`` list from whisperx output, attaching per-char parts.

    Each ``segment`` in the whisperx result carries a ``chars`` list (one
    entry per glyph of the segment's input text, whitespace included) and
    a ``words`` list. Char entries have ``{"char", "start", "end",
    "score"}`` for glyphs the CTC backtrace aligned; whitespace and
    unalignable glyphs arrive without ``start``/``end``. We split chars
    into groups at spaces and zip with the words list 1:1, producing
    ``WordPart`` entries for glyphs with valid timings - those become
    per-character ``\\kf`` fills in the rendered ASS.

    Words whose glyphs all lacked timings get ``parts=None`` and render
    as a single ``\\kf`` spanning the word's full duration (same as the
    pre-char-alignment behaviour).
    """
    out: list[Word] = []
    for seg in aligned.get("segments", []):
        seg_words = seg.get("words") or []
        char_groups = _group_chars_by_word(seg.get("chars") or [])
        for word_idx, word in enumerate(seg_words):
            if "start" not in word or "end" not in word:
                continue
            text = str(word.get("word", "")).strip()
            if not text:
                continue
            group = char_groups[word_idx] if word_idx < len(char_groups) else []
            parts = _build_parts_from_chars(group)
            word_start = float(word["start"])
            word_end = float(word["end"])
            parts_tuple = tuple(parts) if len(parts) > 1 else None
            parts_tuple = _smooth_spike_parts(parts_tuple, word_start, word_end)
            out.append(Word(text=text, start=word_start, end=word_end, parts=parts_tuple))
    return out


# When one char's CTC duration exceeds this multiple of the mean char
# duration in the word, the alignment looks like a "spike" - typical of
# a sustained sung vowel where CTC fires on a single high-confidence
# frame and packs the remaining chars into the trailing milliseconds.
# We redistribute uniformly in that case for steadier karaoke fill.
_SPIKE_REDIST_FACTOR = 3.0


def _smooth_spike_parts(
    parts: tuple[WordPart, ...] | None,
    word_start: float,
    word_end: float,
) -> tuple[WordPart, ...] | None:
    """Flatten CTC spike timings to a uniform per-char distribution.

    On sung sustained vowels CTC emits a high-confidence spike on the
    sustained glyph and assigns trailing chars near-zero durations. The
    karaoke fill then sits on one letter for seconds before racing
    through the remainder. Detect that pattern (max char duration much
    larger than the mean) and replace per-char timings with a uniform
    spread across the word's span - same total time, smoother visual.

    No-ops for words with fewer than two parts (single ``\\kf`` already)
    or with already-balanced char durations.
    """
    if not parts or len(parts) < 2:
        return parts
    durations = [p.end - p.start for p in parts]
    longest = max(durations)
    mean = sum(durations) / len(durations)
    if longest <= mean * _SPIKE_REDIST_FACTOR:
        return parts
    span = max(word_end - word_start, 0.01)
    per = span / len(parts)
    return tuple(
        WordPart(text=p.text, start=word_start + per * i, end=word_start + per * (i + 1))
        for i, p in enumerate(parts)
    )


def _group_chars_by_word(seg_chars: list[dict]) -> list[list[dict]]:
    """Split whisperx's flat char list into per-word char groups.

    Space characters are delimiters - they appear in the char list even
    though they carry no timings. We start a new group whenever a space
    is seen; leading spaces produce empty-group prefixes which we drop
    to stay aligned with the word list (which has no leading-space
    placeholder).
    """
    groups: list[list[dict]] = [[]]
    for entry in seg_chars:
        if not isinstance(entry, dict):
            continue
        ch = entry.get("char", "")
        if ch == " ":
            if groups[-1]:  # only start a new group after non-empty content
                groups.append([])
            continue
        groups[-1].append(entry)
    if groups and not groups[-1]:
        groups.pop()
    return groups


def _build_parts_from_chars(group: list[dict]) -> list[WordPart]:
    """``WordPart`` list for one word's char group. Drops unaligned glyphs."""
    parts: list[WordPart] = []
    for entry in group:
        ch = entry.get("char", "")
        if not ch:
            continue
        c_start = entry.get("start")
        c_end = entry.get("end")
        if c_start is None or c_end is None:
            continue
        parts.append(WordPart(text=ch, start=float(c_start), end=float(c_end)))
    return parts


def _parts_for_ref(
    parts: tuple[WordPart, ...] | None, ref_text: str
) -> tuple[WordPart, ...] | None:
    """Reconcile a whisper word's char parts with the reference token text.

    Aligned words normally carry their LRC-line glyphs verbatim, so
    ``"".join(p.text) == ref_text`` is the common case. When the joined
    parts appear as a substring of ``ref_text`` (e.g. reference has
    trailing punctuation the matcher normalized away), we attach the
    leading/trailing chars onto the first/last part so the renderer can
    still display the full reference glyph set. When the join doesn't
    occur in ``ref_text`` at all we give up and return ``None`` so the
    renderer falls back to one ``\\kf`` for the whole word - safer than
    emitting visibly wrong characters.
    """
    if not parts:
        return None
    joined = "".join(p.text for p in parts)
    if joined == ref_text:
        return parts
    idx = ref_text.find(joined)
    if idx < 0:
        return None
    prefix = ref_text[:idx]
    suffix = ref_text[idx + len(joined) :]
    new_parts = list(parts)
    if prefix:
        first = new_parts[0]
        new_parts[0] = WordPart(text=prefix + first.text, start=first.start, end=first.end)
    if suffix:
        last = new_parts[-1]
        new_parts[-1] = WordPart(text=last.text + suffix, start=last.start, end=last.end)
    return tuple(new_parts)


def map_whisper_to_reference(whisper_words: list[Word], reference_text: str) -> list[Word]:
    """Transfer whisper's word timings onto the reference text tokens.

    Matches reference tokens to whisper tokens via SequenceMatcher
    (case-insensitive, punctuation-normalized). Reference tokens without a
    direct match get linearly interpolated timings from their neighbors;
    tokens that can't be interpolated are dropped.
    """
    ref_tokens = reference_text.split()
    if not ref_tokens or not whisper_words:
        return []

    ref_norm = [_normalize(t) for t in ref_tokens]
    whisper_norm = [_normalize(w.text) for w in whisper_words]

    matched: list[Word | None] = [None] * len(ref_tokens)
    matcher = SequenceMatcher(a=ref_norm, b=whisper_norm, autojunk=False)
    for block in matcher.get_matching_blocks():
        for i in range(block.size):
            w = whisper_words[block.b + i]
            ref = ref_tokens[block.a + i]
            matched[block.a + i] = Word(
                text=ref, start=w.start, end=w.end, parts=_parts_for_ref(w.parts, ref)
            )

    return _interpolate_gaps(ref_tokens, matched)


def map_whisper_to_reference_by_lines(
    whisper_words: list[Word],
    lrc_lines: list[tuple[float, float, str]],
) -> list[Word]:
    """Per-line version of ``map_whisper_to_reference``.

    For each LRC line the matcher only sees whisper words whose timestamps
    fall inside ``[line_start - tolerance, line_end + tolerance]``. Repeated
    phrases elsewhere in the song are invisible to that line's matcher, so
    anchors can't migrate across line boundaries. Lines with no whisper
    anchors in their window get uniform timings across the window - the
    downstream ASS builder still renders per-word highlighting, just at
    line-level sync accuracy.
    """
    out: list[Word] = []
    for line_start, line_end, text in lrc_lines:
        ref_tokens = text.split()
        if not ref_tokens:
            continue
        lo = line_start - _LINE_WINDOW_TOLERANCE_S
        hi = line_end + _LINE_WINDOW_TOLERANCE_S
        line_whisper = [w for w in whisper_words if w.start >= lo and w.end <= hi]
        if not line_whisper:
            out.extend(_uniform_line_words(ref_tokens, line_start, line_end))
            continue
        ref_norm = [_normalize(t) for t in ref_tokens]
        whisper_norm = [_normalize(w.text) for w in line_whisper]
        matched: list[Word | None] = [None] * len(ref_tokens)
        matcher = SequenceMatcher(a=ref_norm, b=whisper_norm, autojunk=False)
        for block in matcher.get_matching_blocks():
            for i in range(block.size):
                w = line_whisper[block.b + i]
                ref = ref_tokens[block.a + i]
                matched[block.a + i] = Word(
                    text=ref,
                    start=w.start,
                    end=w.end,
                    parts=_parts_for_ref(w.parts, ref),
                )
        if not _anchors_look_credible(matched, line_start, line_end, len(ref_tokens)):
            logger.info(
                "wav2vec2: discarding anchors for line %.2f-%.2fs (CTC bleed "
                "from previous sustain); using uniform fallback for %r",
                line_start,
                line_end,
                text[:60],
            )
            out.extend(_uniform_line_words(ref_tokens, line_start, line_end))
            continue
        out.extend(_interpolate_line_gaps(ref_tokens, matched, line_start, line_end))
    return out


# Threshold for the "CTC bleed" guard: when a single word in a multi-word
# line absorbs more than this fraction of the line window, the alignment
# is almost certainly wrong (wav2vec2 latched onto the previous line's
# sustained vowel that crossed into this line's audio window). Same
# threshold is used to reject anchors that start past the line's midpoint
# in multi-word lines - the singer can't realistically delay the entire
# phrase that long without LRCLib having flagged a later line_start.
_BLEED_GUARD_FRACTION = 0.5


def _anchors_look_credible(
    matched: list[Word | None], line_start: float, line_end: float, num_words: int
) -> bool:
    """Heuristic check that wav2vec2's anchors aren't a CTC-bleed artifact.

    Returns False when the matched anchors show the classic bleed
    signature - one word eating more than half the line window, or the
    first anchor landing past the line's midpoint in a multi-word line.
    A False return tells the caller to discard anchors and fall back to
    uniform timing for this line.

    Single-word lines are always considered credible: a single sustained
    final note legitimately fills the line window.
    """
    anchors = [m for m in matched if m is not None]
    if not anchors or num_words < 2:
        return True
    window = line_end - line_start
    if window <= 0:
        return True
    threshold = window * _BLEED_GUARD_FRACTION
    if any((a.end - a.start) > threshold for a in anchors):
        return False
    first = next((m for m in matched if m is not None), None)
    if first and (first.start - line_start) > threshold:
        return False
    return True


# Whisper timestamps can drift by a second or so around real line boundaries;
# the tolerance extends each LRC line's window for candidate whisper words.
# Keep smaller than _ALIGNMENT_TOLERANCE_S in lyrics.py so the downstream
# overlap sanity check never trips on this path.
_LINE_WINDOW_TOLERANCE_S = 1.5


def _interpolate_line_gaps(
    ref_tokens: list[str],
    matched: list[Word | None],
    line_start: float,
    line_end: float,
) -> list[Word]:
    """Fill gaps in ``matched`` by interpolating between intra-line anchors.

    Leading/trailing gaps anchor against the LRC line window boundaries
    rather than bleeding into adjacent lines.
    """
    n = len(ref_tokens)
    out: list[Word] = []
    i = 0
    while i < n:
        if matched[i]:
            out.append(matched[i])  # type: ignore[arg-type]
            i += 1
            continue
        prev_end = out[-1].end if out else line_start
        j = i
        while j < n and matched[j] is None:
            j += 1
        next_start = matched[j].start if j < n else line_end  # type: ignore[union-attr]
        gap = j - i
        dur = max((next_start - prev_end) / gap, 0.01)
        for k in range(gap):
            start = prev_end + dur * k
            end = start + dur
            out.append(Word(text=ref_tokens[i + k], start=start, end=end))
        i = j
    return out


def _uniform_line_words(tokens: list[str], start: float, end: float) -> list[Word]:
    """Spread ``tokens`` evenly across ``[start, end]`` (no whisper anchor)."""
    duration = max(end - start, 0.01)
    per = duration / len(tokens)
    return [
        Word(text=t, start=start + per * i, end=start + per * (i + 1)) for i, t in enumerate(tokens)
    ]


def _normalize(token: str) -> str:
    return "".join(ch for ch in token.lower() if ch.isalnum())


def _interpolate_gaps(ref_tokens: list[str], matched: list[Word | None]) -> list[Word]:
    n = len(ref_tokens)
    out: list[Word] = []
    i = 0
    while i < n:
        if matched[i]:
            out.append(matched[i])  # type: ignore[arg-type]
            i += 1
            continue
        # Find gap [gap_start, gap_end) between prev matched and next matched.
        prev_end = out[-1].end if out else 0.0
        j = i
        while j < n and matched[j] is None:
            j += 1
        if j == n:
            return out  # no further anchor; drop trailing unmatched
        next_start = matched[j].start  # type: ignore[union-attr]
        gap = j - i
        dur = max((next_start - prev_end) / gap, 0.01)
        for k in range(gap):
            start = prev_end + dur * k
            end = start + dur
            out.append(Word(text=ref_tokens[i + k], start=start, end=end))
        i = j
    return out
