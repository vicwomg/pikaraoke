"""Tests for the non-torch surface of demucs_processor."""

from pikaraoke.lib.demucs_processor import resolve_audio_source


class TestResolveAudioSource:
    """Prewarm uses the sibling .m4a when it exists; otherwise the input.

    These paths feed both the cache-key SHA256 and the ffmpeg extract
    step, so a stable answer across download- and play-time invocations
    is the whole point.
    """

    def test_prefers_sibling_m4a(self, tmp_path):
        video = tmp_path / "song.mp4"
        audio = tmp_path / "song.m4a"
        video.write_text("")
        audio.write_text("")
        assert resolve_audio_source(str(video)) == str(audio)

    def test_no_sibling_returns_input(self, tmp_path):
        video = tmp_path / "song.mp4"
        video.write_text("")
        assert resolve_audio_source(str(video)) == str(video)

    def test_audio_input_returned_unchanged(self, tmp_path):
        audio = tmp_path / "song.m4a"
        audio.write_text("")
        assert resolve_audio_source(str(audio)) == str(audio)

    def test_mp3_input_returned_unchanged(self, tmp_path):
        # mp3 callers bypass sibling lookup — the file itself is audio.
        mp3 = tmp_path / "song.mp3"
        mp3.write_text("")
        assert resolve_audio_source(str(mp3)) == str(mp3)

    def test_webm_without_sibling_returns_input(self, tmp_path):
        # We scope sibling resolution to mp4 callers in FileResolver, but
        # the helper itself falls through for any video container.
        video = tmp_path / "song.webm"
        video.write_text("")
        assert resolve_audio_source(str(video)) == str(video)
