"""Unit tests for audio_processor module."""

from unittest.mock import MagicMock, patch

from pikaraoke.lib.audio_processor import (
    BYTES_PER_SEC,
    WAV_HEADER_SIZE,
    AudioTrackConfig,
    build_audio_filters,
    build_pcm_command,
    build_wav_header,
    parse_range,
    stream_wav_range,
    total_wav_size,
)


class TestBuildAudioFilters:
    def test_no_transforms_returns_none(self):
        assert build_audio_filters(0, False) is None

    def test_semitones_only(self):
        result = build_audio_filters(2, False)
        assert result == f"rubberband=pitch={2 ** (2 / 12)}"

    def test_normalize_only(self):
        assert build_audio_filters(0, True) == "loudnorm=i=-16:tp=-1.5:lra=11"

    def test_both(self):
        result = build_audio_filters(-3, True)
        assert "rubberband" in result and "loudnorm" in result
        assert "," in result


class TestBuildPcmCommand:
    def test_includes_source_and_output_format(self):
        cmd = build_pcm_command("/songs/x.mp4", 0, False, start_sec=0)
        assert "-i" in cmd
        assert cmd[cmd.index("-i") + 1] == "/songs/x.mp4"
        assert "-f" in cmd
        assert cmd[cmd.index("-f") + 1] == "s16le"
        assert "-vn" in cmd
        assert cmd[-1] == "-"

    def test_start_sec_zero_omits_seek(self):
        cmd = build_pcm_command("/songs/x.mp4", 0, False, start_sec=0)
        assert "-ss" not in cmd

    def test_start_sec_nonzero_adds_seek_before_input(self):
        cmd = build_pcm_command("/songs/x.mp4", 0, False, start_sec=10.0)
        ss_idx = cmd.index("-ss")
        i_idx = cmd.index("-i")
        assert ss_idx < i_idx

    def test_transforms_added_as_af(self):
        cmd = build_pcm_command("/songs/x.mp4", 2, True, start_sec=0)
        assert "-af" in cmd
        af_value = cmd[cmd.index("-af") + 1]
        assert "rubberband" in af_value and "loudnorm" in af_value


class TestParseRange:
    def test_no_header_returns_full(self):
        assert parse_range(None, 1000) == (0, 999)

    def test_start_and_end(self):
        assert parse_range("bytes=100-500", 1000) == (100, 500)

    def test_open_ended(self):
        assert parse_range("bytes=100-", 1000) == (100, 999)

    def test_end_clamped_to_total(self):
        assert parse_range("bytes=100-5000", 1000) == (100, 999)

    def test_malformed_falls_back_to_full(self):
        assert parse_range("bytes=abc", 1000) == (0, 999)

    def test_inverted_falls_back_to_full(self):
        assert parse_range("bytes=500-100", 1000) == (0, 999)


class TestWavSizing:
    def test_header_is_44_bytes(self):
        assert len(build_wav_header(0)) == WAV_HEADER_SIZE

    def test_total_matches_duration(self):
        # 3 seconds * 192000 bytes/sec + 44 header
        assert total_wav_size(3.0) == WAV_HEADER_SIZE + 3 * BYTES_PER_SEC

    def test_header_contains_riff_wave_markers(self):
        header = build_wav_header(1000)
        assert header[:4] == b"RIFF"
        assert header[8:12] == b"WAVE"
        assert b"fmt " in header
        assert b"data" in header


class TestStreamWavRange:
    """Integration-style tests for the generator + headers."""

    def _config(self):
        return AudioTrackConfig(
            source_path="/songs/test.mp4",
            duration_sec=2.0,  # 384000 PCM bytes + 44 header = 384044
            semitones=0,
            normalize=False,
        )

    def test_full_request_returns_200(self):
        _, status, headers, total = stream_wav_range(self._config(), range_header=None)
        assert status == 200
        assert int(headers["Content-Length"]) == total
        assert "Content-Range" not in headers
        assert headers["Content-Type"] == "audio/wav"

    def test_range_request_returns_206_with_content_range(self):
        _, status, headers, total = stream_wav_range(self._config(), range_header="bytes=100-500")
        assert status == 206
        assert headers["Content-Range"] == f"bytes 100-500/{total}"
        assert int(headers["Content-Length"]) == 401

    def _mock_popen_returning(self, payload: bytes):
        """Patch Popen so proc.stdout.read(n) returns up to n bytes of payload."""
        from unittest.mock import patch as _patch

        buf = {"data": payload}

        def read(n=-1):
            if n < 0:
                out = buf["data"]
                buf["data"] = b""
                return out
            out = buf["data"][:n]
            buf["data"] = buf["data"][n:]
            return out

        proc = MagicMock()
        proc.stdout.read.side_effect = read
        return _patch("pikaraoke.lib.audio_processor.subprocess.Popen", return_value=proc)

    def test_generator_yields_header_first_when_range_starts_at_zero(self):
        """Initial bytes must be a valid WAV header."""
        with self._mock_popen_returning(b"\x00" * (128 * 1024)):
            generate, _status, _headers, _total = stream_wav_range(
                self._config(), range_header="bytes=0-100"
            )
            chunks = list(generate())

        buf = b"".join(chunks)
        assert len(buf) == 101
        assert buf[:4] == b"RIFF"
        assert buf[8:12] == b"WAVE"

    def test_range_beyond_header_skips_header(self):
        """Range that starts after the 44-byte header goes straight to PCM."""
        with self._mock_popen_returning(b"\xaa" * 100):
            generate, _status, _headers, _total = stream_wav_range(
                self._config(),
                range_header=f"bytes={WAV_HEADER_SIZE + 10}-{WAV_HEADER_SIZE + 19}",
            )
            chunks = list(generate())

        buf = b"".join(chunks)
        assert len(buf) == 10
        # Make sure we never emitted the header.
        assert b"RIFF" not in buf
