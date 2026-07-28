"""Song library management: scan, delete, rename, and display name operations."""

import contextlib
import logging
import os
import re
from collections.abc import Callable

from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.get_platform import is_windows
from pikaraoke.lib.karaoke_database import KaraokeDatabase
from pikaraoke.lib.library_scanner import build_song_record
from pikaraoke.lib.metadata_parser import regex_tidy, remove_accents, youtube_id_suffix
from pikaraoke.lib.song_list import SongList

# Characters illegal in Windows filenames
_WINDOWS_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*]')
_PATH_SEPARATORS = re.compile(r"[/\\]")


def sanitize_filename(name: str) -> str:
    """Remove characters that are illegal in a filename on the current platform.

    Path separators go on every platform: callers pass a bare filename, so a
    separator can only be an attempt to write outside the song directory.
    """
    if is_windows():
        name = _WINDOWS_ILLEGAL_CHARS.sub("-", name)
    else:
        name = _PATH_SEPARATORS.sub("-", name)
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
        events: EventSystem,
        get_title_tidy: Callable[[], bool] | None = None,
    ) -> None:
        self.download_path = download_path
        self.songs = SongList()
        self._db = db
        self._events = events
        self._get_title_tidy = get_title_tidy
        self._index: list[tuple[str, str]] = []  # (accent-folded raw filename key, path)
        self._index_version: int | None = None
        self._folders: dict[str, list[str]] = {}  # folder key -> immediate subfolder names
        self._folders_version: int | None = None

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

    # ------------------------------------------------------------------
    # Matching
    #
    # The match key is the *untidied* filename stem. regex_tidy() is almost
    # entirely subtractive, so every term in the tidied name survives in the raw
    # one and the raw one additionally matches what tidy threw away. Matching the
    # tidied name could only ever find fewer songs -- and never "karaoke", which
    # TRAILING_NOISE_PATTERNS strips to end-of-string by construction, so
    # "bohemian karaoke" would silently return nothing. Keying on the raw stem
    # also makes the key independent of the enable_title_tidy preference.
    # ------------------------------------------------------------------

    def _match_index(self) -> list[tuple[str, str]]:
        """(accent-folded lowercase raw filename, path) pairs, rebuilt when the song list changes."""
        # Read the version BEFORE iterating: a mutation racing this rebuild then stores
        # newer data under an older version and rebuilds next call. Reading it after
        # would cache stale data under the new version and never rebuild.
        version = self.songs.version
        if self._index_version != version:
            self._index = [
                (remove_accents(self.filename_from_path(p, tidy=False)).lower(), p)
                for p in self.songs
            ]
            self._index_version = version
        return self._index

    def _scoped_index(self, folder: str | None) -> list[tuple[str, str]]:
        """Match index entries, narrowed to one folder's direct children.

        `None` means the whole library; `""` means the download path itself. The
        two are not the same: the flat view lists every song wherever it sits,
        while the root of the folder view lists only what is not in a subfolder.
        """
        index = self._match_index()
        if folder is None:
            return index
        target = self.folder_path(folder)
        return [
            (name, path)
            for name, path in index
            if os.path.dirname(os.path.normpath(path)) == target
        ]

    def search(self, query: str, folder: str | None = None) -> list[str]:
        """Songs whose filename contains every whitespace-separated term, in list order."""
        terms = remove_accents(query).lower().split()
        index = self._scoped_index(folder)
        if not terms:
            return [path for _, path in index]
        if len(terms) == 1:
            term = terms[0]
            return [path for name, path in index if term in name]
        return [path for name, path in index if all(t in name for t in terms)]

    def songs_by_letter(self, letter: str, folder: str | None = None) -> list[str]:
        """Songs whose filename starts with `letter`, or with a digit when 'numeric'.

        Grouping by the raw filename keeps the alpha bar in agreement with
        SongList's sort order, which also keys on the raw basename.
        """
        index = self._scoped_index(folder)
        if letter == "numeric":
            return [path for name, path in index if name[:1].isnumeric()]
        target = letter.lower()
        return [path for name, path in index if name[:1] == target]

    # ------------------------------------------------------------------
    # Folders
    #
    # Folder keys are relative to download_path and always "/"-separated, because
    # they travel as a URL parameter and must not change shape between platforms.
    # The empty string is the root.
    # ------------------------------------------------------------------

    def folder_path(self, folder: str) -> str:
        """Absolute directory for a folder key."""
        base = os.path.normpath(self.download_path)
        return os.path.join(base, *folder.split("/")) if folder else base

    def folder_tree(self) -> dict[str, list[str]]:
        """Every folder holding songs, mapped to its immediate subfolder names.

        Memoized against the song list like the match index: the browse page needs
        the root entry on every request to decide whether to offer the folder view
        at all, and rebuilding that per request is an O(songs) pass on a Pi.

        Because keys are derived from real song paths, membership doubles as the
        path-traversal guard -- "../../etc" is simply not a key.
        """
        version = self.songs.version
        if self._folders_version != version:
            children: dict[str, set[str]] = {"": set()}
            prefix = os.path.normpath(self.download_path) + os.sep
            for path in self.songs:
                directory = os.path.dirname(os.path.normpath(path))
                if not directory.startswith(prefix):
                    continue  # sits directly in the download path
                parent = ""
                for name in directory[len(prefix) :].split(os.sep):
                    children.setdefault(parent, set()).add(name)
                    parent = f"{parent}/{name}" if parent else name
                children.setdefault(parent, set())
            self._folders = {
                folder: sorted(names, key=str.lower) for folder, names in children.items()
            }
            self._folders_version = version
        return self._folders

    def songs_in_folder(self, folder: str) -> list[str]:
        """Songs sitting directly in `folder`, excluding its subfolders, in list order."""
        return [path for _, path in self._scoped_index(folder)]

    def _get_companion_files(self, song_path: str) -> list[str]:
        """Return paths to companion files (.cdg, .ass) that exist alongside a song."""
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
            if f_base.lower() == base_lower and f_ext.lower() in (".cdg", ".ass"):
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

    def rename_target(self, song_path: str, new_name: str) -> str:
        """The path `rename` writes `new_name` to, sanitized as `rename` sanitizes it.

        A caller checking the destination for a clash has to ask here. A path
        built by hand skips the sanitizing, so the real target moves out from
        under the check and os.rename replaces the clashing song without a word.
        """
        _, ext = os.path.splitext(song_path)
        # The song's own directory, not download_path: the scanner walks
        # recursively, so a renamed song stays in the subfolder it was filed in.
        return os.path.join(os.path.dirname(song_path), sanitize_filename(new_name) + ext)

    def rename(self, song_path: str, new_name: str) -> str:
        """Rename a song on disk, in SongList, and in DB. Returns new path.

        Args:
            song_path: Full path to the current song file.
            new_name: New filename (without extension).
        """
        new_name = sanitize_filename(new_name)
        logging.info(f"Renaming song: '{song_path}' to: {new_name}")
        companions = self._get_companion_files(song_path)
        dirpath = os.path.dirname(song_path)
        new_path = self.rename_target(song_path, new_name)
        os.rename(song_path, new_path)
        for companion in companions:
            companion_ext = os.path.splitext(companion)[1]
            os.rename(companion, os.path.join(dirpath, new_name + companion_ext))
        self.songs.rename(song_path, new_path)
        self._db.update_path(song_path, new_path)
        # A rename is the user correcting the name, not a display preference, so
        # the play log follows it. Without this a local rip -- which has no
        # YouTube id to be identified by -- would rank as two separate songs,
        # its plays split at the moment it was renamed.
        song_id = self._db.get_song_identity(new_path)[0]
        if song_id is not None:
            self._events.emit("song_renamed", song_id, self.display_name_from_path(new_path))
        return new_path

    def register_download(self, song_path: str) -> None:
        """Register a newly downloaded song in SongList and DB."""
        self.songs.add_if_valid(song_path)
        self._db.insert_songs([build_song_record(song_path)])
