"""Efficient song list data structure for PiKaraoke."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path


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
        self._sort_key = sort_key or (lambda f: os.path.basename(f).lower())

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
        if song_path not in self._songs:
            self._songs.add(song_path)
            self._invalidate_cache()

    def remove(self, song_path: str) -> None:
        """Remove a song from the list. O(1) average."""
        try:
            self._songs.remove(song_path)
            self._invalidate_cache()
        except KeyError:
            logging.warning(f"Song not found in list: {song_path}")

    def discard(self, song_path: str) -> None:
        """Remove a song if present, no error if not. O(1) average."""
        if song_path in self._songs:
            self._songs.discard(song_path)
            self._invalidate_cache()

    def clear(self) -> None:
        """Remove all songs."""
        self._songs.clear()
        self._invalidate_cache()

    def update(self, songs: list[str]) -> None:
        """Replace all songs with a new list."""
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
        """Add a song only if it's a valid song file.

        Validates that the file exists and has a valid extension before adding.

        Args:
            song_path: Full path to the song file.

        Returns:
            True if the song was added, False if validation failed.
        """
        if not self.is_valid_song(song_path):
            return False

        self.add(song_path)
        logging.debug(f"Added song to list: {song_path}")
        return True

    def rename(self, old_path: str, new_path: str) -> bool:
        """Update a song's path after a file rename.

        Removes the old path and adds the new path with validation.

        Args:
            old_path: Current path of the song file.
            new_path: New path of the song file.

        Returns:
            True if successful, False if new path is invalid.
        """
        self.remove(old_path)
        return self.add_if_valid(new_path)

    def scan_directory(self, directory: str) -> int:
        """Scan a directory for song files and replace the current list.

        Args:
            directory: Path to directory to scan.

        Returns:
            Number of songs found.
        """
        logging.info(f"Scanning for songs in: {directory}")
        files_found = []
        for file in Path(directory).rglob("*.*"):
            file_path = file.as_posix()
            ext = os.path.splitext(file_path)[1].lower()
            if ext in self.VALID_EXTENSIONS and os.path.isfile(file_path):
                logging.debug(f"Found song: {file.name}")
                files_found.append(file_path)

        self.update(files_found)
        return len(files_found)

    def find_and_add(self, directory: str, pattern: str) -> str | None:
        """Find a file matching a glob pattern and add it to the list.

        Useful for adding newly downloaded files by YouTube ID pattern.

        Args:
            directory: Directory to search in.
            pattern: Glob pattern to match (e.g., "*---dQw4w9WgXcQ.*").

        Returns:
            Path to the found and added song, or None if not found.
        """
        for file in Path(directory).rglob(pattern):
            file_path = file.as_posix()
            if self.is_valid_song(file_path):
                if file_path not in self:
                    self.add(file_path)
                    logging.debug(f"Added song to list: {file_path}")
                return file_path

        logging.warning(f"No song found matching pattern: {pattern}")
        return None

    def __contains__(self, song_path: str) -> bool:
        """Check if a song is in the list. O(1) average."""
        return song_path in self._songs

    def __len__(self) -> int:
        """Return the number of songs. O(1)."""
        return len(self._songs)

    def __iter__(self) -> Iterator[str]:
        """Iterate over songs in sorted order."""
        return iter(self._ensure_sorted())

    def __getitem__(self, index: int | slice) -> str | list[str]:
        """Get song(s) by index or slice from sorted list."""
        return self._ensure_sorted()[index]

    def __bool__(self) -> bool:
        """Return True if there are any songs."""
        return bool(self._songs)

    def copy(self) -> list[str]:
        """Return a copy of the sorted song list."""
        return list(self._ensure_sorted())
