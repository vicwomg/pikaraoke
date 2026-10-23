"""Unit tests for stream route helpers."""

from pikaraoke.routes.stream import _parse_byte_range

SIZE = 1000


class TestParseByteRange:
    """RFC 7233 single ranges resolve to inclusive bounds; the rest return None."""

    def test_explicit_range(self):
        assert _parse_byte_range("bytes=0-499", SIZE) == (0, 499)

    def test_open_ended_range(self):
        assert _parse_byte_range("bytes=500-", SIZE) == (500, 999)

    def test_suffix_range_serves_the_last_bytes(self):
        """The case the old regex could not match and crashed on."""
        assert _parse_byte_range("bytes=-500", SIZE) == (500, 999)

    def test_suffix_larger_than_file_clamps_to_whole_file(self):
        assert _parse_byte_range("bytes=-99999", SIZE) == (0, 999)

    def test_end_past_the_file_is_clamped(self):
        assert _parse_byte_range("bytes=0-99999", SIZE) == (0, 999)

    def test_start_past_the_file_is_unsatisfiable(self):
        assert _parse_byte_range("bytes=9999-", SIZE) is None

    def test_zero_length_suffix_is_unsatisfiable(self):
        assert _parse_byte_range("bytes=-0", SIZE) is None

    def test_empty_range_is_rejected(self):
        assert _parse_byte_range("bytes=-", SIZE) is None

    def test_non_numeric_range_is_rejected(self):
        assert _parse_byte_range("bytes=abc", SIZE) is None

    def test_non_bytes_unit_is_rejected(self):
        assert _parse_byte_range("items=0-499", SIZE) is None
