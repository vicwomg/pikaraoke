"""The start watchdog's progress probe must report real growth for both formats.

`transcode_progress` is what tells a stalled FFmpeg from a slow-but-healthy one,
so it has to measure growth in whichever form the transcode writes: HLS counts
its .m4s segments, progressive MP4 its single .mp4. Before this, the probe only
looked at .m4s files, so an MP4 transcode always read as (0, 0) and the watchdog
would treat a healthy MP4 transcode as stalled.
"""

from unittest.mock import MagicMock

from pikaraoke.lib.stream_manager import StreamManager

STREAM_UID = "17264398115502847361"
OTHER_UID = "4820159763348201975"


def _manager(tmp_path, streaming_format, fr):
    """A StreamManager whose in-flight transcode is the given FileResolver."""
    prefs = MagicMock()
    prefs.get_or_default.return_value = 0
    mgr = StreamManager(prefs, streaming_format=streaming_format)
    mgr._current_fr = fr
    return mgr


def _fr(tmp_dir):
    fr = MagicMock()
    fr.stream_uid = STREAM_UID
    fr.tmp_dir = tmp_dir
    return fr


class TestTranscodeProgressHls:
    def test_counts_only_this_streams_segments(self, tmp_path):
        # Two .m4s segments for this stream, a decoy for another, a non-segment.
        (tmp_path / f"{STREAM_UID}_segment_000.m4s").write_bytes(b"x" * 100)
        (tmp_path / f"{STREAM_UID}_segment_001.m4s").write_bytes(b"x" * 250)
        (tmp_path / f"{OTHER_UID}_segment_000.m4s").write_bytes(b"x" * 999)
        (tmp_path / f"{STREAM_UID}_init.mp4").write_bytes(b"y" * 50)

        mgr = _manager(tmp_path, "hls", _fr(str(tmp_path)))
        progress = mgr.transcode_progress(STREAM_UID)
        assert progress is not None
        count, total = progress

        assert count == 2
        assert total == 350

    def test_none_when_no_transcode_for_this_playback(self, tmp_path):
        (tmp_path / f"{STREAM_UID}_segment_000.m4s").write_bytes(b"x" * 100)
        mgr = _manager(tmp_path, "hls", _fr(str(tmp_path)))
        # A different playback id is not the one in flight.
        assert mgr.transcode_progress(OTHER_UID) is None
        # No transcode in flight at all.
        mgr._current_fr = None
        assert mgr.transcode_progress(STREAM_UID) is None

    def test_zero_when_dir_is_empty(self, tmp_path):
        mgr = _manager(tmp_path, "hls", _fr(str(tmp_path)))
        assert mgr.transcode_progress(STREAM_UID) == (0, 0)


class TestTranscodeProgressMp4:
    def test_reports_the_single_mp4_size(self, tmp_path):
        (tmp_path / f"{STREAM_UID}.mp4").write_bytes(b"x" * 4000)
        (tmp_path / f"{OTHER_UID}.mp4").write_bytes(b"x" * 999)

        mgr = _manager(tmp_path, "mp4", _fr(str(tmp_path)))
        progress = mgr.transcode_progress(STREAM_UID)
        assert progress is not None
        count, total = progress

        assert count == 1
        assert total == 4000

    def test_zero_bytes_until_the_file_appears(self, tmp_path):
        mgr = _manager(tmp_path, "mp4", _fr(str(tmp_path)))
        assert mgr.transcode_progress(STREAM_UID) == (0, 0)

        # As FFmpeg grows the .mp4, the probe sees real growth (no (0,0) stall).
        (tmp_path / f"{STREAM_UID}.mp4").write_bytes(b"x" * 1000)
        assert mgr.transcode_progress(STREAM_UID) == (1, 1000)
        (tmp_path / f"{STREAM_UID}.mp4").write_bytes(b"x" * 2500)
        assert mgr.transcode_progress(STREAM_UID) == (1, 2500)
