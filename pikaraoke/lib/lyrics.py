"""Synced lyrics from LRCLIB, written out as a karaoke .ass subtitle beside a song."""

import logging
import re

import requests

from pikaraoke.version import __version__ as VERSION

LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
# LRCLIB asks clients to identify themselves.
LRCLIB_USER_AGENT = f"PiKaraoke/{VERSION} (https://github.com/vicwomg/pikaraoke)"

# LRCLIB times lyrics to the studio recording. A video longer or shorter than that
# by more than this has its own intro or edit, and album timing would drift off it.
MAX_DURATION_DIFF_S = 3.0

# A line is held until the next one starts, but not across an instrumental break.
MAX_LINE_S = 10.0

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


def lrc_to_ass(lrc: str) -> str:
    """Render synced lyrics as a two-row KTV .ass subtitle with a colour sweep."""
    lines = parse_lrc(lrc)
    events = []
    # The last line rendered; it ended at 0 when there is none.
    prev_start: float | None = None
    prev_end = 0.0
    for i, (start, text) in enumerate(lines):
        if not text:
            continue
        next_start = lines[i + 1][0] if i + 1 < len(lines) else start + MAX_LINE_S
        end = min(next_start, start + MAX_LINE_S)
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


def fetch_synced_lyrics(query: str, duration: float) -> str | None:
    """Find LRC lyrics timed to a recording of this length, or None.

    Searches by free text, since a YouTube title rarely splits cleanly into artist
    and track, and keeps only results whose length matches the video's.
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
        return None

    candidates = [
        r
        for r in results
        if r.get("syncedLyrics")
        and r.get("duration")
        and abs(r["duration"] - duration) <= MAX_DURATION_DIFF_S
    ]
    if not candidates:
        logging.info(f"No synced lyrics matching {duration:.0f}s found for '{query}'")
        return None
    best = min(candidates, key=lambda r: abs(r["duration"] - duration))
    logging.info(f"Found synced lyrics for '{query}': {best['artistName']} - {best['trackName']}")
    return best["syncedLyrics"]
