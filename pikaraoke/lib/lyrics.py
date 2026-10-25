"""Synced lyrics from LRCLIB, written out as a karaoke .ass subtitle beside a song."""

import logging
import re
import subprocess

import requests

from pikaraoke.lib.metadata_parser import normalize_for_comparison
from pikaraoke.version import __version__ as VERSION

LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
# LRCLIB asks clients to identify themselves.
LRCLIB_USER_AGENT = f"PiKaraoke/{VERSION} (https://github.com/vicwomg/pikaraoke)"

# LRCLIB times lyrics to the studio recording. A video longer or shorter than that
# by more than this has its own intro or edit, and album timing would drift off it.
MAX_DURATION_DIFF_S = 3.0

# A line is held until the next one starts, but not across an instrumental break.
MAX_LINE_S = 10.0

# Vocal activity is measured in frames this long, on audio resampled to this rate.
ACTIVITY_FRAME_S = 0.1
_ACTIVITY_RATE = 8000
# Anything this close to the lead vocal's loud passages counts as singing.
ACTIVITY_RANGE_DB = 30.0

# How far alignment may move lyrics: a music video's intro can run a minute or
# two past the recording's, and rarely starts much later into the song.
MAX_ALIGN_EARLIER_S = 30.0
MAX_ALIGN_LATER_S = 150.0
# The share of each line, from its start, taken to be sung when aligning.
SUNG_FRACTION = 0.7
# Below this overlap between sung lines and singing, the alignment found nowhere
# the lyrics fit. It is no test of the song itself: other songs' lyrics score
# nearly as well against a busy vocal.
MIN_ALIGNMENT_AGREEMENT = 0.5
# Alignments of different uploads that place the first line this close agree.
ALIGNMENT_CONSENSUS_S = 1.5
# Hand-timed LRC stamps a line a little before it is sung, and alignment finds the
# singing itself; measured at 0.6-0.9s on lyrics timed to their own video.
LRC_LEAD_S = 0.7

# How early a line appears when no line before it is on screen to make room for it.
LEAD_IN_S = 3.0

_TIMESTAMP_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")

# KTV layout: lines alternate between a left-aligned upper row and a right-aligned
# lower row, so the next line is readable while the current one is sung. The \kf
# sweep fills each line from SecondaryColour (unsung) to PrimaryColour (sung).
_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, \
Alignment, MarginL, MarginR, MarginV, Encoding
Style: Upper,Arial,80,&H00FFB040,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,\
1,160,160,250,1
Style: Lower,Arial,80,&H00FFB040,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,\
3,160,160,130,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def parse_lrc(lrc: str) -> list[tuple[float, str]]:
    """Read LRC into (start seconds, text) pairs, in time order.

    A blank line is kept: it marks where the line before it stops being sung.
    """
    lines = []
    for raw in lrc.splitlines():
        stamps = list(_TIMESTAMP_RE.finditer(raw))
        if not stamps:
            continue
        text = raw[stamps[-1].end() :].strip()
        # A chorus can be written once under several timestamps.
        for stamp in stamps:
            lines.append((int(stamp.group(1)) * 60 + float(stamp.group(2)), text))
    return sorted(lines, key=lambda line: line[0])


def _ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, centis = divmod(centis, 360000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_text(text: str) -> str:
    # Braces open override blocks and a backslash starts a tag.
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _timed_lines(lrc: str) -> list[tuple[float, float, str]]:
    """(start, end, text) for each sung line.

    A line is held until the next one starts, but not across an instrumental break.
    """
    lines = parse_lrc(lrc)
    timed = []
    for i, (start, text) in enumerate(lines):
        if not text:
            continue
        next_start = lines[i + 1][0] if i + 1 < len(lines) else start + MAX_LINE_S
        timed.append((start, min(next_start, start + MAX_LINE_S), text))
    return timed


def lrc_to_ass(lrc: str, offset: float = 0.0) -> str:
    """Render synced lyrics as a two-row KTV .ass subtitle with a colour sweep.

    `offset` moves every line later by that many seconds, or earlier if negative.
    """
    events = []
    # The last line rendered; it ended at 0 when there is none.
    prev_start: float | None = None
    prev_end = 0.0
    for start, end, text in _timed_lines(lrc):
        start, end = start + offset, end + offset
        if end <= 0:
            continue  # Sung before the video begins
        if prev_start is not None and prev_end >= start:
            # Sung straight after the previous line: show it as that one starts.
            appear = prev_start
        else:
            appear = max(start - LEAD_IN_S, prev_end)
        style = "Upper" if len(events) % 2 == 0 else "Lower"
        # An empty \k syllable holds the sweep back until the line is sung.
        events.append(
            f"Dialogue: 0,{_ass_time(appear)},{_ass_time(end)},{style},,0,0,0,,"
            f"{{\\k{round((start - appear) * 100)}}}"
            f"{{\\kf{round((end - start) * 100)}}}{_ass_text(text)}"
        )
        prev_start, prev_end = start, end
    return _ASS_HEADER + "\n".join(events) + "\n"


_ASS_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{2})")


def _parse_ass_time(stamp: str) -> float:
    hours, minutes, secs, centis = (int(g) for g in _ASS_TIME_RE.fullmatch(stamp).groups())
    return hours * 3600 + minutes * 60 + secs + centis / 100


def shift_ass(ass: str, seconds: float) -> str:
    """Move every subtitle event later by `seconds`, or earlier if negative.

    An event pushed entirely before the start is dropped; one straddling it starts at 0.
    """
    shifted = []
    for line in ass.splitlines():
        if not line.startswith("Dialogue:"):
            shifted.append(line)
            continue
        fields = line.split(",", 9)
        end = _parse_ass_time(fields[2]) + seconds
        if end <= 0:
            continue
        fields[1] = _ass_time(_parse_ass_time(fields[1]) + seconds)
        fields[2] = _ass_time(end)
        shifted.append(",".join(fields))
    return "\n".join(shifted) + "\n"


def search_synced_lyrics(query: str) -> list[dict]:
    """LRCLIB results for a free-text query that carry synced lyrics.

    Free text, since a YouTube title rarely splits cleanly into artist and track.
    """
    try:
        response = requests.get(
            LRCLIB_SEARCH_URL,
            params={"q": query},
            headers={"User-Agent": LRCLIB_USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
        results = response.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        logging.warning(f"Lyrics lookup failed for '{query}': {e}")
        return []
    return [r for r in results if r.get("syncedLyrics") and r.get("duration")]


def pick_by_duration(results: list[dict], duration: float) -> str | None:
    """The lyrics timed to a recording of this length, or None.

    Lyrics timed by hand to this very version beat any alignment, so a matching
    length is trusted first, and unmoved.
    """
    candidates = [r for r in results if abs(r["duration"] - duration) <= MAX_DURATION_DIFF_S]
    if not candidates:
        return None
    return min(candidates, key=lambda r: abs(r["duration"] - duration))["syncedLyrics"]


def vocal_activity(vocals_path: str) -> list[bool] | None:
    """Whether the lead vocal is sounding, per ACTIVITY_FRAME_S of the song.

    ffmpeg measures the loudness so a long song costs no Python time on the server's
    event loop. A separated lead vocal is near-silent between lines, so anything
    within ACTIVITY_RANGE_DB of its loud passages counts as singing.
    """
    frame_samples = round(_ACTIVITY_RATE * ACTIVITY_FRAME_S)
    # Resampled inside the filter chain: -ar would apply only after the measuring.
    loudness_filter = (
        f"aformat=sample_rates={_ACTIVITY_RATE}:channel_layouts=mono,"
        f"asetnsamples=n={frame_samples}:p=0,astats=metadata=1:reset=1,"
        "ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-"
    )
    result = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", vocals_path]
        + ["-af", loudness_filter, "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logging.warning(f"Measuring vocal activity failed for {vocals_path}: {result.stderr}")
        return None
    levels = [
        float(line.split("=", 1)[1])
        for line in result.stdout.splitlines()
        if line.startswith("lavfi.astats.Overall.RMS_level=")
    ]
    finite = sorted(level for level in levels if level != float("-inf"))
    if not finite:
        return None
    loud = finite[int(len(finite) * 0.95)]
    return [level > loud - ACTIVITY_RANGE_DB for level in levels]


def align_lyrics(lrc: str, activity: list[bool]) -> tuple[float, float]:
    """Find how far to move the lyrics so their lines land on the singing.

    Slides the lines across the vocal activity and keeps the offset covering the
    most singing. Only the front of each line counts as sung: a line is held until
    the next starts, and its tail is often a breath. Returns (offset seconds, agreement from 0 to 1), the
    agreement being the Dice overlap between sung lines and singing.
    """
    frames = len(activity)
    voiced_before = [0]
    for voiced in activity:
        voiced_before.append(voiced_before[-1] + voiced)
    intervals = [
        (
            round(start / ACTIVITY_FRAME_S),
            round((start + (end - start) * SUNG_FRACTION) / ACTIVITY_FRAME_S),
        )
        for start, end, _ in _timed_lines(lrc)
    ]
    sung = sum(end - start for start, end in intervals)
    voiced_total = voiced_before[-1]
    if not sung or not voiced_total:
        return 0.0, 0.0

    def overlap(shift: int) -> int:
        def clamp(frame: int) -> int:
            return min(max(frame, 0), frames)

        return sum(
            voiced_before[clamp(end + shift)] - voiced_before[clamp(start + shift)]
            for start, end in intervals
        )

    earliest = -round(MAX_ALIGN_EARLIER_S / ACTIVITY_FRAME_S)
    latest = round(MAX_ALIGN_LATER_S / ACTIVITY_FRAME_S)
    # max() keeps the first of equals, so a tie goes to the earliest offset: lines
    # starting as the singing starts, rather than partway into it.
    best_shift = max(range(earliest, latest + 1), key=overlap)
    return best_shift * ACTIVITY_FRAME_S, 2 * overlap(best_shift) / (sung + voiced_total)


def _same_song(track_name: str, query: str) -> bool:
    """Whether an LRCLIB track names the song searched for.

    Alignment cannot tell songs apart, since nearly any lyrics fit a busy vocal
    somewhere, so a search result for another song has to be dropped by name.
    """
    track = normalize_for_comparison(track_name)
    wanted = normalize_for_comparison(query)
    return bool(track) and (track in wanted or wanted in track)


def best_alignment(
    results: list[dict], activity: list[bool], query: str
) -> tuple[str, float] | None:
    """Lyrics placed on the song's singing, for a video whose length matches none.

    Each upload of the song's lyrics is aligned on its own and the median placement
    is kept: one alignment is only good to a second or two, but uploads agreeing
    with each other are not chance. Returns None without that agreement.
    """
    placements = []  # (first line's time once moved, lyrics, offset)
    candidates = (r["syncedLyrics"] for r in results if _same_song(r.get("trackName", ""), query))
    for lrc in dict.fromkeys(candidates):
        offset, agreement = align_lyrics(lrc, activity)
        lines = _timed_lines(lrc)
        if lines and agreement >= MIN_ALIGNMENT_AGREEMENT:
            placements.append((lines[0][0] + offset, lrc, offset))
    if not placements:
        return None
    placements.sort(key=lambda p: p[0])
    first_line, lrc, offset = placements[len(placements) // 2]
    offset -= LRC_LEAD_S
    agreeing = sum(abs(p[0] - first_line) <= ALIGNMENT_CONSENSUS_S for p in placements)
    if agreeing * 2 <= len(placements) and len(placements) > 1:
        logging.info(f"Lyrics alignments for '{query}' disagree; not using them")
        return None
    logging.info(f"Aligned lyrics for '{query}' with offset {offset:+.1f}s")
    return lrc, offset
