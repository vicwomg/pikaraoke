"""Song library management: scan, delete, rename, and display name operations."""

from __future__ import annotations

import contextlib
import logging
import os

from pikaraoke.lib.song_list import SongList


class SongManager:
    """Manages the song library and file operations.

    Owns the SongList instance and provides all song discovery,
    delete, rename, and display name operations.
    """

    def __init__(self, download_path: str) -> None:
        self.download_path = download_path
        self.songs = SongList()

    def refresh_songs(self) -> None:
        """Scan the download directory and update the song list."""
        self.songs.scan_directory(self.download_path)

    @staticmethod
    def filename_from_path(file_path: str, remove_youtube_id: bool = True) -> str:
        """Extract a clean display name from a file path.

        Args:
            file_path: Full path to the file.
            remove_youtube_id: Strip YouTube ID suffix if present.

        Returns:
            Clean filename without extension or YouTube ID.
        """
        name = os.path.splitext(os.path.basename(file_path))[0]
        if remove_youtube_id:
            name = name.split("---")[0]
        return name

    def _get_companion_files(self, song_path: str) -> list[str]:
        """Return paths to companion files (.cdg, .ass) that exist alongside a song."""
        base = os.path.splitext(song_path)[0]
        companions = []
        for ext in (".cdg", ".ass"):
            path = base + ext
            if os.path.exists(path):
                companions.append(path)
        return companions

    def delete(self, song_path: str) -> None:
        """Delete a song file and its associated companion files if present."""
        logging.info(f"Deleting song: {song_path}")
        companions = self._get_companion_files(song_path)
        with contextlib.suppress(FileNotFoundError):
            os.remove(song_path)
        for companion in companions:
            with contextlib.suppress(FileNotFoundError):
                os.remove(companion)
        self.songs.remove(song_path)

    def rename(self, song_path: str, new_name: str) -> None:
        """Rename a song file and its associated companion files if present.

        Args:
            song_path: Full path to the current song file.
            new_name: New filename (without extension).
        """
        logging.info(f"Renaming song: '{song_path}' to: {new_name}")
        companions = self._get_companion_files(song_path)
        _, ext = os.path.splitext(song_path)
        new_path = os.path.join(self.download_path, new_name + ext)
        os.rename(song_path, new_path)
        for companion in companions:
            companion_ext = os.path.splitext(companion)[1]
            os.rename(companion, os.path.join(self.download_path, new_name + companion_ext))
        self.songs.rename(song_path, new_path)
