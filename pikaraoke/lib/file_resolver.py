import os
import re
import shutil
import zipfile
from sys import maxsize

from pikaraoke.lib.get_platform import get_platform


def get_tmp_dir():
    # Determine tmp directories (for things like extracted cdg files)
    pid = os.getpid()  # for scoping tmp directories to this process
    if get_platform() == "windows":
        tmp_dir = os.path.expanduser(r"~\\AppData\\Local\\Temp\\pikaraoke\\" + str(pid) + r"\\")
    else:
        tmp_dir = f"/tmp/pikaraoke/{pid}"
    return tmp_dir


def create_tmp_dir():
    tmp_dir = get_tmp_dir()
    # create tmp_dir if it doesn't exist
    if not os.path.exists(tmp_dir):
        os.makedirs(tmp_dir)


def delete_tmp_dir():
    tmp_dir = get_tmp_dir()
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)


def string_to_hash(s):
    return hash(s) % ((maxsize + 1) * 2)


# Processes a given file path and determines the file format and file path, extracting zips into cdg + mp3 if necessary.
class FileResolver:
    file_path = None
    cdg_file_path = None
    file_extension = None

    def __init__(self, file_path, buffer_fully_before_playback=False):
        create_tmp_dir()
        self.tmp_dir = get_tmp_dir()
        self.resolved_file_path = self.process_file(file_path)
        self.stream_uid = string_to_hash(file_path)
        self.output_file = f"{self.tmp_dir}/{self.stream_uid}.mp4"
        if buffer_fully_before_playback:
            # This route is used for streaming the full video file, and includes more
            # accurate headers for safari and other browsers
            self.stream_url_path = f"/stream/full/{self.stream_uid}"
        else:
            # This route is used for streaming the video file in chunks, only works on chrome
            self.stream_url_path = f"/stream/{self.stream_uid}"

    # Extract zipped cdg + mp3 files into a temporary directory, and set the paths to both files.
    def handle_zipped_cdg(self, file_path):
        extracted_dir = os.path.join(self.tmp_dir, "extracted")
        if os.path.exists(extracted_dir):
            shutil.rmtree(extracted_dir)  # clears out any previous extractions
        with zipfile.ZipFile(file_path, "r") as zip_ref:
            zip_ref.extractall(extracted_dir)

        mp3_file = None
        cdg_file = None
        files = os.listdir(extracted_dir)
        print(files)
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
                raise Exception("Zipped .mp3 file did not have a matching .cdg file: " + files)
        else:
            raise Exception("No .mp3 or .cdg was found in the zip file: " + file_path)

    def handle_mp3_cdg(self, file_path):
        f = os.path.splitext(os.path.basename(file_path))[0]
        pattern = f + ".cdg"
        rule = re.compile(re.escape(pattern), re.IGNORECASE)
        p = os.path.dirname(file_path)  # get the path, not the filename
        print(p)
        print(pattern)
        for n in os.listdir(p):
            if rule.match(n):
                self.file_path = file_path
                self.cdg_file_path = file_path.replace(".mp3", ".cdg")
                return True

        raise Exception("No matching .cdg file found for: " + file_path)

    def process_file(self, file_path):
        file_extension = os.path.splitext(file_path)[1].casefold()
        self.file_extension = file_extension
        if file_extension == ".zip":
            self.handle_zipped_cdg(file_path)
        elif file_extension == ".mp3":
            self.handle_mp3_cdg(file_path)
        else:
            self.file_path = file_path
