"""Fills songs.duration from the moments a song is played and queued."""

import os
from typing import Callable

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.ffmpeg import get_media_duration
from pikaraoke.lib.karaoke_database import KaraokeDatabase


class DurationPopulator:
    """Writes songs.duration as songs are played and queued.

    Both sources funnel through KaraokeDatabase.set_duration, whose NULL guard
    keeps a song caught at more than one moment from being rewritten. Subscribes
    rather than being called, so playback and the queue stay unaware of the
    songs table -- the way PlayHistoryManager subscribes to song_ended.
    """

    def __init__(
        self,
        db: KaraokeDatabase,
        events: EventSystem,
        get_now_playing: Callable[[], tuple[str | None, int | None]],
    ) -> None:
        self._db = db
        # Returns (file_path, duration) of the song on screen. The playback path
        # already probed it, so persisting it here costs no probe.
        self._get_now_playing = get_now_playing
        events.on("playback_started", self._on_playback_started)
        events.on("song_queued", self._on_song_queued)

    def _on_playback_started(self) -> None:
        file_path, duration = self._get_now_playing()
        if file_path and duration is not None:
            self._db.set_duration(file_path, duration)

    def _on_song_queued(self, song_path: str) -> None:
        # A zip's audio is inside the archive, so probing it needs a full
        # extraction. Too costly here; it is filled at play, where it is
        # extracted anyway.
        if os.path.splitext(song_path)[1].casefold() == ".zip":
            return
        # Skip the probe, not just the write, when the row is already filled.
        if self._db.get_duration(song_path) is not None:
            return
        duration = get_media_duration(song_path)
        if duration is not None:
            self._db.set_duration(song_path, duration)
