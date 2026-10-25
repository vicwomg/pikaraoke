"""Background preparation of queued songs: an instrumental track and synced lyrics."""

import logging
import os
from collections.abc import Callable
from queue import Queue

from gevent import Greenlet, spawn

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ffmpeg import get_media_duration
from pikaraoke.lib.lyrics import (
    best_alignment,
    lrc_to_ass,
    pick_by_duration,
    search_synced_lyrics,
    vocal_activity,
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

    def start(self) -> None:
        """Start the worker greenlet."""
        self._worker = spawn(self._process_queue)

    def _due_tasks(self, song_path: str) -> list[str]:
        tasks = []
        # Vocals first: the separated lead vocal is what lyrics are aligned against.
        if self._preferences.get_or_default("vocal_removal") and is_vocal_removal_available():
            tasks.append("vocals")
        if self._preferences.get_or_default("auto_lyrics"):
            tasks.append("lyrics")
        return [t for t in tasks if (t, song_path) not in self._attempted]

    def prepare(self, song_path: str) -> None:
        """Queue a song for whatever preparation is enabled and not yet tried."""
        if song_path in self._waiting or not self._due_tasks(song_path):
            return
        self._waiting.add(song_path)
        self._pending.put(song_path)

    def _process_queue(self) -> None:
        while True:
            self._prepare_next()

    def _prepare_next(self) -> None:
        """Take the next waiting song and run its due tasks."""
        song_path = self._pending.get()
        try:
            for task in self._due_tasks(song_path):
                self._attempted.add((task, song_path))
                if task == "lyrics":
                    self._add_lyrics(song_path)
                else:
                    self._add_instrumental(song_path)
        except Exception as e:
            # Deliberately broad: one bad song must not stop the worker for the rest.
            logging.error(f"Error preparing {song_path}: {e}")
        finally:
            self._waiting.discard(song_path)

    def _add_lyrics(self, song_path: str) -> None:
        base, ext = os.path.splitext(song_path)
        ass_path = base + ".ass"
        if ext.lower() not in _SUBTITLED_EXTENSIONS or os.path.exists(ass_path):
            return
        name = self._filename_from_path(song_path, remove_youtube_id=True)
        if has_on_screen_lyrics(name):
            return
        query = clean_search_query(name)
        results = search_synced_lyrics(query)
        lyrics = self._choose_lyrics(song_path, query, results)
        if lyrics is None:
            logging.info(f"No synced lyrics fit '{query}'")
            return
        lrc, offset = lyrics
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(lrc_to_ass(lrc, offset))
        self._events.emit("lyrics_ready", song_path)

    def _choose_lyrics(
        self, song_path: str, query: str, results: list[dict]
    ) -> tuple[str, float] | None:
        """Pick lyrics and the offset that lines them up with this video.

        Lyrics of the video's own length are used as timed. Failing that, a
        separated lead vocal lets lyrics for another edit be moved onto the
        singing, which rescues a music video with its own intro.
        """
        if not results:
            return None
        duration = get_media_duration(song_path)
        lrc = pick_by_duration(results, duration) if duration is not None else None
        if lrc is not None:
            return lrc, 0.0
        vocals = find_vocals(song_path)
        activity = vocal_activity(vocals) if vocals else None
        return best_alignment(results, activity, query) if activity else None

    def _add_instrumental(self, song_path: str) -> None:
        # A zipped CDG is unpacked only for playback, so there is no audio file to split.
        if song_path.lower().endswith(".zip") or find_instrumental(song_path):
            return
        # A karaoke upload already has its vocals off; separating it spends minutes for nothing.
        if is_karaoke_title(self._filename_from_path(song_path, remove_youtube_id=True)):
            return
        if separate_vocals(song_path):
            self._events.emit("instrumental_ready", song_path)
            # Lyrics tried before the vocals existed may now align where length alone did not.
            if ("lyrics", song_path) in self._attempted and self._preferences.get_or_default(
                "auto_lyrics"
            ):
                self._add_lyrics(song_path)
