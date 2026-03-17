"""Efficient song list data structure for PiKaraoke."""

import logging
import os
import threading
import unicodedata
from collections.abc import Iterator


class SongList:
    """A hybrid data structure for efficient song list management.

    Uses a set for O(1) membership checks and add/remove operations,
    with a lazily-generated sorted list cache for iteration and display.

    This is more efficient than a plain list when:
    - Membership checks are frequent (e.g., checking if song exists)
    - Add/remove operations happen incrementally
    - The sorted list is accessed less frequently than modifications

    Performance characteristics:
    - Membership check (`in`): O(1) average
    - Add: O(1) average
    - Remove: O(1) average
    - Iteration/indexing: O(n log n) on first access after modification, O(1) thereafter
    - Length: O(1)
    """

    # Supported song file extensions
    VALID_EXTENSIONS = {".mp4", ".mp3", ".zip", ".mkv", ".avi", ".webm", ".mov"}

    def __init__(self, sort_key=None):
        """Initialize an empty SongList.

        Args:
            sort_key: Optional function to extract sort key from items.
                      Defaults to lowercase basename.
        """
        self._songs: set[str] = set()
        self._sorted_cache: list[str] | None = None
        self._sort_key = sort_key or (lambda f: self._normalize_sort_key(f))
        self._lock = threading.Lock()

    @staticmethod
    def _normalize_sort_key(file_path: str) -> str:
        """Generate a sort key that treats accented characters like their base letter.

        Uses Unicode NFD normalization to decompose characters (e.g. e + accent),
        then strips combining marks so "Celine" sorts next to "ce" not after "z".
        """
        name = os.path.basename(file_path).lower()
        decomposed = unicodedata.normalize("NFD", name)
        return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")

    def _invalidate_cache(self) -> None:
        """Mark the sorted cache as stale."""
        self._sorted_cache = None

    def _ensure_sorted(self) -> list[str]:
        """Ensure the sorted cache is up to date and return it."""
        if self._sorted_cache is None:
            self._sorted_cache = sorted(self._songs, key=self._sort_key)
        return self._sorted_cache

    def add(self, song_path: str) -> None:
        """Add a song to the list. O(1) average."""
        with self._lock:
            if song_path not in self._songs:
                self._songs.add(song_path)
                self._invalidate_cache()

    def remove(self, song_path: str) -> None:
        """Remove a song from the list. O(1) average."""
        with self._lock:
            try:
                self._songs.remove(song_path)
                self._invalidate_cache()
            except KeyError:
                logging.warning(f"Song not found in list: {song_path}")

    def update(self, songs: list[str]) -> None:
        """Replace all songs with a new list."""
        with self._lock:
            self._songs = set(songs)
            self._invalidate_cache()

    def is_valid_song(self, file_path: str) -> bool:
        """Check if a file path is a valid song file.

        Args:
            file_path: Path to check.

        Returns:
            True if the file exists and has a valid extension.
        """
        ext = os.path.splitext(file_path)[1].lower()
        return ext in self.VALID_EXTENSIONS and os.path.isfile(file_path)

    def add_if_valid(self, song_path: str) -> bool:
        """Add a song only if it's a valid song file and exists.

        Validates that the file exists and has a valid extension before adding.

        Args:
            song_path: Full path to the song file.

        Returns:
            True if the song was added, False if validation failed.
        """

        if os.path.exists(song_path) and self.is_valid_song(song_path):
            self.add(song_path)
            logging.debug(f"Added song to list: {song_path}")
            return True

        logging.debug(f"Song not added to list because it doesn't exist or is invalid: {song_path}")
        return False

    def rename(self, old_path: str, new_path: str) -> bool:
        """Update a song's path after a file rename.

        Removes the old path and adds the new path with validation.

        Args:
            old_path: Current path of the song file.
            new_path: New path of the song file.

        Returns:
            True if successful, False if new path is invalid.
        """
        # remove and add_if_valid each acquire the lock independently
        self.remove(old_path)
        return self.add_if_valid(new_path)

    def find_by_id(self, directory: str, video_id: str) -> str | None:
        """Efficiently find a song by its YouTube ID in a directory (non-recursive).

        Args:
            directory: The directory to search in.
            video_id: The YouTube ID to match (searches for "---ID.").

        Returns:
            The full path to the found song, or None if not found.
        """
        id_pattern = f"---{video_id}."
        try:
            with os.scandir(directory) as it:
                for entry in it:
                    if entry.is_file() and id_pattern in entry.name:
                        file_path = entry.path
                        if self.is_valid_song(file_path):
                            return file_path
        except OSError as e:
            logging.error(f"Error searching for song by ID {video_id} in {directory}: {e}")
        return None

    def __contains__(self, song_path: str) -> bool:
        """Check if a song is in the list. O(1) average."""
        with self._lock:
            return song_path in self._songs

    def __len__(self) -> int:
        """Return the number of songs. O(1)."""
        with self._lock:
            return len(self._songs)

    def __iter__(self) -> Iterator[str]:
        """Iterate over songs in sorted order. Returns iterator over a snapshot."""
        with self._lock:
            return iter(list(self._ensure_sorted()))

    def __getitem__(self, index: int | slice) -> str | list[str]:
        """Get song(s) by index or slice from sorted list."""
        with self._lock:
            return self._ensure_sorted()[index]

    def __bool__(self) -> bool:
        """Return True if there are any songs."""
        with self._lock:
            return bool(self._songs)
