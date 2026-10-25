"""AI lead-vocal removal, leaving a karaoke track beside a song."""

import glob
import importlib.util
import logging
import os
import subprocess
import sys
import tempfile

from pikaraoke.lib.get_platform import get_data_directory, use_spare_capacity

# The track lives next to its song under the same name, so the library's rename
# and delete carry it along like a .cdg or .ass.
INSTRUMENTAL_SUFFIX = ".instrumental.m4a"

# A karaoke model: it takes out the lead vocal and keeps the backing vocals,
# which a singer sings along with and a full vocal remover would strip.
SEPARATION_MODEL = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"

# Two prediction windows instead of the model's eight: about four times faster,
# which is the difference between ready before the song's turn and not.
SEPARATION_OVERLAP = "2"

# A CPU-only separation of a long song on a slow machine; beyond this it is stuck.
SEPARATION_TIMEOUT_S = 60 * 60

_RUN_CLI = "from audio_separator.utils.cli import main; main()"


def is_vocal_removal_available() -> bool:
    """Whether audio-separator is installed, via the optional `vocal-removal` extra."""
    return importlib.util.find_spec("audio_separator") is not None


def instrumental_path(song_path: str) -> str:
    """Where a song's karaoke track is, or would be written."""
    return os.path.splitext(song_path)[0] + INSTRUMENTAL_SUFFIX


def find_instrumental(song_path: str) -> str | None:
    """The song's karaoke track, if it has been separated."""
    path = instrumental_path(song_path)
    return path if os.path.isfile(path) else None


def _separation_command(audio_path: str, out_dir: str) -> list[str]:
    # The model is downloaded on first use (about 900MB); keep it with the rest of
    # PiKaraoke's data rather than in the temp dir the OS clears.
    model_dir = os.path.join(get_data_directory(), "separation-models")
    return [
        sys.executable,
        "-c",
        _RUN_CLI,
        "-m",
        SEPARATION_MODEL,
        "--mdxc_overlap",
        SEPARATION_OVERLAP,
        "--single_stem",
        "Instrumental",
        "--output_format",
        "WAV",
        "--output_dir",
        out_dir,
        "--model_file_dir",
        model_dir,
        audio_path,
    ]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
    )
    use_spare_capacity(process)
    try:
        output, _ = process.communicate(timeout=SEPARATION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
    return subprocess.CompletedProcess(cmd, process.returncode, output)


def separate_vocals(song_path: str) -> bool:
    """Write the song's karaoke track beside it. Returns True on success."""
    with tempfile.TemporaryDirectory(prefix="pikaraoke-separation-") as work_dir:
        # The separator reads audio files only, not the video containers songs come in.
        audio = os.path.join(work_dir, "audio.wav")
        extract = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", song_path, "-vn", audio])
        if extract.returncode != 0:
            logging.error(f"Extracting audio failed for {song_path}: {extract.stdout}")
            return False

        out_dir = os.path.join(work_dir, "out")
        result = _run(_separation_command(audio, out_dir))
        stems = glob.glob(os.path.join(out_dir, "*.wav"))
        if result.returncode != 0 or len(stems) != 1:
            logging.error(f"Vocal separation failed for {song_path}: {result.stdout[-2000:]}")
            return False

        target = instrumental_path(song_path)
        # Encode beside the song, then rename into place, so playback never picks up
        # a half-written track.
        partial = os.path.splitext(song_path)[0] + ".instrumental.part.m4a"
        encode = _run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", stems[0]]
            + ["-c:a", "aac", "-b:a", "192k", partial]
        )
        if encode.returncode != 0:
            logging.error(f"Encoding instrumental failed for {song_path}: {encode.stdout}")
            if os.path.exists(partial):
                os.remove(partial)
            return False
        os.replace(partial, target)
    logging.info(f"Separated vocals: {target}")
    return True
