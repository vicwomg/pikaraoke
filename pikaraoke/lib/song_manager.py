"""Song library management: scan, delete, rename, and display name operations."""

import contextlib
import logging
import os
import re
from collections.abc import Callable

from pikaraoke.lib.get_platform import is_windows
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.library_scanner import build_song_record
from pikaraoke.lib.metadata_parser import regex_tidy, youtube_id_suffix
from pikaraoke.lib.song_list import SongList

# Characters illegal in Windows filenames
_WINDOWS_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize_filename(name: str) -> str:
    """Remove characters that are illegal in filenames on the current platform."""
    if is_windows():
        name = _WINDOWS_ILLEGAL_CHARS.sub("-", name)
    return name.strip()


class SongManager:
    """Manages the song library and file operations.

    Owns the SongList instance and provides all song discovery,
    delete, rename, and display name operations.
    """

    def __init__(
        self,
        download_path: str,
        db: KaraokeDatabase,
        get_title_tidy: Callable[[], bool] | None = None,
    ) -> None:
        self.download_path = download_path
        self.songs = SongList()
        self._db = db
        self._get_title_tidy = get_title_tidy

    @staticmethod
    def filename_from_path(
        file_path: str, remove_youtube_id: bool = True, tidy: bool = True
    ) -> str:
        """Extract a display name from a file path.

        Args:
            file_path: Full path to the file.
            remove_youtube_id: Strip YouTube ID suffix if present.
            tidy: Apply regex_tidy() to strip noise words and normalize.

        Returns:
            Filename without extension, optionally cleaned.
        """
        name = os.path.splitext(os.path.basename(file_path))[0]
        suffix = youtube_id_suffix(file_path)
        if remove_youtube_id and suffix:
            name = name[: -len(suffix)]
        if tidy and suffix and remove_youtube_id:
            tidied = regex_tidy(name)
            if tidied:
                name = tidied
        return name

    def display_name_from_path(self, file_path: str, remove_youtube_id: bool = True) -> str:
        """Extract display name from path, respecting the enable_title_tidy preference."""
        tidy = self._get_title_tidy() if self._get_title_tidy else True
        return self.filename_from_path(file_path, remove_youtube_id=remove_youtube_id, tidy=tidy)

    def _get_companion_files(self, song_path: str) -> list[str]:
        """Return paths to companion files (.cdg, .ass, .m4a) alongside a song.

        .m4a siblings are produced by the parallel-download pipeline (vocal
        removal): the main .mp4 is silent and the audio lives next to it.
        Deleting or renaming a song has to move them together.
        """
        dirpath = os.path.dirname(song_path)
        base = os.path.splitext(os.path.basename(song_path))[0]
        try:
            files = os.listdir(dirpath)
        except OSError:
            return []
        base_lower = base.lower()
        companions = []
        for f in files:
            f_base, f_ext = os.path.splitext(f)
            if f_base.lower() == base_lower and f_ext.lower() in (".cdg", ".ass", ".m4a"):
                companions.append(os.path.join(dirpath, f))
        return companions

    def delete(self, song_path: str) -> None:
        """Delete a song from disk, SongList, and DB."""
        logging.info(f"Deleting song: {song_path}")
        companions = self._get_companion_files(song_path)
        with contextlib.suppress(FileNotFoundError):
            os.remove(song_path)
        for companion in companions:
            with contextlib.suppress(FileNotFoundError):
                os.remove(companion)
        self.songs.remove(song_path)
        self._db.delete_by_path(song_path)

    def rename(self, song_path: str, new_name: str) -> str:
        """Rename a song on disk, in SongList, and in DB. Returns new path.

        Args:
            song_path: Full path to the current song file.
            new_name: New filename (without extension).
        """
        new_name = sanitize_filename(new_name)
        logging.info(f"Renaming song: '{song_path}' to: {new_name}")
        companions = self._get_companion_files(song_path)
        _, ext = os.path.splitext(song_path)
        new_path = os.path.join(self.download_path, new_name + ext)
        os.rename(song_path, new_path)
        for companion in companions:
            companion_ext = os.path.splitext(companion)[1]
            os.rename(companion, os.path.join(self.download_path, new_name + companion_ext))
        self.songs.rename(song_path, new_path)
        self._db.update_path(song_path, new_path)
        return new_path

    def register_download(self, song_path: str) -> None:
        """Register a newly downloaded song in SongList and DB."""
        self.songs.add_if_valid(song_path)
        self._db.insert_songs([build_song_record(song_path)])
