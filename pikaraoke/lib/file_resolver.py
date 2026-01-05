"""File resolution and temporary file management utilities."""

import os
import shutil
import tempfile
import time
import zipfile
from sys import maxsize

from pikaraoke.karaoke import logging
from pikaraoke.lib.ffmpeg import get_media_duration
from pikaraoke.lib.get_platform import get_platform


def get_tmp_dir() -> str:
    """Get the temporary directory path scoped to this process.

    Returns:
        Path to the process-specific temporary directory.
    """
    pid = os.getpid()  # for scoping tmp directories to this process
    tmp_dir = os.path.join(tempfile.gettempdir(), f"{pid}")
    return tmp_dir


def create_tmp_dir() -> None:
    """Create the temporary directory if it doesn't exist."""
    tmp_dir = get_tmp_dir()
    if not os.path.exists(tmp_dir):
        os.makedirs(tmp_dir)


def delete_tmp_dir() -> None:
    """Delete the temporary directory and all its contents."""
    tmp_dir = get_tmp_dir()
    if os.path.exists(tmp_dir):
        # On Windows, files may still be locked briefly after process termination
        # Use error handler to ignore permission errors on individual files
        def handle_remove_error(func, path, exc_info):
            """Error handler for shutil.rmtree - ignores permission errors on Windows"""
            import logging

            if isinstance(exc_info[1], PermissionError):
                logging.debug(
                    f"Could not delete {path}: file in use, will be cleaned up on next run"
                )
            else:
                logging.warning(f"Error deleting {path}: {exc_info[1]}")

        shutil.rmtree(tmp_dir, onerror=handle_remove_error)


def string_to_hash(s: str) -> int:
    """Convert a string to a positive integer hash.

    Args:
        s: String to hash.

    Returns:
        Positive integer hash value.
    """
    return hash(s) % ((maxsize + 1) * 2)


def is_cdg_file(file_path: str) -> bool:
    """Check if a file is a CDG karaoke file (zip or mp3 with cdg).

    Args:
        file_path: Path to the file.

    Returns:
        True if the file is a CDG-related format.
    """
    file_extension = os.path.splitext(file_path)[1].casefold()
    return file_extension == ".zip" or file_extension == ".mp3"


def is_transcoding_required(file_path: str) -> bool:
    """Check if a file requires transcoding for browser playback.

    MP4 and WebM files can be played natively; others need transcoding.

    Args:
        file_path: Path to the media file.

    Returns:
        True if transcoding is required, False otherwise.
    """
    file_extension = os.path.splitext(file_path)[1].casefold()
    return file_extension != ".mp4" and file_extension != ".webm"


class FileResolver:
    """Resolves media files for playback, handling CDG and zipped formats.

    Processes a given file path and determines the file format and paths,
    extracting zips into cdg + mp3 if necessary.

    Attributes:
        file_path: Path to the main media file (audio).
        cdg_file_path: Path to the CDG graphics file, if applicable.
        file_extension: Lowercase file extension of the input file.
        tmp_dir: Temporary directory for extracted files.
        stream_uid: Unique identifier for the stream based on file path hash.
        output_file: Path where the transcoded output will be written.
        segment_pattern: Pattern for HLS segment filenames.
        init_filename: Filename for HLS initialization segment.
        streaming_format: Video streaming format ('hls' or 'mp4').
        duration: Duration of the media file in seconds.
    """

    file_path: str | None = None
    cdg_file_path: str | None = None
    file_extension: str | None = None
    ass_file_path: str | None = None

    def __init__(self, file_path: str, streaming_format: str = "hls") -> None:
        """Initialize the FileResolver with a media file path.

        Args:
            file_path: Path to the media file to resolve.
            streaming_format: Video streaming format ('hls' or 'mp4').
        """
        create_tmp_dir()
        self.tmp_dir = get_tmp_dir()
        self.resolved_file_path = self.process_file(file_path)
        # Include timestamp to ensure unique stream UIDs for repeated plays
        unique_string = f"{file_path}_{time.time()}"
        self.stream_uid = string_to_hash(unique_string)
        self.streaming_format = streaming_format

        # Set output file extension based on streaming format
        if streaming_format == "mp4":
            self.output_file = f"{self.tmp_dir}/{self.stream_uid}.mp4"
        else:  # hls
            self.output_file = f"{self.tmp_dir}/{self.stream_uid}.m3u8"

        self.segment_pattern = f"{self.tmp_dir}/{self.stream_uid}_segment_%03d.m4s"
        self.init_filename = f"{self.stream_uid}_init.mp4"

    def get_current_stream_size(self) -> int:
        """Get the size of files belonging to this stream in the temporary directory.

        Only counts files containing the stream_uid in their filename.
        Primarily used for HLS mode to check if the buffer is full before starting playback.
        """
        stream_uid_str = str(self.stream_uid)
        return sum(
            os.path.getsize(os.path.join(self.tmp_dir, f))
            for f in os.listdir(self.tmp_dir)
            if stream_uid_str in f
        )

    def handle_aegissub_subtile(self, file_path: str) -> bool:
        """Find and set the ASS subtitle file path for an media file.

        Searches for an ASS file with the same base name as the media.

        Args:
            file_path: Path to the media file.

        Returns:
            True if ASS file found, False otherwise.
        """
        base_name = os.path.splitext(file_path)[0]

        # Check common case variations without listing directory
        for ext in (".ass", ".ASS", ".Ass"):
            ass_path = base_name + ext
            if os.path.exists(ass_path):
                self.file_path = file_path
                self.ass_file_path = ass_path
                logging.debug(f"Subtitle file found: {ass_path}")
                return True
        return False

    def handle_zipped_cdg(self, file_path: str) -> None:
        """Extract zipped CDG + MP3 files into a temporary directory.

        Sets self.file_path and self.cdg_file_path to the extracted files.

        Args:
            file_path: Path to the zip file containing CDG and MP3.

        Raises:
            Exception: If the zip doesn't contain matching CDG and MP3 files.
        """
        extracted_dir = os.path.join(self.tmp_dir, "extracted")
        if os.path.exists(extracted_dir):
            shutil.rmtree(extracted_dir)  # clears out any previous extractions
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(extracted_dir)

        mp3_file = None
        cdg_file = None
        files = os.listdir(extracted_dir)
        for file in files:
            ext = os.path.splitext(file)[1]
            if ext.casefold() == ".mp3":
                mp3_file = file
            elif ext.casefold() == ".cdg":
                cdg_file = file
        if (mp3_file is not None) and (cdg_file is not None):
            if os.path.splitext(mp3_file)[0] == os.path.splitext(cdg_file)[0]:
                self.file_path = os.path.join(extracted_dir, mp3_file)
                self.cdg_file_path = os.path.join(extracted_dir, cdg_file)
            else:
                raise Exception(
                    "Zipped .mp3 file did not have a matching .cdg file: " + ", ".join(files)
                )
        else:
            raise Exception("No .mp3 or .cdg was found in the zip file: " + file_path)

    def handle_mp3_cdg(self, file_path: str) -> bool:
        """Find and set the CDG file path for an MP3 file.

        Searches for a CDG file with the same base name as the MP3.

        Args:
            file_path: Path to the MP3 file.

        Returns:
            True if a matching CDG file was found.

        Raises:
            Exception: If no matching CDG file is found.
        """
        base_name = os.path.splitext(file_path)[0]

        # Check common case variations without listing directory
        for ext in (".cdg", ".CDG", ".Cdg"):
            cdg_path = base_name + ext
            if os.path.exists(cdg_path):
                self.file_path = file_path
                self.cdg_file_path = cdg_path
                return True

        raise Exception("No matching .cdg file found for: " + file_path)

    def process_file(self, file_path: str) -> None:
        """Process a file path and set up resolution based on file type.

        Args:
            file_path: Path to the media file.
        """

        file_extension = os.path.splitext(file_path)[1].casefold()
        self.file_extension = file_extension
        if file_extension == ".zip":
            self.handle_zipped_cdg(file_path)
        elif file_extension == ".mp3":
            self.handle_mp3_cdg(file_path)
        else:
            self.file_path = file_path
            # If there is an aegissub subtitle file found, set the path to it
            self.handle_aegissub_subtile(file_path)
        if not self.file_path:
            raise ValueError("File path is required to process file")
        self.duration = get_media_duration(self.file_path)
