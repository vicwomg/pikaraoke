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

from pikaraoke.lib.lyrics import Word

logger = logging.getLogger(__name__)


# Upper bound for the whole-song fallback segment when no LRC line windows
# are supplied. whisperx clamps segment ends to actual audio length.
_WHOLE_SONG_SEGMENT_END_S = 24 * 3600.0


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

    @property
    def model_id(self) -> str:
        """Stable identifier recorded alongside aligned .ass for cache invalidation.

        Bumped from ``whisperx-<size>`` to ``wav2vec2-lrc`` when we dropped
        the whisper ASR step, so existing cached .ass files auto-invalidate
        and get re-generated with the higher-quality direct alignment.
        """
        return "wav2vec2-lrc"

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
        if not language:
            raise ValueError(
                "language required: wav2vec2 is per-language, caller must supply it"
            )
        wx = self._whisperx
        self.last_detected_language = language
        self._ensure_align_model(language)

        segments = self._build_segments(reference_text, lrc_lines)
        if not segments:
            return []

        aligned = wx.align(
            segments,
            self._align_model,
            self._align_meta,
            audio_path,
            self._device,
            return_char_alignments=False,
        )
        aligned_words = [
            Word(text=str(w.get("word", "")).strip(), start=float(w["start"]), end=float(w["end"]))
            for w in aligned.get("word_segments", [])
            if "start" in w and "end" in w and w.get("word")
        ]
        # wav2vec2 can silently drop tokens it couldn't align phonetically
        # (weak onsets, overlapping instruments). Route through the mapper
        # so missing reference tokens get interpolated within their line
        # window rather than vanishing from the output.
        if lrc_lines is not None:
            return map_whisper_to_reference_by_lines(aligned_words, lrc_lines)
        return map_whisper_to_reference(aligned_words, reference_text)

    def _ensure_align_model(self, language: str) -> None:
        if self._align_model is None or self._align_lang != language:
            self._align_model, self._align_meta = self._whisperx.load_align_model(
                language_code=language, device=self._device
            )
            self._align_lang = language

    @staticmethod
    def _build_segments(
        reference_text: str,
        lrc_lines: list[tuple[float, float, str]] | None,
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
        # No line windows available: align against the whole song. The
        # large upper bound is benign - whisperx clamps to audio length.
        return [{"start": 0.0, "end": _WHOLE_SONG_SEGMENT_END_S, "text": text}]


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
            matched[block.a + i] = Word(text=ref_tokens[block.a + i], start=w.start, end=w.end)

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
                matched[block.a + i] = Word(
                    text=ref_tokens[block.a + i], start=w.start, end=w.end
                )
        out.extend(_interpolate_line_gaps(ref_tokens, matched, line_start, line_end))
    return out


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
        Word(text=t, start=start + per * i, end=start + per * (i + 1))
        for i, t in enumerate(tokens)
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
