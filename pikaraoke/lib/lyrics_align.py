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

# Silence threshold + minimum-duration for the leading-silence probe.
# -30 dBFS catches sustained vocals without being so sensitive that
# breath noise or de-essing artifacts qualify. 0.5s minimum avoids
# treating brief consonant gaps as the song's intro.
_SILENCE_THRESHOLD_DB = -30
_SILENCE_MIN_DURATION_S = 0.5

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


# How far on either side of the LRC-extrapolated time we'll accept a
# silence boundary as "this line's vocal onset". Bigger window catches
# longer drift, but risks matching the wrong silence when verses are
# closely spaced. 2.5s is loose enough to absorb the per-line drift seen
# on Mam Tę Moc (>1.5s per chorus) without hopping multi-line in dense
# sections.
_PER_LINE_SEARCH_WINDOW_S = 2.5


def _list_silence_ends(audio_path: str) -> list[float]:
    """Return every ``silence_end`` time reported by ffmpeg silencedetect.

    Each entry marks a moment where audio crosses from silence into
    sound, i.e. the start of a vocal segment on the demucs vocals stem.
    Returns an empty list on any ffmpeg failure (missing binary, parse
    failure, timeout) - callers fall back to no shift in that case.
    """
    import re
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                audio_path,
                "-af",
                f"silencedetect=n={_SILENCE_THRESHOLD_DB}dB:d={_SILENCE_MIN_DURATION_S}",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    return [float(m.group(1)) for m in re.finditer(r"silence_end:\s*([\d.]+)", proc.stderr)]


def _detect_per_line_starts(
    audio_path: str, lrc_lines: list[tuple[float, float, str]]
) -> list[float] | None:
    """Compute audio-aligned start times for each LRC line.

    Walks LRC lines and silence boundaries in lockstep, locking each line
    onto the nearest plausible silence_end when one is within
    ``_PER_LINE_SEARCH_WINDOW_S`` of the line's extrapolated audio time.
    Lines without a clear silence anchor inherit the cumulative shift
    from the most recently locked line - so continuous singing inside a
    verse stays smooth after the verse's first line snaps to its onset.

    Returns the list of new line starts (one per LRC entry, including
    empty-text rows so indices align with ``lrc_lines``). Each entry is
    pulled forward by ``_KARAOKE_LEAD_IN_S`` so subs fire just before the
    vocal attack rather than on its peak.

    Returns None when silence detection failed, when the audio doesn't
    start silent (no leading-silence to anchor against), or when the
    initial offset is below the correction threshold or implausibly
    large.
    """
    silence_ends = sorted(_list_silence_ends(audio_path))
    if not silence_ends or silence_ends[0] > _LEADING_SILENCE_MAX_S:
        return None
    first_idx = next(
        (i for i, (_, _, t) in enumerate(lrc_lines) if t.strip()),
        None,
    )
    if first_idx is None:
        return None
    first_lrc_start = float(lrc_lines[first_idx][0])
    first_audio = silence_ends[0]
    initial = first_audio - first_lrc_start
    if abs(initial) < _GLOBAL_OFFSET_MIN_S:
        return None
    if abs(initial) > _GLOBAL_OFFSET_MAX_S:
        return None

    out: list[float] = []
    cumulative = initial
    sil_idx = 0
    for lrc_start, _line_end, _text in lrc_lines:
        expected = float(lrc_start) + cumulative
        # Skip past silence boundaries that sit too far in the past for
        # this line to plausibly own them.
        while (
            sil_idx < len(silence_ends)
            and silence_ends[sil_idx] < expected - _PER_LINE_SEARCH_WINDOW_S
        ):
            sil_idx += 1
        if sil_idx < len(silence_ends):
            candidate = silence_ends[sil_idx]
            if abs(candidate - expected) <= _PER_LINE_SEARCH_WINDOW_S:
                # Snap to this silence_end and update the running offset
                # so downstream continuous lines pick up the new tempo.
                cumulative = candidate - float(lrc_start)
                expected = candidate
                sil_idx += 1  # consumed
        out.append(expected - _KARAOKE_LEAD_IN_S)
    return out


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
        Existing cached .ass files auto-invalidate.
        """
        return "wav2vec2-char-perline"

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
