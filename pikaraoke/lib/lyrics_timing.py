"""Timing plain lyrics against a song's separated lead vocal with speech recognition.

Speech recognition mishears sung words, but places them well in time. Matching
the heard text against known lyrics keeps the lyrics' words and the recognizer's
timing, for songs whose lyrics nobody has timed.
"""

import difflib
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys

from pikaraoke.lib.get_platform import get_data_directory, use_spare_capacity

# A distilled large model: close to the full one on sung Chinese and English, and
# fast enough on a CPU to finish while the song before it plays.
WHISPER_MODEL = "turbo"

# A long song on a slow CPU; beyond this recognition is stuck.
TRANSCRIPTION_TIMEOUT_S = 30 * 60

# Share of a line's words that must be heard to place it. Sung words are misheard
# often enough that most lines are only partly recognized.
MIN_LINE_MATCH = 0.35
# Share of lines that must be placed for the timing to be used: below it the
# lyrics are likely another song's, or the vocal too buried to recognize.
MIN_LINES_PLACED = 0.5
# Spacing given to lines no recognized word places, beyond the first or last placed one.
UNPLACED_LINE_S = 3.0

# A word heard this long after the one before it starts a new phrase.
PHRASE_GAP_S = 1.5

# Speech detection's threshold for a karaoke track: the default 0.5 skips most
# singing under music, a lead left in included.
IN_MUSIC_SPEECH_THRESHOLD = 0.2
# Mean log-probability below which a recognized line is music misheard as words.
MIN_CONFIDENT_LOGPROB = -0.7
# Phrases and different words heard in a karaoke track that mean its lead was
# left in. A lead left in through its choruses gave 34 phrases of 54 words; plain
# music, at most one stock phrase of 13.
LEFT_IN_MIN_PHRASES = 5
LEFT_IN_MIN_WORDS = 20

_WORD_RE = re.compile(r"[^\W_]+")


def is_transcription_available() -> bool:
    """Whether faster-whisper is installed, via the optional `vocal-removal` extra."""
    return importlib.util.find_spec("faster_whisper") is not None


def transcribe_words(audio_path: str, in_music: bool = False) -> list[tuple[str, float]] | None:
    """Each word heard, with when it starts, or None on failure.

    `in_music` listens for singing buried in a karaoke track rather than in a
    lead vocal, keeping only words heard with confidence. Recognition runs in its
    own process: it holds the CPU for tens of seconds, which inside the server
    would stall every request.
    """
    model_dir = os.path.join(get_data_directory(), "whisper-models")
    mode = ["in-music"] if in_music else []
    process = subprocess.Popen(
        [sys.executable, "-m", "pikaraoke.lib.lyrics_timing", audio_path, model_dir] + mode,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    use_spare_capacity(process)
    try:
        output, errors = process.communicate(timeout=TRANSCRIPTION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        process.kill()
        output, errors = process.communicate()
    if process.returncode != 0:
        logging.error(f"Transcribing {audio_path} failed: {errors[-2000:]}")
        return None
    return [(word, start) for word, start in json.loads(output)]


def _tokens(text: str) -> list[str]:
    """Text as the units recognition is matched on: words, and each Chinese character.

    Recognition writes Chinese in either script whatever the lyrics use, so both
    sides are compared in simplified script.
    """
    from opencc import OpenCC  # pylint: disable=import-outside-toplevel

    tokens = []
    # Apostrophes dropped, so "I'm" and a heard "im" are the same word.
    for word in _WORD_RE.findall(OpenCC("t2s").convert(text.lower().replace("'", ""))):
        # Chinese is written without spaces, so a "word" here is a run of characters.
        tokens.extend([word] if word.isascii() else list(word))
    return tokens


def time_plain_lyrics(plain: str, words: list[tuple[str, float]]) -> str | None:
    """LRC for plain lyrics, each line timed by where its words were heard.

    The whole lyrics are matched against everything heard at once, in order, so a
    chorus sung several times pairs each occurrence with its own singing, and a
    spoken intro is left out. A line too little of which was heard is placed from
    its neighbours. None when too few lines are heard to trust the rest.
    """
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    heard, times = [], []
    for word, start in words:
        for token in _tokens(word):
            heard.append(token)
            times.append(start)
    line_tokens = [_tokens(line) for line in lines]
    sung = [token for tokens in line_tokens for token in tokens]
    # For each sung token, its line and its place in that line.
    sung_at = [(i, n) for i, tokens in enumerate(line_tokens) for n in range(len(tokens))]

    # Each line's matched (place in line, place heard) pairs, in order.
    matches: list[list[tuple[int, int]]] = [[] for _ in lines]
    matcher = difflib.SequenceMatcher(None, sung, heard, autojunk=False)
    for block in matcher.get_matching_blocks():
        for n in range(block.size):
            line, in_line = sung_at[block.a + n]
            matches[line].append((in_line, block.b + n))

    starts: list[float | None] = []
    # Where singing resumes after the previous line's last heard word.
    resumes: list[float | None] = []
    last_heard = -1
    for i in range(len(lines)):
        resumes.append(
            next(
                (t for t in times[last_heard + 1 :] if last_heard < 0 or t > times[last_heard]),
                None,
            )
        )
        if matches[i] and len(matches[i]) >= len(line_tokens[i]) * MIN_LINE_MATCH:
            in_line, at = matches[i][0]
            # A misheard opening still took about as many heard words as it has,
            # though not from before a pause, which it would have been sung after.
            for _ in range(in_line):
                if at - 1 <= last_heard or times[at] - times[at - 1] > PHRASE_GAP_S:
                    break
                at -= 1
            starts.append(times[at])
            last_heard = matches[i][-1][1]
        else:
            starts.append(None)

    placed = [i for i, start in enumerate(starts) if start is not None]
    if len(placed) < len(lines) * MIN_LINES_PLACED:
        return None
    for i, start in enumerate(starts):
        if start is not None:
            continue
        after = min((p for p in placed if p > i), default=None)
        if i == 0:
            starts[i] = max(0.0, starts[after] - UNPLACED_LINE_S * after)
        elif after is None:
            starts[i] = starts[i - 1] + UNPLACED_LINE_S
        elif (
            i - 1 in placed
            and resumes[i] is not None
            and starts[i - 1] < resumes[i] < starts[after]
        ):
            # Heard but misheard past matching: it starts where the singing picks up,
            # which across an instrumental break is far from evenly spaced.
            starts[i] = resumes[i]
        else:
            starts[i] = starts[i - 1] + (starts[after] - starts[i - 1]) / (after - i + 1)
    return "".join(
        f"[{int(start // 60):02d}:{start % 60:05.2f}]{line}\n" for line, start in zip(lines, starts)
    )


def lead_vocal_left_in(heard: list[tuple[str, float]]) -> bool:
    """Whether the words heard in a karaoke track show its lead singing was left in.

    Recognition invents a stock phrase or two in plain music, but a lead left in
    gives line after line, of many different words.
    """
    phrases = sum(
        1 for i, (_, start) in enumerate(heard) if i == 0 or start - heard[i - 1][1] > PHRASE_GAP_S
    )
    words = {token for word, _ in heard for token in _tokens(word)}
    return phrases >= LEFT_IN_MIN_PHRASES and len(words) >= LEFT_IN_MIN_WORDS


def _transcribe(audio_path: str, model_dir: str, in_music: bool) -> list[tuple[str, float]]:
    import numpy  # pylint: disable=import-outside-toplevel
    from faster_whisper import WhisperModel  # pylint: disable=import-outside-toplevel

    # Decoded by ffmpeg, which PiKaraoke already relies on to read every format.
    pcm = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", audio_path]
        + ["-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True,
        check=True,
    ).stdout
    model = WhisperModel(WHISPER_MODEL, compute_type="int8", download_root=model_dir)
    segments, _ = model.transcribe(
        numpy.frombuffer(pcm, numpy.float32),
        word_timestamps=True,
        # Skips the instrumental breaks, where recognition invents words.
        vad_filter=True,
        vad_parameters={"threshold": IN_MUSIC_SPEECH_THRESHOLD} if in_music else None,
        # Each line on its own: carrying text over spreads one mishearing into a loop.
        condition_on_previous_text=False,
    )
    return [
        (w.word, round(w.start, 2))
        for segment in segments
        if not in_music or segment.avg_logprob > MIN_CONFIDENT_LOGPROB
        for w in segment.words or []
    ]


if __name__ == "__main__":
    print(
        json.dumps(
            _transcribe(sys.argv[1], sys.argv[2], in_music=sys.argv[3:] == ["in-music"]),
            ensure_ascii=False,
        )
    )
