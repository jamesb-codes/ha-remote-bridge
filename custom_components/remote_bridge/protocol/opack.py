"""OPACK serialization.

Ported from pyatv/support/opack.py (MIT, (c) Pierre Ståhl) at pyatv b277a4c, with
type hints added. Every tag value below comes from that file.
Not implemented (same as upstream): packing absolute time (0x06) and
packing with UID references beyond what the object list dedup does.
"""

from __future__ import annotations

import struct
from typing import Any
from uuid import UUID


class SizedInt(int):
    """Int that remembers its encoded width so it re-encodes identically."""

    size: int

    def __new__(cls, value: int, size: int) -> SizedInt:
        obj = super().__new__(cls, value)
        obj.size = size
        return obj


def pack(data: Any) -> bytes:
    """Pack a Python object into OPACK bytes."""
    return _pack(data, [])


def _pack(data: Any, object_list: list[bytes]) -> bytes:
    packed: bytes
    if data is None:
        packed = b"\x04"
    elif isinstance(data, bool):
        packed = bytes([1 if data else 2])
    elif isinstance(data, UUID):
        packed = b"\x05" + data.bytes
    elif isinstance(data, int):
        size_hint = getattr(data, "size", None)
        if data < 0:
            raise ValueError("negative integers are not supported by OPACK")
        if data < 0x28 and not size_hint:
            packed = bytes([data + 8])
        elif (data <= 0xFF and not size_hint) or size_hint == 1:
            packed = b"\x30" + int(data).to_bytes(1, "little")
        elif (data <= 0xFFFF and not size_hint) or size_hint == 2:
            packed = b"\x31" + int(data).to_bytes(2, "little")
        elif (data <= 0xFFFFFFFF and not size_hint) or size_hint == 4:
            packed = b"\x32" + int(data).to_bytes(4, "little")
        elif data <= 0xFFFFFFFFFFFFFFFF:
            packed = b"\x33" + int(data).to_bytes(8, "little")
        else:
            raise ValueError("integer too large for OPACK")
    elif isinstance(data, float):
        packed = b"\x36" + struct.pack("<d", data)
    elif isinstance(data, str):
        packed = _pack_sized(data.encode("utf-8"), 0x40, _STR_SIZED)
    elif isinstance(data, (bytes, bytearray)):
        packed = _pack_sized(bytes(data), 0x70, _BYTES_SIZED)
    elif isinstance(data, list):
        packed = bytes([0xD0 + min(len(data), 0xF)]) + b"".join(_pack(x, object_list) for x in data)
        if len(data) >= 0xF:
            packed += b"\x03"
    elif isinstance(data, dict):
        packed = bytes([0xE0 + min(len(data), 0xF)]) + b"".join(
            _pack(k, object_list) + _pack(v, object_list) for k, v in data.items()
        )
        if len(data) >= 0xF:
            packed += b"\x03"
    else:
        raise TypeError(str(type(data)))

    # Object back-references, as upstream.
    if packed in object_list:
        index = object_list.index(packed)
        if index < 0x21:
            packed = bytes([0xA0 + index])
        elif index <= 0xFF:
            packed = b"\xc1" + index.to_bytes(1, "little")
        elif index <= 0xFFFF:
            packed = b"\xc2" + index.to_bytes(2, "little")
        else:
            packed = b"\xc3" + index.to_bytes(4, "little")
    elif len(packed) > 1:
        object_list.append(packed)
    return packed


# (tag, length-field width) pairs, from pyatv opack.py:62-100.
_STR_SIZED = ((0x61, 1), (0x62, 2), (0x63, 3), (0x64, 4))
_BYTES_SIZED = ((0x91, 1), (0x92, 2), (0x93, 4), (0x94, 8))


def _pack_sized(encoded: bytes, inline_base: int, sized: tuple[tuple[int, int], ...]) -> bytes:
    length = len(encoded)
    if length <= 0x20:
        return bytes([inline_base + length]) + encoded
    for tag, width in sized:
        if length < (1 << (8 * width)):
            return bytes([tag]) + length.to_bytes(width, "little") + encoded
    raise ValueError("value too long for OPACK")


def unpack(data: bytes) -> tuple[Any, bytes]:
    """Unpack OPACK bytes. Returns (object, remaining bytes)."""
    return _unpack(data, [])


def _unpack(data: bytes, object_list: list[Any]) -> tuple[Any, bytes]:
    if not data:
        raise ValueError("empty OPACK data")
    tag = data[0]
    value: Any
    add_to_object_list = True
    if tag == 0x01:
        value, remaining = True, data[1:]
        add_to_object_list = False
    elif tag == 0x02:
        value, remaining = False, data[1:]
        add_to_object_list = False
    elif tag == 0x04:
        value, remaining = None, data[1:]
        add_to_object_list = False
    elif tag == 0x05:
        value, remaining = UUID(bytes=data[1:17]), data[17:]
    elif tag == 0x06:
        # Absolute time; upstream parses it as an integer too.
        value, remaining = int.from_bytes(data[1:9], "little"), data[9:]
    elif 0x08 <= tag <= 0x2F:
        value, remaining = tag - 8, data[1:]
        add_to_object_list = False
    elif tag == 0x35:
        value, remaining = struct.unpack("<f", data[1:5])[0], data[5:]
    elif tag == 0x36:
        value, remaining = struct.unpack("<d", data[1:9])[0], data[9:]
    elif (tag & 0xF0) == 0x30:
        width = 2 ** (tag & 0xF)
        value = SizedInt(int.from_bytes(data[1 : 1 + width], "little"), width)
        remaining = data[1 + width :]
    elif 0x40 <= tag <= 0x60:
        length = tag - 0x40
        value, remaining = data[1 : 1 + length].decode("utf-8"), data[1 + length :]
    elif 0x60 < tag <= 0x64:
        width = tag & 0xF
        length = int.from_bytes(data[1 : 1 + width], "little")
        start = 1 + width
        value = data[start : start + length].decode("utf-8")
        remaining = data[start + length :]
    elif 0x70 <= tag <= 0x90:
        length = tag - 0x70
        value, remaining = bytes(data[1 : 1 + length]), data[1 + length :]
    elif 0x91 <= tag <= 0x94:
        width = 1 << ((tag & 0xF) - 1)
        length = int.from_bytes(data[1 : 1 + width], "little")
        start = 1 + width
        value, remaining = bytes(data[start : start + length]), data[start + length :]
    elif (tag & 0xF0) == 0xD0:
        count = tag & 0xF
        items: list[Any] = []
        ptr = data[1:]
        if count == 0xF:
            while ptr[0] != 0x03:
                item, ptr = _unpack(ptr, object_list)
                items.append(item)
            ptr = ptr[1:]
        else:
            for _ in range(count):
                item, ptr = _unpack(ptr, object_list)
                items.append(item)
        value, remaining = items, ptr
        add_to_object_list = False
    elif (tag & 0xE0) == 0xE0:
        count = tag & 0xF
        output: dict[Any, Any] = {}
        ptr = data[1:]
        if count == 0xF:
            while ptr[0] != 0x03:
                key, ptr = _unpack(ptr, object_list)
                output[key], ptr = _unpack(ptr, object_list)
            ptr = ptr[1:]
        else:
            for _ in range(count):
                key, ptr = _unpack(ptr, object_list)
                output[key], ptr = _unpack(ptr, object_list)
        value, remaining = output, ptr
        add_to_object_list = False
    elif 0xA0 <= tag <= 0xC0:
        value, remaining = object_list[tag - 0xA0], data[1:]
    elif 0xC1 <= tag <= 0xC4:
        width = tag - 0xC0
        uid = int.from_bytes(data[1 : 1 + width], "little")
        value, remaining = object_list[uid], data[1 + width :]
    else:
        raise TypeError(f"unknown OPACK tag {tag:#x}")

    if add_to_object_list and value not in object_list:
        object_list.append(value)
    return value, remaining
