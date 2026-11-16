"""OPACK, TLV8 and SRP, cross-checked against pyatv's own implementations."""

from __future__ import annotations

import binascii
import uuid

import pytest
from pyatv.auth.hap_srp import SRPAuthHandler
from pyatv.auth.hap_tlv8 import read_tlv as pyatv_read_tlv
from pyatv.auth.hap_tlv8 import write_tlv as pyatv_write_tlv
from pyatv.support import opack as pyatv_opack

from remote_bridge.protocol import opack, tlv8
from remote_bridge.protocol.srp import N, SrpError, SrpServer

SAMPLES = [
    None,
    True,
    False,
    0,
    0x27,
    0x28,
    0xFF,
    0x100,
    0xFFFF,
    0x10000,
    0xFFFFFFFF,
    0x100000000,
    1.5,
    "",
    "a" * 0x20,
    "b" * 0x21,
    "c" * 300,
    "ünïcødé",
    b"",
    b"\x00" * 0x20,
    b"\x01" * 0x21,
    b"\x02" * 70000,
    uuid.UUID("12345678-1234-5678-1234-567812345678"),
    [],
    [1, "two", b"three"],
    list(range(20)),
    {"_i": "_hidC", "_t": 2, "_c": {"_hBtS": 1, "_hidC": 6}, "_x": 1234},
    {f"k{i}": i for i in range(20)},
    {"dup": "same", "dup2": "same", "nested": [{"a": "same"}]},
]


@pytest.mark.parametrize("value", SAMPLES, ids=lambda v: type(v).__name__)
def test_opack_matches_pyatv(value: object) -> None:
    ours = opack.pack(value)
    assert ours == pyatv_opack.pack(value)
    assert opack.unpack(ours) == (value, b"")
    assert pyatv_opack.unpack(ours)[0] == value


def test_opack_sized_int_roundtrip() -> None:
    packed = b"\x32" + (5).to_bytes(4, "little")
    value, rest = opack.unpack(packed)
    assert value == 5 and rest == b""
    assert opack.pack(value) == packed


def test_opack_rejects_bad_input() -> None:
    with pytest.raises(TypeError):
        opack.pack(object())
    with pytest.raises(ValueError):
        opack.pack(-1)
    with pytest.raises(TypeError):
        opack.unpack(b"\x07")
    with pytest.raises(ValueError):
        opack.unpack(b"")


def test_tlv8_matches_pyatv() -> None:
    entries = {1: b"id", 3: bytes(range(256)) * 2, 6: b"\x01"}
    ours = tlv8.write(entries)
    assert ours == pyatv_write_tlv(entries)
    assert tlv8.read(ours) == pyatv_read_tlv(ours)
    assert tlv8.seq_no(tlv8.read(ours)) == 1
    # Zero-length items are legal in HAP TLV8 (pyatv just drops them).
    assert tlv8.write({0x0A: b""}) == b"\x0a\x00"
    assert tlv8.read(b"\x0a\x00") == {0x0A: b""}


def _pyatv_client(pin: str, server: SrpServer) -> tuple[SRPAuthHandler, bytes, bytes]:
    client = SRPAuthHandler()
    client.initialize()
    client.step1(pin)
    pub, proof = client.step2(server.public_key, server.salt)
    return client, pub, proof


def test_srp_against_srptools_client() -> None:
    server = SrpServer("4321")
    client, pub, proof = _pyatv_client("4321", server)
    assert server.verify(pub, proof)
    # Same session key and server proof as srptools computes.
    assert server.session_key == binascii.unhexlify(client._session.key)
    assert server.server_proof == binascii.unhexlify(client._session.key_proof_hash)


def test_srp_wrong_pin() -> None:
    server = SrpServer("4321")
    _, pub, proof = _pyatv_client("1234", server)
    assert not server.verify(pub, proof)
    with pytest.raises(SrpError):
        _ = server.session_key
    with pytest.raises(SrpError):
        _ = server.server_proof


def test_srp_rejects_zero_public_key() -> None:
    with pytest.raises(SrpError):
        SrpServer("1111").verify(N.to_bytes(384, "big"), b"x" * 64)


def test_srp_salt_and_public_key_full_width() -> None:
    for _ in range(20):
        server = SrpServer("1111")
        assert server.salt[0] != 0 and len(server.salt) == 16
        assert len(server.public_key) == 384
