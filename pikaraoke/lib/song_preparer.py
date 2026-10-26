"""Background preparation of queued songs: an instrumental track and synced lyrics."""

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Queue

from gevent import Greenlet, spawn

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ffmpeg import get_media_duration
from pikaraoke.lib.file_resolver import find_ass_file
from pikaraoke.lib.lyrics import (
    LRC_LEAD_S,
    best_alignment,
    by_artist_in,
    cjk_title,
    lrc_to_ass,
    pick_by_duration,
    pick_plain_lyrics,
    search_lyrics,
    vocal_activity,
)
from pikaraoke.lib.lyrics_timing import (
    is_transcription_available,
    lead_vocal_left_in,
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
    has_all_vocals_removed,
    is_vocal_removal_available,
    separate_vocals,
)

# Only plain video files carry a subtitle; a CDG draws its own lyrics.
_SUBTITLED_EXTENSIONS = (".mp4", ".webm", ".mkv", ".mov", ".avi")


@dataclass
class _Lane:
    """One line of songs waiting for one kind of task, worked one song at a time."""

    tasks: tuple[str, ...]
    pending: Queue = field(default_factory=Queue)
    waiting: set[str] = field(default_factory=set)
    # The task under way, as (task, song).
    working: tuple[str, str] | None = None


class SongPreparer:
    """Readies songs as they are queued, ahead of their turn.

    Separations run one at a time: one takes a CPU-minute or more, and two at
    once would only finish both later. Lyrics take seconds to look up, so they
    have their own lane rather than waiting behind separations. Each task is
    attempted once per song per run, so a lyrics miss is not looked up again
    every time the queue changes.
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
        self._separation = _Lane(("vocals", "all_vocals"))
        self._lyrics = _Lane(("lyrics",))
        self._attempted: set[tuple[str, str]] = set()
        # Songs someone asked to have every voice taken out of, backing vocals included.
        self._all_vocals_requested: set[str] = set()
        # Whether a song's instrumental has every voice out, read from the file once.
        self._all_vocals_removed: dict[str, bool] = {}
        self._workers: list[Greenlet] = []
        # When the separation under way started, and the length of its song.
        self._working_since = 0.0
        self._working_duration: float | None = None
        # Seconds a separation takes per second of song, from the last one; None until then.
        self._separation_rate: float | None = None

    def start(self) -> None:
        """Start a worker greenlet for each lane."""
        self._workers = [spawn(self._process_lane, lane) for lane in self._lanes]

    @property
    def _lanes(self) -> tuple[_Lane, _Lane]:
        return self._separation, self._lyrics

    def _due_tasks(self, song_path: str, lane: _Lane) -> list[str]:
        tasks = []
        # A song already having the result is skipped, so it is not shown waiting.
        if (
            self._preferences.get_or_default("vocal_removal")
            and is_vocal_removal_available()
            and not find_instrumental(song_path)
        ):
            tasks.append("vocals")
        if self._preferences.get_or_default("auto_lyrics") and not find_ass_file(song_path):
            tasks.append("lyrics")
        if song_path in self._all_vocals_requested:
            tasks.append("all_vocals")
        return [t for t in tasks if t in lane.tasks and (t, song_path) not in self._attempted]

    def prepare(self, song_path: str) -> None:
        """Queue a song for whatever preparation is enabled and not yet tried."""
        for lane in self._lanes:
            if song_path not in lane.waiting and self._due_tasks(song_path, lane):
                lane.waiting.add(song_path)
                lane.pending.put(song_path)

    def remove_all_vocals(self, song_path: str) -> bool:
        """Separate a song again with every voice taken out, backing vocals included.

        For a song whose lead the karaoke separation left in. False when it is
        already done or under way.
        """
        if not self.all_vocals_removable(song_path):
            return False
        self._all_vocals_requested.add(song_path)
        self.prepare(song_path)
        return True

    def all_vocals_removable(self, song_path: str) -> bool:
        """Whether a song's backing vocals are still in its instrumental, and not being removed."""
        if song_path in self._all_vocals_requested:
            return False
        if song_path not in self._all_vocals_removed:
            self._all_vocals_removed[song_path] = has_all_vocals_removed(song_path)
        return not self._all_vocals_removed[song_path]

    def progress(self, song_path: str) -> dict[str, str | int | None] | None:
        """What is being done for a song, for showing singers, or None if nothing is due.

        `task` is "vocals" or "lyrics" while under way, or "waiting" while songs ahead
        of it are prepared. A separation also gives `remaining_s` once one has been timed.
        A separation is the one shown when both are under way: it is the long one.
        """
        if self._separation.working and self._separation.working[1] == song_path:
            if self._separation.working[0] == "lyrics":
                return {"task": "lyrics", "remaining_s": None}
            remaining = None
            if self._separation_rate and self._working_duration:
                elapsed = time.monotonic() - self._working_since
                remaining = max(0, round(self._working_duration * self._separation_rate - elapsed))
            # Singers see one "removing vocals", however many passes it takes.
            return {"task": "vocals", "remaining_s": remaining}
        if self._lyrics.working and self._lyrics.working[1] == song_path:
            return {"task": "lyrics", "remaining_s": None}
        if any(song_path in lane.waiting for lane in self._lanes):
            return {"task": "waiting", "remaining_s": None}
        return None

    def _set_working(self, lane: _Lane, task: str | None, song_path: str) -> None:
        lane.working = (task, song_path) if task else None
        if lane is self._separation:
            self._working_since = time.monotonic()
            separating = task in self._separation.tasks
            self._working_duration = get_media_duration(song_path) if separating else None
        self._events.emit("preparation_update")

    def _process_lane(self, lane: _Lane) -> None:
        while True:
            self._prepare_next(lane)

    def _prepare_next(self, lane: _Lane) -> None:
        """Take the lane's next waiting song and run its due tasks."""
        song_path = lane.pending.get()
        try:
            for task in self._due_tasks(song_path, lane):
                self._attempted.add((task, song_path))
                self._set_working(lane, task, song_path)
                if task == "lyrics":
                    self._add_lyrics(song_path)
                elif task == "all_vocals":
                    self._remove_all_vocals(song_path)
                else:
                    self._add_instrumental(song_path)
        except Exception as e:
            # Deliberately broad: one bad song must not stop the worker for the rest.
            logging.error(f"Error preparing {song_path}: {e}")
        finally:
            lane.waiting.discard(song_path)
            self._set_working(lane, None, song_path)

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
            query, results = title, by_artist_in(search_lyrics(title), query)
        lyrics = self._choose_lyrics(song_path, query, results)
        if lyrics is None:
            logging.info(f"No lyrics fit '{query}'")
            return
        lrc, offset, approximate = lyrics
        try:
            # Exclusive: lyrics written meanwhile, by the other lane or by hand, are kept.
            with open(ass_path, "x", encoding="utf-8") as f:
                f.write(lrc_to_ass(lrc, offset, approximate))
        except FileExistsError:
            return
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
        keep_backing = self._preferences.get_or_default("keep_backing_vocals")
        if separate_vocals(song_path, keep_backing=keep_backing):
            self._all_vocals_removed.pop(song_path, None)
            if self._working_duration:
                elapsed = time.monotonic() - self._working_since
                self._separation_rate = elapsed / self._working_duration
            if keep_backing and self._lead_left_in(song_path):
                logging.info(f"Lead vocal left in, taking out all vocals: {song_path}")
                separate_vocals(song_path, keep_backing=False)
            self._events.emit("instrumental_ready", song_path)
            # Lyrics tried before the vocals existed may now align where length alone did not.
            if ("lyrics", song_path) in self._attempted and self._preferences.get_or_default(
                "auto_lyrics"
            ):
                self._set_working(self._separation, "lyrics", song_path)
                self._add_lyrics(song_path)

    def _lead_left_in(self, song_path: str) -> bool:
        """Whether the karaoke track still has the lead singing in it.

        A lead sung in stacked layers, common in pop choruses, can be taken for
        backing vocals and kept, and recognition then hears its lyrics there.
        """
        if not is_transcription_available():
            return False
        heard = transcribe_words(find_instrumental(song_path), in_music=True)
        return heard is not None and lead_vocal_left_in(heard)

    def _remove_all_vocals(self, song_path: str) -> None:
        if separate_vocals(song_path, keep_backing=False):
            self._events.emit("all_vocals_removed", song_path)
