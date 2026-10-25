"""Unit tests for AI lead-vocal removal."""

import os
import subprocess
from unittest.mock import patch

from pikaraoke.lib.vocal_remover import (
    SEPARATION_MODEL,
    _separation_command,
    find_instrumental,
    instrumental_path,
    separate_vocals,
)


def _completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout)


class TestInstrumentalPath:
    def test_sits_beside_the_song_under_its_name(self):
        assert instrumental_path("/songs/Title---abc.mp4") == "/songs/Title---abc.instrumental.m4a"

    def test_find_returns_only_an_existing_track(self, tmp_path):
        song = tmp_path / "Title---abc.mp4"
        assert find_instrumental(str(song)) is None
        (tmp_path / "Title---abc.instrumental.m4a").write_bytes(b"aac")
        assert find_instrumental(str(song)) == str(tmp_path / "Title---abc.instrumental.m4a")


class TestSeparationCommand:
    @patch("pikaraoke.lib.vocal_remover.get_data_directory", return_value="/data")
    def test_uses_the_karaoke_model_kept_with_app_data(self, mock_data_dir):
        cmd = _separation_command("/songs/a.mp4", "/tmp/out")
        assert cmd[cmd.index("-m") + 1] == SEPARATION_MODEL
        assert cmd[cmd.index("--output_dir") + 1] == "/tmp/out"
        assert cmd[cmd.index("--model_file_dir") + 1] == os.path.join("/data", "separation-models")
        assert cmd[-1] == "/songs/a.mp4"


class TestSeparateVocals:
    def _fake_run(self, separation_rc=0, encode_rc=0):
        def run(cmd):
            if "--output_dir" in cmd:
                out_dir = cmd[cmd.index("--output_dir") + 1]
                if separation_rc == 0:
                    os.makedirs(out_dir)
                    for stem in ("Instrumental", "Vocals"):
                        with open(os.path.join(out_dir, f"a_({stem})_model.wav"), "wb") as f:
                            f.write(b"wav")
                return _completed(separation_rc, "separator log")
            if cmd[-1].endswith("audio.wav"):
                return _completed(0, "")  # Audio extraction
            if encode_rc == 0:
                with open(cmd[-1], "wb") as f:
                    f.write(b"aac")
            return _completed(encode_rc, "ffmpeg log")

        return run

    def test_writes_both_stems_beside_song(self, tmp_path):
        song = tmp_path / "Title---abc.mp4"
        song.write_bytes(b"video")
        with patch("pikaraoke.lib.vocal_remover._run", side_effect=self._fake_run()):
            assert separate_vocals(str(song)) is True
        assert sorted(os.listdir(tmp_path)) == [
            "Title---abc.instrumental.m4a",
            "Title---abc.mp4",
            "Title---abc.vocals.m4a",
        ]

    def test_separates_the_extracted_audio_not_the_video(self, tmp_path):
        song = tmp_path / "Title---abc.mp4"
        song.write_bytes(b"video")
        calls = []

        def run(cmd):
            calls.append(cmd)
            return self._fake_run()(cmd)

        with patch("pikaraoke.lib.vocal_remover._run", side_effect=run):
            separate_vocals(str(song))
        extract, separate = calls[0], calls[1]
        assert extract[extract.index("-i") + 1] == str(song)
        assert separate[-1] == extract[-1]

    def test_separation_failure_writes_nothing(self, tmp_path):
        song = tmp_path / "Title---abc.mp4"
        song.write_bytes(b"video")
        with patch("pikaraoke.lib.vocal_remover._run", side_effect=self._fake_run(separation_rc=1)):
            assert separate_vocals(str(song)) is False
        assert sorted(os.listdir(tmp_path)) == ["Title---abc.mp4"]

    def test_encode_failure_leaves_no_partial_track(self, tmp_path):
        song = tmp_path / "Title---abc.mp4"
        song.write_bytes(b"video")

        def run(cmd):
            result = self._fake_run(encode_rc=1)(cmd)
            if cmd[-1].endswith(".part.m4a"):
                with open(cmd[-1], "wb") as f:
                    f.write(b"half")
            return result

        with patch("pikaraoke.lib.vocal_remover._run", side_effect=run):
            assert separate_vocals(str(song)) is False
        assert sorted(os.listdir(tmp_path)) == ["Title---abc.mp4"]
