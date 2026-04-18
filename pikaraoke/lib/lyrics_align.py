"""WhisperX-based forced alignment for per-word karaoke highlighting.

`whisperx` is an optional dependency - installed via `pip install 'pikaraoke[align]'`.
The import is lazy so the rest of the app keeps working when it's absent.

Output contract: a list of `Word(text, start, end)` where `text` comes from
the reference lyrics (LRCLib) but timings come from whisper's acoustic alignment.
"""

import logging
from difflib import SequenceMatcher

from pikaraoke.lib.lyrics import Word

logger = logging.getLogger(__name__)


class WhisperXAligner:
    """Transcribes audio with whisper and aligns per-word timings to reference lyrics.

    Model and alignment-model instances are cached on the first call - subsequent
    songs reuse them. Alignment models are per-language; the cache invalidates
    when a song in a new language appears.
    """

    def __init__(self, model_size: str = "base", device: str = "cpu") -> None:
        import whisperx  # lazy - optional dep

        self._whisperx = whisperx
        self._model_size = model_size
        self._device = device
        self._asr_model = None
        self._align_model = None
        self._align_meta = None
        self._align_lang: str | None = None

    @property
    def model_id(self) -> str:
        """Stable identifier recorded alongside aligned .ass for cache invalidation."""
        return f"whisperx-{self._model_size}"

    def align(self, audio_path: str, reference_text: str) -> list[Word]:
        wx = self._whisperx
        if self._asr_model is None:
            compute_type = "float16" if self._device != "cpu" else "int8"
            self._asr_model = wx.load_model(
                self._model_size, self._device, compute_type=compute_type
            )
        asr = self._asr_model.transcribe(audio_path)
        language = asr.get("language", "en")

        if self._align_model is None or self._align_lang != language:
            self._align_model, self._align_meta = wx.load_align_model(
                language_code=language, device=self._device
            )
            self._align_lang = language

        aligned = wx.align(
            asr["segments"],
            self._align_model,
            self._align_meta,
            audio_path,
            self._device,
            return_char_alignments=False,
        )
        whisper_words = [
            Word(text=str(w.get("word", "")).strip(), start=float(w["start"]), end=float(w["end"]))
            for w in aligned.get("word_segments", [])
            if "start" in w and "end" in w and w.get("word")
        ]
        return map_whisper_to_reference(whisper_words, reference_text)


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
