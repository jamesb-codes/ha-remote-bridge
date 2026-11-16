"""TLV8 encoding used inside Companion pairing messages."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import IntEnum
from typing import Any


class Tag(IntEnum):
    """TLV tags. From pyatv/auth/hap_tlv8.py:13-33 (TlvValue)."""

    METHOD = 0x00
    IDENTIFIER = 0x01
    SALT = 0x02
    PUBLIC_KEY = 0x03
    PROOF = 0x04
    ENCRYPTED_DATA = 0x05
    SEQ_NO = 0x06
    ERROR = 0x07
    SIGNATURE = 0x0A
    EXTRA_INFO = 0x11  # pyatv "Name": OPACK dict (pyatv hap_srp.py:192-197)
    # Sent in pair-setup M2 alongside _pwTy=1. Meaning undocumented.
    # Source: pyatv companion/server_auth.py:150, atv-core session.rs:376.
    FLAGS_27 = 27


class ErrorCode(IntEnum):
    """From pyatv/auth/hap_tlv8.py:43-52."""

    UNKNOWN = 0x01
    AUTHENTICATION = 0x02
    BACKOFF = 0x03
    MAX_PEERS = 0x04
    MAX_TRIES = 0x05
    UNAVAILABLE = 0x06
    BUSY = 0x07


def read(data: bytes) -> dict[int, bytes]:
    """Parse TLV8. Repeated tags (values > 255 bytes) are concatenated."""
    result: dict[int, bytes] = {}
    pos = 0
    while pos + 2 <= len(data):
        tag = data[pos]
        length = data[pos + 1]
        value = data[pos + 2 : pos + 2 + length]
        result[tag] = result.get(tag, b"") + value
        pos += 2 + length
    return result


def write(entries: Mapping[Any, bytes] | Iterable[tuple[int, bytes]]) -> bytes:
    """Encode TLV8, splitting values longer than 255 bytes into chunks."""
    items = entries.items() if isinstance(entries, Mapping) else entries
    out = bytearray()
    for tag, value in items:
        if not value:
            out += bytes([int(tag), 0])
            continue
        for pos in range(0, len(value), 255):
            chunk = value[pos : pos + 255]
            out += bytes([int(tag), len(chunk)]) + chunk
    return bytes(out)


def seq_no(tlv: Mapping[int, bytes]) -> int:
    """Return the SeqNo (state) of a parsed TLV. pyatv server_auth.py:90."""
    return int.from_bytes(tlv.get(Tag.SEQ_NO, b"\x00"), "little")
