"""AI lead-vocal removal, leaving a karaoke track beside a song."""

import glob
import importlib.util
import logging
import os
import subprocess
import sys
import tempfile

import ffmpeg

from pikaraoke.lib.get_platform import get_data_directory, use_spare_capacity

# The tracks live next to their song under the same name, so the library's rename
# and delete carry them along like a .cdg or .ass. The lead vocal is kept to time
# lyrics against.
INSTRUMENTAL_SUFFIX = ".instrumental.m4a"
VOCALS_SUFFIX = ".vocals.m4a"

# A karaoke model: it takes out the lead vocal and keeps the backing vocals,
# which a singer sings along with and a full vocal remover would strip.
SEPARATION_MODEL = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
# Takes out every voice. For a chorus whose lead is sung in stacked layers, which
# the karaoke model mistakes for backing vocals and leaves in.
ALL_VOCALS_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

# Written into an instrumental with every voice taken out, so it is not redone.
ALL_VOCALS_REMOVED_TAG = "PiKaraoke: all vocals removed"

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


def vocals_path(song_path: str) -> str:
    """Where a song's separated lead vocal is, or would be written."""
    return os.path.splitext(song_path)[0] + VOCALS_SUFFIX


def find_vocals(song_path: str) -> str | None:
    """The song's separated lead vocal, if it has one."""
    path = vocals_path(song_path)
    return path if os.path.isfile(path) else None


def find_instrumental(song_path: str) -> str | None:
    """The song's karaoke track, if it has been separated."""
    path = instrumental_path(song_path)
    return path if os.path.isfile(path) else None


def has_all_vocals_removed(song_path: str) -> bool:
    """Whether the song's instrumental has its backing vocals taken out too."""
    path = find_instrumental(song_path)
    if path is None:
        return False
    try:
        tags = ffmpeg.probe(path)["format"].get("tags", {})
    except ffmpeg.Error as e:
        logging.error(f"Reading {path} failed: {e.stderr}")
        return False
    return tags.get("comment") == ALL_VOCALS_REMOVED_TAG


def _separation_command(audio_path: str, out_dir: str, model: str) -> list[str]:
    # The model is downloaded on first use (about 900MB); keep it with the rest of
    # PiKaraoke's data rather than in the temp dir the OS clears.
    model_dir = os.path.join(get_data_directory(), "separation-models")
    return [
        sys.executable,
        "-c",
        _RUN_CLI,
        "-m",
        model,
        "--mdxc_overlap",
        SEPARATION_OVERLAP,
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


def _encode_beside(stem: str, target: str, comment: str | None = None) -> bool:
    """Encode a stem to `target`, appearing only once complete.

    Encoded under a partial name first, so playback never picks up a half-written track.
    """
    partial = target.removesuffix(".m4a") + ".part.m4a"
    tags = ["-metadata", f"comment={comment}"] if comment else []
    encode = _run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", stem, "-c:a", "aac", "-b:a", "192k"]
        + tags
        + [partial]
    )
    if encode.returncode != 0:
        logging.error(f"Encoding {target} failed: {encode.stdout}")
        if os.path.exists(partial):
            os.remove(partial)
        return False
    os.replace(partial, target)
    return True


def separate_vocals(song_path: str, keep_backing: bool) -> bool:
    """Write the song's karaoke track and vocal stem beside it, replacing any there.

    With `keep_backing` only the lead vocal comes out; otherwise every voice does.
    True on success.
    """
    with tempfile.TemporaryDirectory(prefix="pikaraoke-separation-") as work_dir:
        # The separator reads audio files only, not the video containers songs come in.
        audio = os.path.join(work_dir, "audio.wav")
        extract = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", song_path, "-vn", audio])
        if extract.returncode != 0:
            logging.error(f"Extracting audio failed for {song_path}: {extract.stdout}")
            return False

        out_dir = os.path.join(work_dir, "out")
        model = SEPARATION_MODEL if keep_backing else ALL_VOCALS_MODEL
        result = _run(_separation_command(audio, out_dir, model))
        instrumental = glob.glob(os.path.join(out_dir, "*(Instrumental)*.wav"))
        vocals = glob.glob(os.path.join(out_dir, "*(Vocals)*.wav"))
        if result.returncode != 0 or len(instrumental) != 1 or len(vocals) != 1:
            logging.error(f"Vocal separation failed for {song_path}: {result.stdout[-2000:]}")
            return False

        # The instrumental goes last: its presence is what marks a song as separated.
        if not _encode_beside(vocals[0], vocals_path(song_path)):
            return False
        comment = None if keep_backing else ALL_VOCALS_REMOVED_TAG
        if not _encode_beside(instrumental[0], instrumental_path(song_path), comment):
            return False
    logging.info(f"Separated vocals: {song_path}")
    return True
