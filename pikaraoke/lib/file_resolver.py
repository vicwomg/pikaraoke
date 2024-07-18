import logging
import os
import re
import shutil
import zipfile
from pathlib import Path

from .get_platform import get_platform

logger = logging.getLogger(__name__)


class FileResolver:
    """Processes a given file path to determine the file format and file path

    Extracting zips into cdg + mp3 if necessary.
    """

    def __init__(self, file: str):
        self._pid = os.getpid()  # for scoping tmp directories to this process

        self._file_path = None
        self._cdg_file_path = None
        self._file_extension = None

        # Determine tmp directories (for things like extracted cdg files)
        if get_platform().is_windows():
            self.tmp_dir = (
                Path.home() / "AppData" / "Local" / "Temp" / "pikaraoke" / str(self._pid) / ""
            )
        else:
            self.tmp_dir = Path("/tmp") / "pikaraoke" / str(self._pid)

        self.resolved_file_path = self._process_file(Path(file))

    @property
    def file_path(self):
        return self._file_path

    @property
    def cdg_file_path(self):
        return self._cdg_file_path

    @property
    def file_extension(self):
        return self._file_extension

    def _handle_zipped_cdg(self, file_path: Path):
        """Extract zipped cdg + mp3 files into a temporary directory

        Sets the paths to both files.
        """
        extracted_dir = self.tmp_dir.joinpath("extracted")
        if extracted_dir.is_dir():
            shutil.rmtree(str(extracted_dir))  # clears out any previous extractions
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(extracted_dir)

        mp3_file = None
        cdg_file = None
        files = extracted_dir.iterdir()

        for file in files:
            file_extension = file.suffix.casefold()
            if file_extension == ".mp3":
                mp3_file = file
            elif file_extension == ".cdg":
                cdg_file = file

        if all([mp3_file, cdg_file]):
            if mp3_file.stem == cdg_file.stem:
                self._file_path = extracted_dir.joinpath(mp3_file)
                self._cdg_file_path = extracted_dir.joinpath(cdg_file)
            else:
                raise Exception("Zipped .mp3 file did not have a matching .cdg file: " + files)
        else:
            raise Exception("No .mp3 or .cdg was found in the zip file: " + file_path)

    def _handle_mp3_cdg(self, file_path: Path):
        f = file_path.stem
        pattern = f + ".cdg"
        rule = re.compile(re.escape(pattern), re.IGNORECASE)
        p = file_path.parent  # get the path, not the filename

        for n in p.iterdir():
            if n.is_file() and rule.match(n.name):
                self._file_path = file_path
                self._cdg_file_path = file_path.with_suffix(".cdg")
                return

        raise Exception("No matching .cdg file found for: " + file_path)

    def _process_file(self, file_path: Path):
        self._file_extension = file_path.suffix.casefold()

        if self._file_extension == ".zip":
            self._handle_zipped_cdg(file_path)
        elif self._file_extension == ".mp3":
            self._handle_mp3_cdg(file_path)
        else:
            self._file_path = file_path
