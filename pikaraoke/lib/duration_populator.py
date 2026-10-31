"""Fills songs.duration from the moments a song is played and queued."""

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
        # (file_path, duration) of the song on screen; the playback path already
        # probed it, so Rung 1 persists that figure at no probe cost.
        self._get_now_playing = get_now_playing
        events.on("playback_started", self._on_playback_started)
        events.on("song_queued", self._on_song_queued)

    def _on_playback_started(self) -> None:
        file_path, duration = self._get_now_playing()
        if file_path and duration is not None:
            self._db.set_duration(file_path, duration)

    def _on_song_queued(self, song_path: str) -> None:
        # Skip the probe, not just the write, for a row already filled -- a
        # download fills it at register before it reaches the queue.
        if self._db.get_duration(song_path) is not None:
            return
        duration = get_media_duration(song_path)
        if duration is not None:
            self._db.set_duration(song_path, duration)
