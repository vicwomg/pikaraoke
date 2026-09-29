"""Decode CAVS .mcg karaoke graphics into standard CD+G.

CAVS libraries pair each .mp3 with a proprietary .mcg instead of a .cdg, which
ffmpeg cannot read. An .mcg is a .cdg with the timing filler stripped and each
remaining instruction packed into a 16-byte record, so it decodes losslessly
back into the CD+G stream it came from.

Vendored from https://github.com/swordfish6975/mcg2cdg (MIT), whose module
docstring holds the full format description and how it was verified.
"""

import logging
import struct

MAGIC = b"CAVSMC"
HEADER_SIZE = 32
RECORD_SIZE = 16
CDG_PACKET_SIZE = 24

OP_TILE_XOR = 0x7A
OP_TILE_NORMAL = 0x7B
OP_PALETTE_HIGH = 0x7C
OP_PALETTE_LOW = 0x7D
OP_MEMORY_PRESET = 0x7F

CDG_COMMAND = 0x09
CDG_MEMORY_PRESET = 1
CDG_TILE_NORMAL = 6
CDG_LOAD_COLOR_LOW = 30
CDG_LOAD_COLOR_HIGH = 31
CDG_TILE_XOR = 38

# Written ahead of the first instruction when there is room, in case the file's
# own colour table comes late.
DEFAULT_PALETTE = [
    (0, 0, 0),
    (15, 15, 15),
    (0, 0, 10),
    (0, 10, 0),
    (10, 0, 0),
    (10, 10, 0),
    (0, 10, 10),
    (10, 0, 10),
    (5, 5, 5),
    (12, 12, 12),
    (0, 0, 15),
    (0, 15, 0),
    (15, 0, 0),
    (15, 15, 0),
    (0, 15, 15),
    (15, 0, 15),
]

# A colour record's 24 payload nibbles, as (r, g, b) nibble indices per colour
# slot. Slots 3-7 are in order; the encoder shuffles slots 0-2.
NIBBLE_ORDER = [
    (2, 3, 0),
    (1, 6, 7),
    (4, 5, 8),
    (9, 10, 11),
    (12, 13, 14),
    (15, 16, 17),
    (18, 19, 20),
    (21, 22, 23),
]

# Twelve MSB-first pixel bits are exactly two six-bit CD+G tile rows, so one
# table repacks a whole tile in six lookups.
TILE_ROWS = [bytes(((v >> 6) & 0x3F, v & 0x3F)) for v in range(4096)]


def _packet(instruction: int, data: bytes | bytearray) -> bytes:
    """A 24-byte CD+G packet: command, instruction, two parity bytes, 16 data bytes."""
    return bytes((CDG_COMMAND, instruction, 0, 0)) + bytes(data) + bytes(4)


def _palette_packet(instruction: int, colors: list[tuple[int, int, int]]) -> bytes:
    data = bytearray(16)
    for i, (r, g, b) in enumerate(colors):
        data[i * 2] = (r << 2) | (g >> 2)
        data[i * 2 + 1] = ((g & 0x03) << 4) | b
    return _packet(instruction, data)


def _tile_rows(record: bytes) -> bytes:
    """The tile's 72 pixel bits, stored across bytes 2 and 5-12, as 12 CD+G rows."""
    bits = int.from_bytes(record[2:3] + record[5:13], "big")
    return b"".join(TILE_ROWS[(bits >> shift) & 0xFFF] for shift in (60, 48, 36, 24, 12, 0))


def _decode_record(record: bytes) -> bytes | None:
    """Translate one record into a CD+G packet, or None for records with no drawing."""
    opcode = record[4]
    if opcode in (OP_TILE_XOR, OP_TILE_NORMAL):
        header = bytes((record[1] & 0x0F, record[1] >> 4, record[0] & 0x3F, record[3] & 0x3F))
        instruction = CDG_TILE_XOR if opcode == OP_TILE_XOR else CDG_TILE_NORMAL
        return _packet(instruction, header + _tile_rows(record))
    if opcode == OP_MEMORY_PRESET:
        return _packet(CDG_MEMORY_PRESET, bytes((record[1] & 0x0F,)) + bytes(15))
    if opcode in (OP_PALETTE_LOW, OP_PALETTE_HIGH):
        nibbles = []
        for byte in record[0:4] + record[5:13]:
            nibbles += (byte >> 4, byte & 0x0F)
        colors = [tuple(nibbles[i] for i in order) for order in NIBBLE_ORDER]
        instruction = CDG_LOAD_COLOR_LOW if opcode == OP_PALETTE_LOW else CDG_LOAD_COLOR_HIGH
        return _palette_packet(instruction, colors)
    return None


def _read_records(mcg_path: str) -> list[bytes]:
    with open(mcg_path, "rb") as handle:
        blob = handle.read()
    if len(blob) < HEADER_SIZE or not blob.startswith(MAGIC):
        raise ValueError(f"Not a CAVS .mcg file: {mcg_path}")

    body = blob[HEADER_SIZE:]
    declared = struct.unpack_from("<I", blob, 12)[0]
    if declared and declared != len(body):
        logging.warning(f"{mcg_path}: header declares {declared} record bytes, found {len(body)}")
    records = [
        body[i : i + RECORD_SIZE] for i in range(0, len(body) - RECORD_SIZE + 1, RECORD_SIZE)
    ]

    # The header's last 16 bytes are a real instruction, often the starting
    # palette, so dropping it loses the song's colours.
    header_record = blob[16:HEADER_SIZE]
    if header_record[4] in (
        OP_TILE_XOR,
        OP_TILE_NORMAL,
        OP_PALETTE_LOW,
        OP_PALETTE_HIGH,
        OP_MEMORY_PRESET,
    ):
        records.insert(0, header_record)
    return records


def decode_mcg(mcg_path: str) -> bytes:
    """Decode an .mcg file into the CD+G stream it was made from.

    The stream ends at the last instruction rather than at the end of the song,
    which ffmpeg handles by holding the final frame, as it does for any .cdg.

    Raises:
        ValueError: The file is not an .mcg.
    """
    packets = []
    slots = []
    for k, record in enumerate(_read_records(mcg_path)):
        packet = _decode_record(record)
        if packet is None:
            continue
        packets.append(packet)
        # The trailer is the original packet position in 4-packet units,
        # XORed with a running record counter.
        slots.append(((record[14] | (record[15] << 8)) ^ ((256 + k) & 0xFFFF)) << 2)

    lead = [
        _palette_packet(CDG_LOAD_COLOR_LOW, DEFAULT_PALETTE[:8]),
        _palette_packet(CDG_LOAD_COLOR_HIGH, DEFAULT_PALETTE[8:]),
    ]
    first = min(slots, default=0)
    # Only where there is free space, so no instruction is pushed off its time
    if first >= len(lead):
        packets = lead + packets
        slots = [first - len(lead) + i for i in range(len(lead))] + slots

    # Two instructions can recover the same position; the later one takes the
    # next free slot rather than overwriting.
    offsets = []
    written = 0
    for slot in slots:
        written = max(written, slot)
        offsets.append(written * CDG_PACKET_SIZE)
        written += 1

    # An all-zero packet is CD+G filler, so untouched slots need no writing
    stream = bytearray(written * CDG_PACKET_SIZE)
    for offset, packet in zip(offsets, packets):
        stream[offset : offset + CDG_PACKET_SIZE] = packet
    return bytes(stream)
