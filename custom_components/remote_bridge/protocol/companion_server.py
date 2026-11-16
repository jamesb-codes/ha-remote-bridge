"""Asyncio Companion Link receiver: framing, encryption, session messages.

See docs/protocol-notes.md for where each message and reply comes from.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from enum import IntEnum
from typing import Any, TypeVar

from . import opack, tlv8
from .crypto import AUTH_TAG_LENGTH, ChaChaCipher
from .hap_pairing import PairingPolicy, PairSetupHandler, PairVerifyHandler, SessionKeys
from .hid import InputKind, RawInput, TouchTracker, decode_hid_command
from .identity import PairedClient, PairingStore, ReceiverIdentity
from .mrp_state import ReceiverState

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


class FrameType(IntEnum):
    """pyatv companion/connection.py:21-40; atv-core lib.rs:36-47."""

    NOOP = 1
    PS_START = 3
    PS_NEXT = 4
    PV_START = 5
    PV_NEXT = 6
    U_OPACK = 7
    E_OPACK = 8
    P_OPACK = 9


# Message types in "_t" (pyatv companion/protocol.py:54-60).
MSG_EVENT = 1
MSG_REQUEST = 2
MSG_RESPONSE = 3

HEADER_LENGTH = 4  # pyatv companion/connection.py:17
MAX_FRAME_LENGTH = 2 * 1024 * 1024  # atv-core server.rs:636

# Close verified connections that never start a remote session, so iOS
# background probes don't hold the channel. Values from atv-core
# session.rs:562-564 (3.0 s after verify) and session.rs:799-809
# (2.5 s after _systemInfo, 1.0 s if the xid has the high bit = probe).
SESSIONLESS_TIMEOUT_AFTER_VERIFY = 3.0
SESSIONLESS_TIMEOUT_AFTER_SYSINFO = 2.5
SESSIONLESS_TIMEOUT_PROBE = 1.0
PROBE_XID_BIT = 0x8000_0000
# Unauthenticated connections must finish pairing/verify in this time.
AUTH_TIMEOUT = 120.0

InputCallback = Callable[[RawInput], None]
SessionCallback = Callable[["ClientConnection", bool], None]
RunBlocking = Callable[..., Awaitable[Any]]


class CompanionServer:
    """One virtual TV's Companion Link listener."""

    def __init__(
        self,
        identity: ReceiverIdentity,
        store: PairingStore,
        *,
        on_input: InputCallback,
        policy: PairingPolicy | None = None,
        on_session_change: SessionCallback | None = None,
        run_blocking: RunBlocking | None = None,
        swipe_threshold: float = 150.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.identity = identity
        self.store = store
        self.policy = policy or PairingPolicy()
        self.state = ReceiverState()
        self.swipe_threshold = swipe_threshold
        self._on_input = on_input
        self._on_session_change = on_session_change
        self._run_blocking: RunBlocking = run_blocking or asyncio.to_thread
        self._clock = clock
        self._server: asyncio.Server | None = None
        self._stopping = False
        self._connections: set[ClientConnection] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def port(self) -> int | None:
        if self._server is None or not self._server.sockets:
            return None
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def connections(self) -> list[ClientConnection]:
        return list(self._connections)

    @property
    def active_sessions(self) -> list[ClientConnection]:
        return [c for c in self._connections if c.remote_session_active]

    async def start(self, host: str = "0.0.0.0", port: int = 0) -> int:
        """Listen on one IPv4 socket and return its port.

        Not ``host=None``: with port 0 asyncio would bind IPv4 and IPv6 on
        *different* random ports, and we advertise only one of them.
        """
        self._stopping = False
        self._server = await asyncio.start_server(self._accept, host=host, port=port)
        bound = self.port
        assert bound is not None
        _LOGGER.debug("Companion server for %s listening on port %s", self.identity.name, bound)
        return bound

    async def stop(self) -> None:
        """Stop listening and close every connection.

        Deliberately no ``Server.wait_closed()``: a socket accepted just before
        ``close()`` can attach afterwards, and CPython 3.13 then raises in
        ``Server._wakeup`` (unraisable TypeError). We wait on our own handler
        tasks instead, and handlers that start after stop close immediately.
        """
        self._stopping = True
        if self._server is not None:
            self._server.close()
            self._server = None
        for conn in list(self._connections):
            conn.close("server stopping")
        if self._tasks:
            _, pending = await asyncio.wait(list(self._tasks), timeout=2)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        await _drain_late_accepts()

    def disconnect_all(self) -> None:
        """Drop every connection (e.g. after forgetting paired devices)."""
        for conn in list(self._connections):
            conn.close("disconnected by receiver")

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._stopping:
            writer.close()
            return
        conn = ClientConnection(self, reader, writer)
        # A new connection from an IP supersedes that IP's connections that never
        # got a remote session (atv-core registry.rs:147-176).
        for other in list(self._connections):
            if other.peer_ip == conn.peer_ip and not other.remote_session_active:
                other.close("superseded by new connection")
        self._connections.add(conn)
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await conn.run()
        finally:
            self._connections.discard(conn)
            if task is not None:
                self._tasks.discard(task)

    # Hooks used by ClientConnection
    def _emit(self, raw: RawInput) -> None:
        try:
            self._on_input(raw)
        except Exception:  # never let a callback kill the session
            _LOGGER.exception("Input callback failed")

    def _session_changed(self, conn: ClientConnection, active: bool) -> None:
        if active and conn.client is not None:
            # One live session per paired phone (atv-core registry.rs:178-191).
            for other in list(self._connections):
                if (
                    other is not conn
                    and other.client is not None
                    and other.client.client_id == conn.client.client_id
                ):
                    other.close("superseded by same device")
        if self._on_session_change is not None:
            try:
                self._on_session_change(conn, active)
            except Exception:
                _LOGGER.exception("Session callback failed")


class ClientConnection:
    """State machine for one TCP connection from a phone."""

    def __init__(
        self, server: CompanionServer, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._server = server
        self._reader = reader
        self._writer = writer
        peer = writer.get_extra_info("peername") or ("?", 0)
        self.peer_ip: str = str(peer[0])
        self.peer: str = f"{peer[0]}:{peer[1]}"
        self.client: PairedClient | None = None
        self.remote_session_active = False
        self._cipher: ChaChaCipher | None = None
        self._setup = PairSetupHandler(server.identity, server.store, server.policy)
        self._verify = PairVerifyHandler(server.identity, server.store)
        self._touch = TouchTracker(swipe_min=server.swipe_threshold)
        self._xid = secrets.randbelow(2**16)
        self._deadline: float | None = time.monotonic() + AUTH_TIMEOUT
        self._closing: str | None = None
        self._write_lock = asyncio.Lock()

    @property
    def client_name(self) -> str:
        if self.client is not None and self.client.name:
            return self.client.name
        return self.peer

    @property
    def verified(self) -> bool:
        return self._cipher is not None

    def close(self, reason: str) -> None:
        if self._closing is None:
            self._closing = reason
            _LOGGER.debug("Closing %s: %s", self.peer, reason)
        self._writer.close()

    async def run(self) -> None:
        _LOGGER.debug("Companion connection from %s", self.peer)
        reason = "peer closed"
        try:
            while True:
                header = await self._read_exactly(HEADER_LENGTH)
                if header is None:
                    break
                length = int.from_bytes(header[1:4], "big")
                if length > MAX_FRAME_LENGTH:
                    reason = f"frame too large ({length})"
                    break
                payload = await self._read_exactly(length) if length else b""
                if payload is None:
                    break
                await self._handle_frame(header, payload)
        except (ConnectionError, asyncio.IncompleteReadError):
            reason = "connection error"
        except _ProtocolViolation as err:
            reason = str(err)
        finally:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            _LOGGER.debug("Connection %s closed: %s", self.peer, self._closing or reason)
            if self.remote_session_active:
                self.remote_session_active = False
                self._server._session_changed(self, False)

    async def _read_exactly(self, count: int) -> bytes | None:
        """Read ``count`` bytes, honouring the current deadline."""
        while True:
            timeout = None if self._deadline is None else self._deadline - time.monotonic()
            if timeout is not None and timeout <= 0:
                self.close("timed out without a remote session")
                return None
            try:
                return await asyncio.wait_for(self._reader.readexactly(count), timeout)
            except TimeoutError:
                continue  # loop re-checks the (possibly cleared) deadline
            except asyncio.IncompleteReadError:
                return None

    # ---- frames -----------------------------------------------------------

    async def _handle_frame(self, header: bytes, payload: bytes) -> None:
        try:
            frame_type = FrameType(header[0])
        except ValueError as err:
            raise _ProtocolViolation(f"unknown frame type {header[0]}") from err
        if self._cipher is not None and payload:
            try:
                payload = self._cipher.decrypt(payload, aad=header)
            except Exception as err:
                raise _ProtocolViolation("decryption failed") from err
        if not payload or frame_type == FrameType.NOOP:
            return
        try:
            message, _ = opack.unpack(payload)
        except (TypeError, ValueError, IndexError, UnicodeDecodeError) as err:
            raise _ProtocolViolation("bad OPACK payload") from err
        if not isinstance(message, dict):
            return

        if frame_type in (FrameType.PS_START, FrameType.PS_NEXT):
            await self._handle_pair_setup(frame_type, message)
        elif frame_type in (FrameType.PV_START, FrameType.PV_NEXT):
            await self._handle_pair_verify(message)
        elif frame_type == FrameType.E_OPACK:
            if self._cipher is None:
                _LOGGER.debug("Dropping E_OPACK before pair-verify from %s", self.peer)
                return
            await self._handle_message(message)
        else:
            _LOGGER.debug("Ignoring frame %s from %s", frame_type.name, self.peer)

    async def _send_frame(self, frame_type: FrameType, message: dict[str, Any]) -> None:
        data = opack.pack(message)
        async with self._write_lock:
            length = len(data) + (AUTH_TAG_LENGTH if self._cipher and data else 0)
            header = bytes([frame_type]) + length.to_bytes(3, "big")
            if self._cipher is not None and data:
                data = self._cipher.encrypt(data, aad=header)
            self._writer.write(header + data)
            await self._writer.drain()

    async def _blocking(self, func: Callable[..., T], *args: Any) -> T:
        result: T = await self._server._run_blocking(func, *args)
        return result

    # ---- pairing ----------------------------------------------------------

    async def _handle_pair_setup(self, frame_type: FrameType, message: dict[str, Any]) -> None:
        pairing_data = message.get("_pd")
        if not isinstance(pairing_data, bytes):
            return
        tlv = tlv8.read(pairing_data)
        if frame_type == FrameType.PS_START:
            _LOGGER.info("Pairing request from %s", self.peer)
        reply = await self._blocking(self._setup.handle, tlv)
        policy = self._server.policy
        if self._setup.pending_pin is not None:
            policy.on_pin(self._setup.pending_pin)
        if self._setup.new_client is not None:
            # Store before M6 so an immediate pair-verify finds the key.
            self._server.store.add(self._setup.new_client)
            policy.on_paired(self._setup.new_client)
        await self._send_frame(FrameType.PS_NEXT, reply)

    async def _handle_pair_verify(self, message: dict[str, Any]) -> None:
        pairing_data = message.get("_pd")
        if not isinstance(pairing_data, bytes):
            return
        reply, keys = await self._blocking(self._verify.handle, tlv8.read(pairing_data))
        await self._send_frame(FrameType.PV_NEXT, reply)
        if keys is not None:
            self._enable_encryption(keys)
        elif tlv8.Tag.ERROR in tlv8.read(reply["_pd"]):
            # Some clients ignore the M4 error and would wait for a timeout.
            self.close("pair-verify failed")

    def _enable_encryption(self, keys: SessionKeys) -> None:
        self._cipher = ChaChaCipher(keys.output_key, keys.input_key, counter_bytes=12)
        self.client = keys.client
        self._deadline = time.monotonic() + SESSIONLESS_TIMEOUT_AFTER_VERIFY
        _LOGGER.debug("Verified %s as %s", self.peer, self.client_name)

    # ---- session messages -------------------------------------------------

    def _next_xid(self) -> int:
        self._xid += 1
        return self._xid

    async def _respond(self, identifier: str, xid: Any, content: dict[str, Any] | None = None) -> None:
        # Shape from atv-core session.rs:210-228 (incl. "_rT": 0 result type).
        response: dict[str, Any] = {"_c": content or {}, "_rT": 0, "_t": MSG_RESPONSE, "_i": identifier}
        if xid is not None:
            response["_x"] = xid
        await self._send_frame(FrameType.E_OPACK, response)

    async def _event(self, identifier: str, content: dict[str, Any], xid: Any = None) -> None:
        # atv-core session.rs:230-245 reuses the triggering request's xid.
        message = {
            "_i": identifier,
            "_t": MSG_EVENT,
            "_c": content,
            "_x": xid if xid is not None else self._next_xid(),
        }
        await self._send_frame(FrameType.E_OPACK, message)

    async def _push_media_capabilities(self, xid: Any = None) -> None:
        """Volume capability, both wire formats (atv-core session.rs:247-269)."""
        state = self._server.state
        await self._event("MediaControlStatus", state.media_control_status(), xid)
        await self._event("_iMC", state.imc(), xid)

    def _mark_session_active(self) -> None:
        self._deadline = None
        if not self.remote_session_active:
            self.remote_session_active = True
            self._server._session_changed(self, True)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        # iOS 27 may put the identifier in "model" (atv-core session.rs:572-578).
        identifier = message.get("_i") or message.get("model")
        if not isinstance(identifier, str):
            return
        xid = message.get("_x")
        is_request = message.get("_t") == MSG_REQUEST
        content = message.get("_c") if isinstance(message.get("_c"), dict) else {}
        assert isinstance(content, dict)
        now = self._server._clock()
        state = self._server.state
        reply: dict[str, Any] | None = None
        _LOGGER.debug("<< %s %s", identifier, _loggable(identifier, content))

        if identifier == "_systemInfo" or identifier == "SystemInfo":
            if is_request:
                await self._respond(identifier, xid)
            await self._event("SystemStatus", state.system_status_event())
            await self._event("TVSystemStatus", state.system_status_event())
            if not self.remote_session_active:
                probe = isinstance(xid, int) and bool(xid & PROBE_XID_BIT)
                timeout = SESSIONLESS_TIMEOUT_PROBE if probe else SESSIONLESS_TIMEOUT_AFTER_SYSINFO
                self._deadline = time.monotonic() + timeout
            return
        if identifier == "_sessionStart":
            self._mark_session_active()
            reply = {"_sid": secrets.randbits(32)}  # atv-core session.rs:584-610
        elif identifier == "TVRCSessionStart":
            self._mark_session_active()
            version = content.get("ProtocolVersionKey", "1.2")
            await self._respond(identifier, xid, {"ProtocolVersionKey": version})
            # "Current iOS versions make the volume/mute decision immediately
            # after session startup" (atv-core session.rs:629-632).
            await self._push_media_capabilities(xid)
            return
        elif identifier == "_sessionStop":
            reply = {}
            if self.remote_session_active:
                self.remote_session_active = False
                self._server._session_changed(self, False)
        elif identifier == "_hidC":
            self._mark_session_active()
            raw = decode_hid_command(content, now, self.client_name)
            if is_request:
                await self._respond(identifier, xid)
            if raw is not None:
                self._server._emit(raw)
                if raw.name in ("volume_up", "volume_down", "mute") and not raw.pressed:
                    # Refresh the phone's volume HUD (atv-core session.rs:271-292,850).
                    await self._push_media_capabilities(xid)
            return
        elif identifier == "_hidT":
            self._mark_session_active()
            self._touch.swipe_min = self._server.swipe_threshold
            raw = self._touch.feed(content, now, self.client_name)
            if is_request:
                await self._respond(identifier, xid)
            if raw is not None:
                self._server._emit(raw)
            return
        elif identifier == "_touchStart":
            self._mark_session_active()
            reply = {"_i": 1}  # atv-core session.rs:651-656
        elif identifier in ("_touchStop", "_touchMove", "_tiStop", "TVRCSessionStop"):
            reply = {}
        elif identifier == "_tiStart":
            reply = {"_tiE": False}  # atv-core session.rs:726-733
        elif identifier == "_interest":
            await self._handle_interest(content, xid)
            return
        elif identifier == "FetchMediaControlStatus":
            reply = state.media_control_status()
        elif identifier == "FetchAttentionState":
            reply = state.system_status_event()
        elif identifier in ("_mcc", "MediaControlCommand"):
            reply, raw = state.handle_media_command(content, now, self.client_name)
            if is_request:
                await self._respond(identifier, xid, reply)
            if raw is not None:
                self._server._emit(raw)
            return
        elif identifier == "_tiC":
            return  # keyboard text payloads; not decoded yet (unknown #4)
        else:
            _LOGGER.debug("Unhandled message %s", identifier)
            reply = {}

        if is_request:
            await self._respond(identifier, xid, reply)

    async def _handle_interest(self, content: dict[str, Any], xid: Any) -> None:
        """Push current state for each newly registered event (atv-core session.rs:668-708)."""
        events = content.get("_regEvents")
        if not isinstance(events, list):
            return
        state = self._server.state
        for name in events:
            if name == "MediaControlStatus":
                await self._event(name, state.media_control_status(), xid)
            elif name == "_iMC":
                await self._event(name, state.imc(), xid)
            elif name in ("SystemStatus", "TVSystemStatus"):
                await self._event(name, state.system_status_event(), xid)
            elif name == "NowPlayingInfo":
                await self._event(name, {}, xid)


# Messages whose bodies carry personal data (iCloud account, Siri profile,
# installed apps, account IDs). Seen from iOS 27.0.1; never log their content.
_PRIVATE_MESSAGES = frozenset(
    {"_systemInfo", "SystemInfo", "PublishPresenceEvent", "SwitchActiveUserAccountEvent"}
)
_SAFE_SYSTEMINFO_KEYS = ("model", "_osV", "_sv")


def _loggable(identifier: str, content: dict[str, Any]) -> Any:
    """Message content that is safe to put in a debug log."""
    if identifier == "_hidT":
        return ""  # very chatty; the decoded result is logged instead
    if identifier in _PRIVATE_MESSAGES:
        safe = {k: content[k] for k in _SAFE_SYSTEMINFO_KEYS if k in content}
        return f"<redacted; {safe}>" if safe else "<redacted>"
    return content


class _ProtocolViolation(Exception):
    """Unrecoverable framing/crypto error: drop the connection."""


async def _drain_late_accepts() -> None:
    """Give sockets accepted just before close() a few loop ticks to reach our
    handler (which closes them while stopping), so their transports are closed
    rather than garbage-collected while still attached to the closed server."""
    for _ in range(3):
        await asyncio.sleep(0.01)


__all__ = [
    "ClientConnection",
    "CompanionServer",
    "FrameType",
    "InputKind",
    "RawInput",
]
