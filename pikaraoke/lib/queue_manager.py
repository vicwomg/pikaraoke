"""Queue management for PiKaraoke.

Handles song queue operations including enqueueing, editing, clearing,
and fair queue algorithm.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

from flask_babel import _

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager

# Names the system queues under when no singer asked for the song, so they are
# exempt from per-singer limits and hold no turn in the fair queue.
_PLACEHOLDER_SINGERS = frozenset({"pikaraoke", "randomizer"})


class QueueManager:
    """Manages the song queue: enqueueing, editing, reordering, and fair queue logic."""

    def __init__(
        self,
        preferences: PreferenceManager,
        events: EventSystem,
        get_now_playing_user: Callable[[], str | None] | None = None,
        filename_from_path: Callable[[str, bool], str] | None = None,
        get_available_songs: Callable[[], Any] | None = None,
        get_turns_taken: Callable[[], dict[str, int]] | None = None,
    ) -> None:
        self.queue: list[dict[str, Any]] = []
        self._preferences = preferences
        self._events = events
        self._get_now_playing_user = get_now_playing_user
        self._filename_from_path = filename_from_path
        self._get_available_songs = get_available_songs
        self._get_turns_taken = get_turns_taken

    def is_song_in_queue(self, song_path: str) -> bool:
        """Check if a song is already in the queue."""
        return any(item["file"] == song_path for item in self.queue)

    def is_user_limited(self, user: str) -> bool:
        """Check if a user has reached their queue limit."""
        limit = self._preferences.get_or_default("limit_user_songs_by")
        if limit == 0 or user.lower() in _PLACEHOLDER_SINGERS:
            return False

        now_playing_user = self._get_now_playing_user() if self._get_now_playing_user else None
        count = sum(1 for item in self.queue if item["user"] == user) + (
            1 if now_playing_user == user else 0
        )
        return count >= int(limit)

    def _resolve_title(self, song_path: str) -> str:
        """Get a display title from a song path."""
        if self._filename_from_path:
            return self._filename_from_path(song_path, True)
        return song_path

    def _find_song_index(self, song_path: str) -> int:
        """Find a song's index in the queue by exact path match. Returns -1 if not found."""
        for idx, item in enumerate(self.queue):
            if song_path == item["file"]:
                return idx
        return -1

    def _calculate_fair_queue_position(self, user: str) -> int:
        """Return where a new song from `user` belongs under fair queuing.

        Singers take turns in rounds, ranked by the turns they have had tonight
        rather than by what is left in the queue, so the song on screen and the
        ones already sung both count against them. Catching up is capped at the
        round that just played, or a singer arriving at midnight would claim
        every round the room got through before they walked in.
        """
        history = self._get_turns_taken() if self._get_turns_taken else {}
        turns_taken = {
            singer: turns for singer, turns in history.items() if singer not in _PLACEHOLDER_SINGERS
        }
        now_playing = self._get_now_playing_user() if self._get_now_playing_user else None
        if now_playing and now_playing.lower() not in _PLACEHOLDER_SINGERS:
            # History cannot count this one until it ends.
            singer = now_playing.lower()
            turns_taken[singer] = turns_taken.get(singer, 0) + 1

        current_round = max(max(turns_taken.values(), default=0) - 1, 0)
        next_round = {singer: max(turns, current_round) for singer, turns in turns_taken.items()}

        rounds: list[int] = []
        for item in self.queue:
            singer = item["user"].lower()
            round_number = next_round.get(singer, current_round)
            next_round[singer] = round_number + 1
            rounds.append(round_number)

        target = next_round.get(user.lower(), current_round)
        for index, round_number in enumerate(rounds):
            if round_number > target:
                return index
        return len(self.queue)

    def enqueue(
        self,
        song_path: str,
        user: str = "Pikaraoke",
        semitones: int = 0,
        add_to_front: bool = False,
        log_action: bool = True,
    ) -> list[bool | str]:
        """Add a song to the queue. Returns [success, message]."""
        title = self._resolve_title(song_path)

        if self.is_song_in_queue(song_path):
            logging.warning("Song is already in queue, will not add: " + song_path)
            return [
                False,
                _("Song is already in the queue: %s") % title,
            ]

        if self.is_user_limited(user):
            limit = self._preferences.get_or_default("limit_user_songs_by")
            logging.debug("User limited by: " + str(limit))
            return [
                False,
                _("You reached the limit of %s song(s) from an user in queue!") % (str(limit)),
            ]

        queue_item = {
            "user": user,
            "file": song_path,
            "title": title,
            "semitones": semitones,
        }
        if add_to_front:
            # MSG: Message shown after the song is added to the top of the queue
            self._events.emit(
                "notification",
                _("%s added to top of queue: %s") % (user, queue_item["title"]),
                "info",
            )
            self.queue.insert(0, queue_item)
        else:
            if log_action:
                # MSG: Message shown after the song is added to the queue
                self._events.emit(
                    "notification",
                    _("%s added to the queue: %s") % (user, queue_item["title"]),
                    "info",
                )
            if self._preferences.get_or_default("enable_fair_queue"):
                insert_pos = self._calculate_fair_queue_position(user)
                self.queue.insert(insert_pos, queue_item)
            else:
                self.queue.append(queue_item)
        self._events.emit("queue_update")
        self._events.emit("now_playing_update")
        return [
            True,
            _("Song added to the queue: %s") % title,
        ]

    def update_song_path(self, old_path: str, new_path: str, new_title: str) -> bool:
        """Point a queue entry at a renamed file, keeping position, user and key.

        Returns False if the song was not queued. Emits nothing: the caller
        renames the library too and announces both changes at once.
        """
        index = self._find_song_index(old_path)
        if index == -1:
            return False
        if self.is_song_in_queue(new_path):
            logging.error("Renamed song is already in the queue: " + new_path)
            return False
        self.queue[index]["file"] = new_path
        self.queue[index]["title"] = new_title
        return True

    def queue_add_random(self, amount: int) -> bool:
        """Add random songs to the queue. Returns False if ran out of songs."""
        logging.info("Adding %d random songs to queue" % amount)

        if not self._get_available_songs:
            logging.error("No available songs callback provided!")
            return False

        available_songs = self._get_available_songs()

        if not available_songs:
            logging.warning("No available songs!")
            return False

        # Get songs not already in queue
        queued_paths = {item["file"] for item in self.queue}
        eligible_songs = [s for s in available_songs if s not in queued_paths]

        if not eligible_songs:
            logging.warning("All songs are already in queue!")
            return False

        # Sample up to 'amount' songs (or all eligible if fewer available)
        sample_size = min(amount, len(eligible_songs))
        selected = random.sample(eligible_songs, sample_size)

        for song in selected:
            self.enqueue(song, "Randomizer")

        if sample_size < amount:
            logging.warning("Ran out of songs! Only added %d" % sample_size)
            return False

        return True

    def queue_clear(self) -> None:
        """Clear all songs from the queue and skip current song."""
        # MSG: Message shown after the queue is cleared
        self._events.emit("notification", _("Clear queue"), "danger")
        self.queue = []
        self._events.emit("queue_update")
        self._events.emit("now_playing_update")
        self._events.emit("skip_requested")

    def reorder(self, old_index: int, new_index: int) -> bool:
        """Move a song from old_index to new_index. Returns False if indices are invalid."""
        if not (0 <= old_index < len(self.queue) and 0 <= new_index < len(self.queue)):
            logging.error(
                f"Invalid reorder indices: old={old_index}, new={new_index}, queue_len={len(self.queue)}"
            )
            return False

        if old_index == new_index:
            return True

        item = self.queue.pop(old_index)
        self.queue.insert(new_index, item)
        logging.info(f"Reordered queue: moved index {old_index} to {new_index}")
        self._events.emit("queue_update")
        self._events.emit("now_playing_update")
        return True

    def move_to_top(self, song_path: str) -> bool:
        """Move a song to the top of the queue (index 0). Returns False if not found or already at top."""
        index = self._find_song_index(song_path)
        if index < 1:  # Not found (-1) or already at top (0)
            if index == -1:
                logging.error("Song not found in queue: " + song_path)
            else:
                logging.warning("Song is already at top of queue: " + song_path)
            return False
        return self.reorder(index, 0)

    def move_to_bottom(self, song_path: str) -> bool:
        """Move a song to the bottom of the queue. Returns False if not found or already at bottom."""
        index = self._find_song_index(song_path)
        if index < 0:
            logging.error("Song not found in queue: " + song_path)
            return False
        if index >= len(self.queue) - 1:
            logging.warning("Song is already at bottom of queue: " + song_path)
            return False
        return self.reorder(index, len(self.queue) - 1)

    def pop_next(self) -> dict[str, Any] | None:
        """Remove and return the next song from the queue.

        Does not emit queue_update to avoid UI flicker during song transitions.
        The playback system emits now_playing events which trigger queue UI updates.

        Returns None if queue is empty.
        """
        if not self.queue:
            return None

        song = self.queue.pop(0)
        logging.info(f"Popped song from queue: {song['title']}")
        return song

    def queue_edit(self, song_path: str, action: str) -> bool:
        """Move or remove a song in the queue. Action: 'up', 'down', or 'delete'."""
        index = self._find_song_index(song_path)
        if index == -1:
            logging.error("Song not found in queue: " + song_path)
            return False

        if action == "up":
            if index < 1:
                logging.warning("Song is up next, can't bump up in queue: " + song_path)
                return False
            return self.reorder(index, index - 1)

        if action == "down":
            if index == len(self.queue) - 1:
                logging.warning("Song is already last, can't bump down in queue: " + song_path)
                return False
            return self.reorder(index, index + 1)

        if action == "delete":
            logging.info("Deleting song from queue: " + song_path)
            del self.queue[index]
            self._events.emit("queue_update")
            self._events.emit("now_playing_update")
            return True

        logging.error("Unrecognized action: " + action)
        return False
