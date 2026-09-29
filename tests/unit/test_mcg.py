"""Unit tests for the CAVS .mcg to CD+G converter."""

import pytest

from pikaraoke.lib.mcg import decode_mcg

PACKET = 24


def _record(opcode: int, payload: bytes, position: int, k: int, row=0, colors=0, col=0) -> bytes:
    """A 16-byte record: `payload` is the 9 pixel bytes (byte 2, then bytes 5-12)."""
    trailer = (position >> 2) ^ (256 + k)
    return (
        bytes((row, colors, payload[0], col, opcode))
        + payload[1:]
        + bytes((0, trailer & 0xFF, trailer >> 8))
    )


def _write_mcg(path, records: list[bytes], header_record: bytes = bytes(16)) -> str:
    """Records are numbered from 1 when the header carries an instruction, else from 0."""
    path.write_bytes(b"CAVSMC" + bytes(10) + header_record + b"".join(records))
    return str(path)


def _convert(tmp_path, records, header_record=bytes(16)) -> bytes:
    return decode_mcg(_write_mcg(tmp_path / "song.mcg", records, header_record))


def _packet_at(stream: bytes, slot: int) -> bytes:
    return stream[slot * PACKET : (slot + 1) * PACKET]


class TestDecodeMcg:
    def test_tile_decodes_to_cdg_tile_block(self, tmp_path):
        # First and last of the 72 pixels set
        payload = bytes((0x80,)) + bytes(7) + bytes((0x01,))
        stream = _convert(
            tmp_path, [_record(0x7B, payload, position=0, k=0, row=5, colors=0x21, col=10)]
        )

        packet = _packet_at(stream, 0)
        assert packet[0] == 0x09
        assert packet[1] == 6
        # color0, color1, row, column
        assert packet[4:8] == bytes((1, 2, 5, 10))
        assert packet[8:20] == bytes((0x20,)) + bytes(10) + bytes((0x01,))

    def test_xor_tile_uses_xor_instruction(self, tmp_path):
        stream = _convert(tmp_path, [_record(0x7A, bytes(9), position=0, k=0)])
        assert _packet_at(stream, 0)[1] == 38

    def test_memory_preset_keeps_fill_colour(self, tmp_path):
        stream = _convert(tmp_path, [_record(0x7F, bytes(9), position=0, k=0, colors=0x07)])
        packet = _packet_at(stream, 0)
        assert packet[1] == 1
        assert packet[4] == 7

    def test_palette_nibbles_are_unshuffled(self, tmp_path):
        # Payload nibble i holds i % 16, so each colour component in slots 0-3
        # shows which nibble it was read from.
        nibbles = bytes((((i * 2) % 16) << 4) | ((i * 2 + 1) % 16) for i in range(12))
        record = bytearray(_record(0x7D, bytes(9), position=0, k=0))
        record[0:4] = nibbles[0:4]
        record[5:13] = nibbles[4:12]
        stream = _convert(tmp_path, [bytes(record)])

        packet = _packet_at(stream, 0)
        assert packet[1] == 30
        # Slot 0 is (r, g, b) = nibbles (2, 3, 0); slot 3 is (9, 10, 11)
        assert packet[4:6] == bytes(((2 << 2) | (3 >> 2), ((3 & 3) << 4) | 0))
        assert packet[10:12] == bytes(((9 << 2) | (10 >> 2), ((10 & 3) << 4) | 11))

    def test_high_palette_uses_high_instruction(self, tmp_path):
        stream = _convert(tmp_path, [_record(0x7C, bytes(9), position=0, k=0)])
        assert _packet_at(stream, 0)[1] == 31

    def test_instructions_land_at_recovered_positions(self, tmp_path):
        records = [
            _record(0x7B, bytes(9), position=0, k=0),
            _record(0x7B, bytes(9), position=400, k=1),
        ]
        stream = _convert(tmp_path, records)
        assert _packet_at(stream, 400)[1] == 6
        assert _packet_at(stream, 1) == bytes(PACKET)
        assert len(stream) == 401 * PACKET

    def test_colliding_positions_take_the_next_free_slot(self, tmp_path):
        records = [
            _record(0x7B, bytes(9), position=0, k=0),
            _record(0x7A, bytes(9), position=0, k=1),
        ]
        stream = _convert(tmp_path, records)
        assert _packet_at(stream, 0)[1] == 6
        assert _packet_at(stream, 1)[1] == 38

    def test_default_palette_fills_free_space_before_first_instruction(self, tmp_path):
        stream = _convert(tmp_path, [_record(0x7B, bytes(9), position=8, k=0)])
        assert [_packet_at(stream, slot)[1] for slot in (6, 7, 8)] == [30, 31, 6]

    def test_header_record_is_the_first_instruction(self, tmp_path):
        header = _record(0x7F, bytes(9), position=0, k=0, colors=0x03)
        stream = _convert(
            tmp_path, [_record(0x7B, bytes(9), position=4, k=1)], header_record=header
        )
        assert _packet_at(stream, 0)[1] == 1
        assert _packet_at(stream, 4)[1] == 6

    def test_unknown_opcodes_are_skipped(self, tmp_path):
        stream = _convert(tmp_path, [_record(0x53, bytes(9), position=0, k=0)])
        assert stream == b""

    def test_rejects_non_mcg_file(self, tmp_path):
        src = tmp_path / "song.mcg"
        src.write_bytes(b"not an mcg file at all, just some bytes")
        with pytest.raises(ValueError, match="Not a CAVS .mcg file"):
            decode_mcg(str(src))
