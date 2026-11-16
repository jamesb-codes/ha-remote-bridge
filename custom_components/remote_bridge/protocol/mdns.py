"""mDNS service definitions (no network I/O; the host registers them).

TXT records from atv-core server.rs:224-299. Rationale for each in
docs/protocol-notes.md section 1.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .hap_pairing import DEVICE_MODEL
from .identity import ReceiverIdentity

COMPANION_TYPE = "_companion-link._tcp.local."
MRP_TYPE = "_mediaremotetv._tcp.local."
AIRPLAY_TYPE = "_airplay._tcp.local."

SOURCE_VERSION = "715.2"  # atv-core server.rs:33 (rpVr / srcvers)
# rpFl: 0x4000 = PIN pairing supported, 0x04 clear = pairing enabled
# (pyatv companion/__init__.py:60,79). Exact value from atv-core server.rs:278.
COMPANION_FLAGS = "0x36782"
SYSTEM_BUILD_VERSION = "22K160"  # atv-core server.rs:263
AIRPLAY_FEATURES = "0x5A7FFFF7,0x1E"  # atv-core server.rs:289
AIRPLAY_FLAGS = "0x44"  # atv-core server.rs:290


def _hash_hex(label: str, identity: ReceiverIdentity, length: int) -> str:
    digest = hashlib.sha256(f"{label}:".encode() + identity.server_id.encode()).digest()
    return digest[:length].hex()


def companion_txt(identity: ReceiverIdentity) -> dict[str, str]:
    return {
        "rpMac": "1",
        "rpHA": identity.device_id.replace(":", "").upper(),
        "rpHN": _hash_hex("rpHN", identity, 6).upper(),
        "rpVr": SOURCE_VERSION,
        "rpMd": DEVICE_MODEL,
        "rpFl": COMPANION_FLAGS,
        "rpAD": _hash_hex("rpAD", identity, 6).upper(),
        "rpHI": _hash_hex("rpHI", identity, 6).upper(),
        "rpBA": identity.device_id,
    }


def mrp_txt(identity: ReceiverIdentity) -> dict[str, str]:
    return {
        "Name": identity.name,
        "UniqueIdentifier": identity.server_id.replace("-", "").upper(),
        "SystemBuildVersion": SYSTEM_BUILD_VERSION,
        "LocalAirPlayReceiverPairingIdentity": _hash_hex("airplay-identity", identity, 8).upper(),
        "ModelName": "Apple TV",
        "AllowPairing": "YES",
    }


def airplay_txt(identity: ReceiverIdentity) -> dict[str, str]:
    return {
        "deviceid": identity.device_id,
        "features": AIRPLAY_FEATURES,
        "flags": AIRPLAY_FLAGS,
        "model": DEVICE_MODEL,
        "srcvers": SOURCE_VERSION,
        "vv": "2",
        "pi": identity.server_id,
        "pk": _hash_hex("pk", identity, 16),
        "name": identity.name,
    }


@dataclass(frozen=True)
class ServiceSpec:
    service_type: str
    port: int
    properties: dict[str, str]


def service_specs(
    identity: ReceiverIdentity,
    companion_port: int,
    mrp_port: int | None = None,
    airplay_port: int | None = None,
) -> list[ServiceSpec]:
    """Services to advertise. MRP/AirPlay only if a stub port is given."""
    specs = [ServiceSpec(COMPANION_TYPE, companion_port, companion_txt(identity))]
    if mrp_port is not None:
        specs.append(ServiceSpec(MRP_TYPE, mrp_port, mrp_txt(identity)))
    if airplay_port is not None:
        specs.append(ServiceSpec(AIRPLAY_TYPE, airplay_port, airplay_txt(identity)))
    return specs


class StubListeners:
    """Discovery-only listeners for the optional MRP and AirPlay adverts.

    MRP: read and discard (atv-core server.rs:1098-1112). AirPlay: answer any
    request with ``200 OK`` (atv-core server.rs:1148-1157).
    """

    def __init__(self) -> None:
        self._servers: list[asyncio.Server] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._stopping = False

    async def start(self, host: str = "0.0.0.0") -> tuple[int, int]:
        self._stopping = False
        mrp = await asyncio.start_server(self._tracked(self._mrp), host=host, port=0)
        airplay = await asyncio.start_server(self._tracked(self._airplay), host=host, port=0)
        self._servers = [mrp, airplay]
        return (
            int(mrp.sockets[0].getsockname()[1]),
            int(airplay.sockets[0].getsockname()[1]),
        )

    async def stop(self) -> None:
        """Close listeners and connections (no wait_closed(): see CompanionServer.stop)."""
        self._stopping = True
        for server in self._servers:
            server.close()
        self._servers = []
        for writer in list(self._writers):
            writer.close()
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=2)
        for _ in range(3):  # let late accepts reach the handler and close
            await asyncio.sleep(0.01)

    def _tracked(
        self, handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]
    ) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]:
        async def wrapper(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            if task is not None:
                self._tasks.add(task)
            self._writers.add(writer)
            try:
                if not self._stopping:
                    with contextlib.suppress(Exception):
                        await handler(reader, writer)
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                self._writers.discard(writer)
                if task is not None:
                    self._tasks.discard(task)

        return wrapper

    @staticmethod
    async def _mrp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while await reader.read(4096):
            pass

    @staticmethod
    async def _airplay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.wait_for(reader.read(4096), 5)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
