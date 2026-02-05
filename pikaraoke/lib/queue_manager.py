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


class QueueManager:
    """Manages the song queue: enqueueing, editing, reordering, and fair queue logic."""

    def __init__(
        self,
        preferences: PreferenceManager,
        events: EventSystem,
        get_now_playing_user: Callable[[], str | None] | None = None,
        filename_from_path: Callable[[str, bool], str] | None = None,
        get_available_songs: Callable[[], Any] | None = None,
    ) -> None:
        self.queue: list[dict[str, Any]] = []
        self._preferences = preferences
        self._events = events
        self._get_now_playing_user = get_now_playing_user
        self._filename_from_path = filename_from_path
        self._get_available_songs = get_available_songs

    def is_song_in_queue(self, song_path: str) -> bool:
        """Check if a song is already in the queue."""
        return any(item["file"] == song_path for item in self.queue)

    def is_user_limited(self, user: str) -> bool:
        """Check if a user has reached their queue limit."""
        limit = self._preferences.get_or_default("limit_user_songs_by")
        if limit == 0 or user in ("Pikaraoke", "Randomizer"):
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

    def _calculate_fair_queue_position(self, user: str) -> int:
        """Calculate insertion position using Nagle Fair Queuing.

        Users take turns in rounds: a user's Nth song is placed after all
        other users' Nth songs (or at queue end).
        """
        # Count how many songs this user already has in queue
        user_song_count = sum(1 for item in self.queue if item["user"] == user)

        # Find position after the last song in "round N" where N = user_song_count
        # Round 0 = first song from each user, Round 1 = second song, etc.
        target_round = user_song_count
        songs_seen_per_user: dict[str, int] = {}

        for idx, item in enumerate(self.queue):
            queue_user = item["user"]
            songs_seen_per_user[queue_user] = songs_seen_per_user.get(queue_user, 0) + 1
            # This song is in round (count - 1) for its user
            song_round = songs_seen_per_user[queue_user] - 1
            if song_round == target_round:
                # Found a song in the target round, insert after it
                # Keep scanning to find the LAST song in this round
                pass
            elif song_round > target_round:
                # We've moved past target round, insert here
                return idx

        # All songs are in rounds <= target_round, append to end
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

    def queue_edit(self, song_name: str, action: str) -> bool:
        """Move or remove a song in the queue. Action: 'up', 'down', or 'delete'."""
        song = None
        index = 0
        for idx, item in enumerate(self.queue):
            if song_name in item["file"]:
                song = item
                index = idx
                break
        if song is None:
            logging.error("Song not found in queue: " + song_name)
            return False

        rc = False
        if action == "up":
            if index < 1:
                logging.warning("Song is up next, can't bump up in queue: " + song["file"])
            else:
                logging.info("Bumping song up in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index - 1, song)
                rc = True
        elif action == "down":
            if index == len(self.queue) - 1:
                logging.warning("Song is already last, can't bump down in queue: " + song["file"])
            else:
                logging.info("Bumping song down in queue: " + song["file"])
                del self.queue[index]
                self.queue.insert(index + 1, song)
                rc = True
        elif action == "delete":
            logging.info("Deleting song from queue: " + song["file"])
            del self.queue[index]
            rc = True
        else:
            logging.error("Unrecognized direction: " + action)
        if rc:
            self._events.emit("queue_update")
            self._events.emit("now_playing_update")
        return rc
