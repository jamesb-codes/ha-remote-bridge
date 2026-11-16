"""Identity persistence, mDNS TXT records, stub listeners and idle timeouts."""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import pytest

from remote_bridge.protocol import companion_server, mdns
from remote_bridge.protocol.companion_server import CompanionServer
from remote_bridge.protocol.identity import JsonFileState, MemoryPairingStore, PairedClient, ReceiverIdentity


def test_identity_generate_and_roundtrip() -> None:
    ident = ReceiverIdentity.generate("Living Room TV")
    mac = ident.mac_bytes
    assert len(mac) == 6 and mac[0] & 0x02 and not mac[0] & 0x01
    assert len(ident.ltpk) == 32 and ident.pairing_id == ident.server_id.encode()
    assert ReceiverIdentity.from_dict(ident.to_dict()) == ident
    assert "ltsk" not in repr(ident)


def test_json_state_persists_privately(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = JsonFileState(path, "Test TV")
    state.add(PairedClient("phone-1", b"\x01" * 32, "Phone"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    again = JsonFileState(path, "ignored")
    assert again.identity == state.identity
    assert again.get("phone-1") == PairedClient("phone-1", b"\x01" * 32, "Phone")
    again.remove_all()
    assert JsonFileState(path, "x").clients == {}


def test_companion_txt() -> None:
    ident = ReceiverIdentity.generate("TV")
    txt = mdns.companion_txt(ident)
    assert set(txt) == {"rpMac", "rpHA", "rpHN", "rpVr", "rpMd", "rpFl", "rpAD", "rpHI", "rpBA"}
    assert int(txt["rpFl"], 16) & 0x4000 and not int(txt["rpFl"], 16) & 0x04
    assert txt["rpHA"] == ident.device_id.replace(":", "")
    assert len(txt["rpHN"]) == 12
    assert mdns.companion_txt(ident) == txt  # stable

    specs = mdns.service_specs(ident, 1, 2, 3)
    assert [s.service_type for s in specs] == [mdns.COMPANION_TYPE, mdns.MRP_TYPE, mdns.AIRPLAY_TYPE]
    assert specs[1].properties["Name"] == "TV" and specs[2].properties["pi"] == ident.server_id
    assert len(mdns.service_specs(ident, 1)) == 1


async def test_stub_listeners() -> None:
    stubs = mdns.StubListeners()
    mrp_port, airplay_port = await stubs.start(host="127.0.0.1")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", airplay_port)
        writer.write(b"GET /info HTTP/1.1\r\n\r\n")
        assert (await reader.read()).startswith(b"HTTP/1.1 200 OK")
        writer.close()
        await writer.wait_closed()
        # MRP stub swallows data and keeps the connection until the peer leaves.
        reader, writer = await asyncio.open_connection("127.0.0.1", mrp_port)
        writer.write(b"\x02ab")
        await writer.drain()
        await asyncio.sleep(0.05)
        assert stubs._writers  # handler is running
        writer.close()
        await writer.wait_closed()
        async with asyncio.timeout(2):
            while stubs._writers:
                await asyncio.sleep(0.01)
    finally:
        await stubs.stop()


async def test_unauthenticated_connection_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(companion_server, "AUTH_TIMEOUT", 0.2)
    server = CompanionServer(ReceiverIdentity.generate("TV"), MemoryPairingStore(), on_input=lambda raw: None)
    port = await server.start(host="127.0.0.1")
    try:
        reader, _ = await asyncio.open_connection("127.0.0.1", port)
        async with asyncio.timeout(2):
            assert await reader.read() == b""  # server closed it
    finally:
        await server.stop()


async def test_bad_frames_drop_connection() -> None:
    server = CompanionServer(ReceiverIdentity.generate("TV"), MemoryPairingStore(), on_input=lambda raw: None)
    port = await server.start(host="127.0.0.1")
    try:
        for junk in (b"\x63\x00\x00\x00", b"\x08\xff\xff\xff", b"\x03\x00\x00\x01\x07"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(junk)
            await writer.drain()
            async with asyncio.timeout(2):
                assert await reader.read() == b""
        # Encrypted message before pair-verify is ignored, connection stays up.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"\x08\x00\x00\x01\xe0")
        await writer.drain()
        await asyncio.sleep(0.1)
        assert len(server.connections) == 1
        writer.close()
    finally:
        await server.stop()


async def test_default_bind_is_single_ipv4_socket() -> None:
    """Regression: host=None + port 0 bound IPv4/IPv6 on different ports, and
    the advertised (first) port then refused IPv4 connections."""
    server = CompanionServer(ReceiverIdentity.generate("TV"), MemoryPairingStore(), on_input=lambda raw: None)
    port = await server.start()
    try:
        assert server._server is not None and len(server._server.sockets) == 1
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


def test_private_messages_are_not_logged() -> None:
    from remote_bridge.protocol.companion_server import _loggable

    body = {"_accID": "someone@example.com", "name": "Alices", "model": "iPhone17,1", "_osV": "27.0.1"}
    text = str(_loggable("_systemInfo", body))
    assert "example.com" not in text and "Alices" not in text and "27.0.1" in text
    assert "example.com" not in str(_loggable("PublishPresenceEvent", {"iCloudAltDSID": "example.com"}))
    assert _loggable("_hidC", {"_hidC": 1}) == {"_hidC": 1}
