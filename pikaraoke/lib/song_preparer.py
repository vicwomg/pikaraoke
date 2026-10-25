"""Background preparation of queued songs: an instrumental track and synced lyrics."""

import logging
import os
import time
from collections.abc import Callable
from queue import Queue

from gevent import Greenlet, spawn

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ffmpeg import get_media_duration
from pikaraoke.lib.file_resolver import find_ass_file
from pikaraoke.lib.lyrics import (
    LRC_LEAD_S,
    best_alignment,
    cjk_title,
    lrc_to_ass,
    pick_by_duration,
    pick_plain_lyrics,
    search_lyrics,
    vocal_activity,
)
from pikaraoke.lib.lyrics_timing import (
    is_transcription_available,
    time_plain_lyrics,
    transcribe_words,
)
from pikaraoke.lib.metadata_parser import (
    clean_search_query,
    has_on_screen_lyrics,
    is_karaoke_title,
)
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.vocal_remover import (
    find_instrumental,
    find_vocals,
    is_vocal_removal_available,
    separate_vocals,
)

# Only plain video files carry a subtitle; a CDG draws its own lyrics.
_SUBTITLED_EXTENSIONS = (".mp4", ".webm", ".mkv", ".mov", ".avi")


class SongPreparer:
    """Readies songs as they are queued, one at a time, ahead of their turn.

    Serial on purpose: a separation takes a CPU-minute or more, and two at once
    would only finish both later. Each task is attempted once per song per run,
    so a lyrics miss is not looked up again every time the queue changes.
    """

    def __init__(
        self,
        events: EventSystem,
        preferences: PreferenceManager,
        filename_from_path: Callable[..., str],
    ) -> None:
        self._events = events
        self._preferences = preferences
        self._filename_from_path = filename_from_path
        self._pending: Queue = Queue()
        self._waiting: set[str] = set()
        self._attempted: set[tuple[str, str]] = set()
        self._worker: Greenlet | None = None
        # The task under way, as (task, song), and when it started.
        self._working: tuple[str, str] | None = None
        self._working_since = 0.0
        self._working_duration: float | None = None
        # Seconds a separation takes per second of song, from the last one; None until then.
        self._separation_rate: float | None = None

    def start(self) -> None:
        """Start the worker greenlet."""
        self._worker = spawn(self._process_queue)

    def _due_tasks(self, song_path: str) -> list[str]:
        tasks = []
        # Vocals first: the separated lead vocal is what lyrics are aligned against.
        # A song already having the result is skipped, so it is not shown waiting.
        if (
            self._preferences.get_or_default("vocal_removal")
            and is_vocal_removal_available()
            and not find_instrumental(song_path)
        ):
            tasks.append("vocals")
        if self._preferences.get_or_default("auto_lyrics") and not find_ass_file(song_path):
            tasks.append("lyrics")
        return [t for t in tasks if (t, song_path) not in self._attempted]

    def prepare(self, song_path: str) -> None:
        """Queue a song for whatever preparation is enabled and not yet tried."""
        if song_path in self._waiting or not self._due_tasks(song_path):
            return
        self._waiting.add(song_path)
        self._pending.put(song_path)

    def progress(self, song_path: str) -> dict[str, str | int | None] | None:
        """What is being done for a song, for showing singers, or None if nothing is due.

        `task` is "vocals" or "lyrics" while under way, or "waiting" while songs ahead
        of it are prepared. A separation also gives `remaining_s` once one has been timed.
        """
        if self._working is not None and self._working[1] == song_path:
            task = self._working[0]
            remaining = None
            if task == "vocals" and self._separation_rate and self._working_duration:
                elapsed = time.monotonic() - self._working_since
                remaining = max(0, round(self._working_duration * self._separation_rate - elapsed))
            return {"task": task, "remaining_s": remaining}
        if song_path in self._waiting:
            return {"task": "waiting", "remaining_s": None}
        return None

    def _set_working(self, task: str | None, song_path: str) -> None:
        self._working = (task, song_path) if task else None
        self._working_since = time.monotonic()
        self._working_duration = get_media_duration(song_path) if task == "vocals" else None
        self._events.emit("preparation_update")

    def _process_queue(self) -> None:
        while True:
            self._prepare_next()

    def _prepare_next(self) -> None:
        """Take the next waiting song and run its due tasks."""
        song_path = self._pending.get()
        try:
            for task in self._due_tasks(song_path):
                self._attempted.add((task, song_path))
                self._set_working(task, song_path)
                if task == "lyrics":
                    self._add_lyrics(song_path)
                else:
                    self._add_instrumental(song_path)
        except Exception as e:
            # Deliberately broad: one bad song must not stop the worker for the rest.
            logging.error(f"Error preparing {song_path}: {e}")
        finally:
            self._waiting.discard(song_path)
            self._set_working(None, song_path)

    def _add_lyrics(self, song_path: str) -> None:
        base, ext = os.path.splitext(song_path)
        ass_path = base + ".ass"
        if ext.lower() not in _SUBTITLED_EXTENSIONS or os.path.exists(ass_path):
            return
        name = self._filename_from_path(song_path, remove_youtube_id=True)
        if has_on_screen_lyrics(name):
            return
        query = clean_search_query(name)
        results = search_lyrics(query)
        title = cjk_title(query) if not results else None
        if title:
            query, results = title, search_lyrics(title)
        lyrics = self._choose_lyrics(song_path, query, results)
        if lyrics is None:
            logging.info(f"No lyrics fit '{query}'")
            return
        lrc, offset, approximate = lyrics
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(lrc_to_ass(lrc, offset, approximate))
        self._events.emit("lyrics_ready", song_path)

    def _choose_lyrics(
        self, song_path: str, query: str, results: list[dict]
    ) -> tuple[str, float, bool] | None:
        """Pick lyrics, the offset that lines them up with this video, and whether
        that timing was worked out here rather than taken as timed.

        Lyrics of the video's own length are used as timed. Failing that, a
        separated lead vocal lets lyrics for another edit be moved onto the
        singing, which rescues a music video with its own intro, and lets lyrics
        nobody timed be timed by recognizing the words sung.
        """
        if not results:
            return None
        duration = get_media_duration(song_path)
        lrc = pick_by_duration(results, duration) if duration is not None else None
        if lrc is not None:
            return lrc, 0.0, False
        vocals = find_vocals(song_path)
        if vocals is None:
            return None
        activity = vocal_activity(vocals)
        aligned = best_alignment(results, activity, query) if activity else None
        if aligned is not None:
            return *aligned, True
        plain = pick_plain_lyrics(results, query)
        if plain is None or not is_transcription_available():
            return None
        words = transcribe_words(vocals)
        lrc = time_plain_lyrics(plain, words) if words else None
        # Timed at the singing itself; hand-timed lyrics, and so the display, lead it.
        return (lrc, -LRC_LEAD_S, True) if lrc is not None else None

    def _add_instrumental(self, song_path: str) -> None:
        # A zipped CDG is unpacked only for playback, so there is no audio file to split.
        if song_path.lower().endswith(".zip") or find_instrumental(song_path):
            return
        # A karaoke upload already has its vocals off; separating it spends minutes for nothing.
        if is_karaoke_title(self._filename_from_path(song_path, remove_youtube_id=True)):
            return
        if separate_vocals(song_path):
            if self._working_duration:
                elapsed = time.monotonic() - self._working_since
                self._separation_rate = elapsed / self._working_duration
            self._events.emit("instrumental_ready", song_path)
            # Lyrics tried before the vocals existed may now align where length alone did not.
            if ("lyrics", song_path) in self._attempted and self._preferences.get_or_default(
                "auto_lyrics"
            ):
                self._set_working("lyrics", song_path)
                self._add_lyrics(song_path)
