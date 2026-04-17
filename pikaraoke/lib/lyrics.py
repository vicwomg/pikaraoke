"""Auto-fetch synced lyrics from LRCLib and render as ASS subtitles.

Pipeline:
  song_downloaded event -> LyricsService.fetch_and_convert
    1. Read <stem>.info.json (written by yt-dlp --write-info-json)
    2. Query LRCLib for syncedLyrics
    3. Convert LRC to line-level ASS and write <stem>.ass
    4. (Optional) in a background thread, run forced alignment and
       replace the ASS with per-word \\k-tagged highlighting.

The existing .ass stack (FileResolver, SubtitlesOctopus in splash.js)
renders the output automatically - no UI changes required.
"""

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from threading import Thread
from typing import Protocol

import requests

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.music_metadata import resolve_metadata

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net"
LRCLIB_TIMEOUT = 5.0

# LRC timestamp: [mm:ss.xx] or [mm:ss.xxx] or [mm:ss]
_LRC_TAG = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]")

# VTT cue timestamp line: `00:00:01.000 --> 00:00:03.000`.
_VTT_CUE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})\.(\d{3})"
)
# Inline tags like `<c>`, `</c>`, `<00:00:01.000>`, `<v Speaker>`.
_VTT_TAG = re.compile(r"<[^>]+>")

# Marker in [Script Info] used to distinguish auto-generated ASS from
# user-supplied Aegisub files. Auto-generated files may be overwritten
# on re-download; user files are left alone.
ASS_MARKER = "PiKaraoke Auto-Lyrics"

VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi")


@dataclass(frozen=True)
class Word:
    """A single word with its start/end time in seconds."""

    text: str
    start: float
    end: float


class Aligner(Protocol):
    """Produces word-level timings for a song given its audio and reference lyrics."""

    def align(self, audio_path: str, reference_text: str) -> list[Word]:
        ...


class LyricsService:
    """Fetches synced lyrics from LRCLib and writes them as ASS subtitles."""

    def __init__(
        self,
        download_path: str,
        events: EventSystem,
        aligner: Aligner | None = None,
    ) -> None:
        self._download_path = download_path
        self._events = events
        self._aligner = aligner

    def fetch_and_convert(self, song_path: str) -> None:
        """Entry point - event listener for `song_downloaded`."""
        try:
            self._do_fetch_and_convert(song_path)
        except Exception:
            logger.exception("Unexpected error fetching lyrics for %s", song_path)

    def _do_fetch_and_convert(self, song_path: str) -> None:
        # User-supplied Aegisub files (without the auto-lyrics marker) are sacred.
        if _user_owned_ass(song_path):
            logger.debug("Skipping: user-supplied .ass present for %s", song_path)
            _cleanup_yt_subs_and_info(song_path)
            return

        info = _read_info_json(song_path)

        # Step 1: baseline from YouTube VTT (always available when yt-dlp wrote any subs).
        wrote_from_vtt = _try_write_ass_from_vtt(song_path)
        if wrote_from_vtt:
            logger.info("Wrote .ass from YouTube VTT for %s", os.path.basename(song_path))

        # Step 2: override with LRCLib if metadata available and track is found.
        lrc = None
        if info:
            lrc = _fetch_lrclib(info["track"], info["artist"], info["duration"])
            if not lrc:
                # Fallback: iTunes canonicalizes the noisy YouTube-derived fields.
                clean = resolve_metadata(f"{info['artist']} - {info['track']}")
                if clean:
                    logger.info(
                        "iTunes canonicalized %r / %r -> %r / %r",
                        info["artist"],
                        info["track"],
                        clean["artist"],
                        clean["track"],
                    )
                    lrc = _fetch_lrclib(clean["track"], clean["artist"], info["duration"])
                    if lrc:
                        info = {**info, "artist": clean["artist"], "track": clean["track"]}
        if lrc:
            ass = _lrc_to_ass_line_level(lrc)
            if ass:
                _write_ass_atomic(song_path, ass)
                logger.info(
                    "LRCLib: wrote line-level .ass for %s - %s",
                    info["artist"],
                    info["track"],
                )

        _cleanup_yt_subs_and_info(song_path)

        if not wrote_from_vtt and not lrc:
            logger.info("No lyrics source for %s", os.path.basename(song_path))
            return

        # Per-word forced alignment requires reference lyrics text.
        if self._aligner and lrc:
            Thread(
                target=self._upgrade_to_word_level,
                args=(song_path, lrc),
                name=f"lyrics-align-{os.path.basename(song_path)}",
                daemon=True,
            ).start()

    def _upgrade_to_word_level(self, song_path: str, lrc: str) -> None:
        if self._aligner is None:
            return
        try:
            plain = _lrc_plain_text(lrc)
            words = self._aligner.align(song_path, plain)
            if not words:
                return
            ass = _words_to_ass_with_k_tags(words, lrc)
            if ass:
                _write_ass_atomic(song_path, ass)
                logger.info("Upgraded to per-word .ass for %s", song_path)
        except Exception:
            logger.warning(
                "word-level alignment failed for %s, keeping line-level",
                song_path,
                exc_info=True,
            )

    def reprocess_library(self, song_paths: list[str]) -> int:
        """Upgrade existing line-level auto-lyrics to word-level in the background.

        Candidates are songs with an auto-generated ``.ass`` (carries the marker)
        that lacks ``\\k`` tags - i.e. files produced before whisperx was
        available. No-op when no aligner is configured or nothing qualifies.

        Returns the number of songs scheduled for upgrade. Processing runs
        serially in a single daemon thread so the aligner doesn't thrash CPU/GPU.
        """
        if self._aligner is None:
            return 0
        candidates = [p for p in song_paths if _needs_word_level_upgrade(p)]
        if not candidates:
            return 0
        logger.info(
            "Reprocessing %d song(s) to word-level karaoke captions in the background",
            len(candidates),
        )
        Thread(
            target=self._reprocess_batch,
            args=(candidates,),
            name="lyrics-reprocess",
            daemon=True,
        ).start()
        return len(candidates)

    def _reprocess_batch(self, song_paths: list[str]) -> None:
        for song_path in song_paths:
            try:
                self._reprocess_one(song_path)
            except Exception:
                logger.exception("reprocess failed for %s", song_path)

    def _reprocess_one(self, song_path: str) -> None:
        """Re-fetch LRCLib from the filename-derived title, then align to word-level."""
        if self._aligner is None:
            return
        if not _needs_word_level_upgrade(song_path):
            return  # raced with another update
        title = _title_from_filename(song_path)
        if not title:
            logger.debug("reprocess: could not extract title from %s", song_path)
            return
        meta = resolve_metadata(title)
        if not meta:
            logger.debug("reprocess: iTunes had no match for %r", title)
            return
        lrc = _fetch_lrclib(meta["track"], meta["artist"], None)
        if not lrc:
            logger.debug(
                "reprocess: LRCLib had no match for %r / %r", meta["artist"], meta["track"]
            )
            return
        self._upgrade_to_word_level(song_path, lrc)


def _needs_word_level_upgrade(song_path: str) -> bool:
    """True when <stem>.ass is auto-generated AND has no \\k tags yet."""
    path = _ass_path(song_path)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    if ASS_MARKER not in content:
        return False  # user-owned Aegisub file
    # Any \k tag means it's already word-level.
    return "\\k" not in content


def _title_from_filename(song_path: str) -> str:
    """Strip the 11-char YouTube ID suffix (both ``---ID`` and ``[ID]`` forms).

    Lightweight replacement for SongManager.filename_from_path so lyrics.py
    stays free of the SongManager dependency.
    """
    stem = os.path.splitext(os.path.basename(song_path))[0]
    # Triple-dash PiKaraoke form
    m = re.search(r"---([A-Za-z0-9_-]{11})$", stem)
    if m:
        return stem[: m.start()].strip()
    # yt-dlp brackets form
    m = re.search(r"\s*\[([A-Za-z0-9_-]{11})\]$", stem)
    if m:
        return stem[: m.start()].strip()
    return stem.strip()


# ----- info.json reading -----


def _info_json_path(song_path: str) -> str:
    stem, _ext = os.path.splitext(song_path)
    return f"{stem}.info.json"


def _ass_path(song_path: str) -> str:
    stem, _ext = os.path.splitext(song_path)
    return f"{stem}.ass"


def _read_info_json(song_path: str) -> dict | None:
    """Read yt-dlp info.json and extract track/artist/duration.

    Falls back to parsing "Artist - Track" from the title when yt-dlp
    didn't provide dedicated fields (common for non-music videos).
    Returns None when artist/track cannot be determined.
    """
    path = _info_json_path(song_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to read %s: %s", path, e)
        return None

    track = (data.get("track") or "").strip()
    artist = (data.get("artist") or "").strip()
    duration = data.get("duration")

    if not track or not artist:
        title = (data.get("title") or "").strip()
        if " - " in title:
            left, right = title.split(" - ", 1)
            artist = artist or left.strip()
            track = track or right.strip()

    if not track or not artist:
        return None
    return {"track": track, "artist": artist, "duration": duration}


def _cleanup_info_json(song_path: str) -> None:
    path = _info_json_path(song_path)
    if os.path.exists(path):
        try:
            os.unlink(path)
        except OSError as e:
            logger.warning("failed to remove %s: %s", path, e)


# ----- LRCLib client -----


def _fetch_lrclib(track: str, artist: str, duration: int | float | None) -> str | None:
    """Query LRCLib for syncedLyrics; None when none found or request failed."""
    get_params: dict[str, str | int] = {"track_name": track, "artist_name": artist}
    if duration:
        get_params["duration"] = int(duration)
    try:
        r = requests.get(f"{LRCLIB_BASE}/api/get", params=get_params, timeout=LRCLIB_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            synced = data.get("syncedLyrics")
            if synced:
                return synced
        r = requests.get(
            f"{LRCLIB_BASE}/api/search",
            params={"track_name": track, "artist_name": artist},
            timeout=LRCLIB_TIMEOUT,
        )
        if r.status_code == 200:
            for item in r.json():
                synced = item.get("syncedLyrics")
                if synced:
                    return synced
    except (requests.RequestException, ValueError) as e:
        logger.warning("LRCLib request failed: %s", e)
    return None


# ----- LRC parser -----


def _parse_lrc(lrc: str) -> list[tuple[float, str]]:
    """Parse LRC into sorted [(start_seconds, text), ...].

    Handles multi-time lines like `[00:12.34][00:25.67]chorus` by duplicating
    the text for each timestamp. Fractional seconds are interpreted as a
    decimal fraction (so `.45` = 0.45s, `.450` = 0.450s).
    """
    entries: list[tuple[float, str]] = []
    for raw in lrc.splitlines():
        tags = _LRC_TAG.findall(raw)
        if not tags:
            continue
        text = _LRC_TAG.sub("", raw).strip()
        if not text:
            continue
        for mm, ss, frac in tags:
            frac_s = int(frac) / (10 ** len(frac)) if frac else 0.0
            start = int(mm) * 60 + int(ss) + frac_s
            entries.append((start, text))
    entries.sort(key=lambda e: e[0])
    return entries


def _lrc_plain_text(lrc: str) -> str:
    """Tags stripped; one line per LRC entry. For forced-alignment reference."""
    return "\n".join(text for _start, text in _parse_lrc(lrc))


# ----- ASS builders -----


_ASS_STYLE = (
    "Style: Default,Arial,64,&H00FFFFFF,&H00FFFF00,&H00000000,&H80000000,"
    "0,0,0,0,100,100,0,0,1,3,1,2,40,40,80,1"
)


def _ass_header() -> str:
    return (
        "[Script Info]\n"
        f"Title: {ASS_MARKER}\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{_ASS_STYLE}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _format_ass_time(seconds: float) -> str:
    """ASS uses H:MM:SS.cc (centiseconds)."""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


_LAST_LINE_HOLD_S = 5.0


def _lrc_to_ass_line_level(lrc: str) -> str | None:
    """Convert LRC to ASS with one Dialogue per line, no highlighting."""
    entries = _parse_lrc(lrc)
    if not entries:
        return None
    out = [_ass_header()]
    for i, (start, text) in enumerate(entries):
        end = entries[i + 1][0] if i + 1 < len(entries) else start + _LAST_LINE_HOLD_S
        out.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Default,,0,0,0,,{_escape_ass(text)}\n"
        )
    return "".join(out)


def _words_to_ass_with_k_tags(words: list[Word], lrc: str) -> str | None:
    """Rebuild ASS using LRC line boundaries but add \\k tags from word timings.

    For each line: select words falling within its time window and render
    `{\\k<centiseconds>}word` tokens. Lines without matching words fall back
    to plain text.
    """
    entries = _parse_lrc(lrc)
    if not entries:
        return None
    out = [_ass_header()]
    for i, (start, text) in enumerate(entries):
        end = entries[i + 1][0] if i + 1 < len(entries) else start + _LAST_LINE_HOLD_S
        line_words = [w for w in words if start <= w.start < end]
        if line_words:
            tokens = [_k_token(w) for w in line_words]
            ass_text = " ".join(tokens)
        else:
            ass_text = _escape_ass(text)
        out.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Default,,0,0,0,,{ass_text}\n"
        )
    return "".join(out)


def _k_token(word: Word) -> str:
    dur_cs = max(1, int(round((word.end - word.start) * 100)))
    return f"{{\\k{dur_cs}}}{_escape_ass(word.text)}"


def _escape_ass(text: str) -> str:
    # Curly braces delimit override blocks in ASS; escape them.
    return text.replace("{", "\\{").replace("}", "\\}")


# ----- atomic write -----


def _write_ass_atomic(song_path: str, ass_content: str) -> None:
    """Write <stem>.ass atomically so a concurrent read never sees partial data."""
    target = _ass_path(song_path)
    directory = os.path.dirname(target) or "."
    fd, tmp = tempfile.mkstemp(suffix=".ass", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ass_content)
        os.replace(tmp, target)
    except OSError:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


# ----- VTT conversion -----


def _try_write_ass_from_vtt(song_path: str) -> bool:
    """Convert the best available YouTube VTT (if any) to ASS. Returns True on success."""
    vtt_path = _pick_best_vtt(song_path)
    if not vtt_path:
        return False
    try:
        with open(vtt_path, encoding="utf-8") as f:
            vtt = f.read()
    except OSError as e:
        logger.warning("failed to read %s: %s", vtt_path, e)
        return False
    ass = _vtt_to_ass(vtt)
    if not ass:
        return False
    _write_ass_atomic(song_path, ass)
    return True


def _pick_best_vtt(song_path: str) -> str | None:
    """Return the most suitable <stem>*.vtt path, or None if none exist.

    Preference order:
      1. Files without the `-orig` / `-auto` language suffix (manual uploads).
      2. Shorter language codes (e.g. `pl` beats `pl-PL`).
      3. Alphabetical as a final tiebreaker.
    """
    stem, _ext = os.path.splitext(song_path)
    directory = os.path.dirname(stem) or "."
    basename = os.path.basename(stem)
    candidates = []
    for name in os.listdir(directory):
        if not name.endswith(".vtt"):
            continue
        if not name.startswith(basename + "."):
            continue
        lang = name[len(basename) + 1 : -len(".vtt")]
        is_auto = "-orig" in lang or lang.endswith("-auto") or "auto" in lang
        candidates.append((is_auto, len(lang), lang, os.path.join(directory, name)))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][3]


def _parse_vtt_cues(vtt: str) -> list[tuple[float, float, str]]:
    """Parse WEBVTT into [(start_s, end_s, text)]. Inline tags stripped."""
    cues: list[tuple[float, float, str]] = []
    lines = vtt.splitlines()
    i = 0
    while i < len(lines):
        m = _VTT_CUE.search(lines[i])
        if not m:
            i += 1
            continue
        start = _vtt_ts_to_s(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _vtt_ts_to_s(m.group(5), m.group(6), m.group(7), m.group(8))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = _VTT_TAG.sub("", lines[i]).strip()
            if cleaned:
                text_lines.append(cleaned)
            i += 1
        if text_lines:
            cues.append((start, end, " ".join(text_lines)))
    return _dedup_rolling_cues(cues)


def _dedup_rolling_cues(
    cues: list[tuple[float, float, str]],
) -> list[tuple[float, float, str]]:
    """YouTube auto-captions repeat each line in a sliding window.

    If cue N's text starts with cue N-1's text, drop cue N-1 and keep only the
    fullest version. This collapses the sliding-window noise back into one line.
    """
    if not cues:
        return cues
    out: list[tuple[float, float, str]] = []
    for cue in cues:
        if out and cue[2].startswith(out[-1][2]):
            # Replace previous with the more complete version.
            out[-1] = (out[-1][0], cue[1], cue[2])
        else:
            out.append(cue)
    return out


def _vtt_ts_to_s(hh: str, mm: str, ss: str, ms: str) -> float:
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def _vtt_to_ass(vtt: str) -> str | None:
    cues = _parse_vtt_cues(vtt)
    if not cues:
        return None
    out = [_ass_header()]
    for start, end, text in cues:
        out.append(
            f"Dialogue: 0,{_format_ass_time(start)},{_format_ass_time(end)},"
            f"Default,,0,0,0,,{_escape_ass(text)}\n"
        )
    return "".join(out)


# ----- ownership check + cleanup -----


def _user_owned_ass(song_path: str) -> bool:
    """True when <stem>.ass exists but was NOT produced by PiKaraoke."""
    path = _ass_path(song_path)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(512)
    except OSError:
        return True  # safer to assume user-owned if unreadable
    return ASS_MARKER not in head


def _cleanup_yt_subs_and_info(song_path: str) -> None:
    """Remove yt-dlp byproducts (<stem>*.vtt, <stem>.info.json) after conversion."""
    stem, _ext = os.path.splitext(song_path)
    directory = os.path.dirname(stem) or "."
    basename = os.path.basename(stem)
    try:
        entries = os.listdir(directory)
    except OSError:
        entries = []
    for name in entries:
        if not name.startswith(basename + "."):
            continue
        if name.endswith(".vtt"):
            try:
                os.unlink(os.path.join(directory, name))
            except OSError as e:
                logger.warning("failed to remove %s: %s", name, e)
    _cleanup_info_json(song_path)
