"""End to end: pyatv's real Companion client against our receiver."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import pyatv
import pytest
from pyatv import conf, exceptions
from pyatv.const import InputAction, Protocol

from remote_bridge.protocol import (
    CompanionServer,
    InputKind,
    MemoryPairingStore,
    PairedClient,
    PairingPolicy,
    RawInput,
    ReceiverIdentity,
)


@dataclass
class Harness:
    server: CompanionServer
    store: MemoryPairingStore
    port: int
    inputs: list[RawInput] = field(default_factory=list)
    pins: list[str] = field(default_factory=list)
    paired: list[PairedClient] = field(default_factory=list)
    sessions: list[bool] = field(default_factory=list)
    allow_pairing: bool = True

    def config(self, credentials: str | None = None) -> conf.AppleTV:
        config = conf.AppleTV("127.0.0.1", "Test TV")
        config.add_service(
            conf.ManualService("test-tv", Protocol.Companion, self.port, {}, credentials=credentials)
        )
        return config

    async def wait_for(self, predicate: Callable[[], bool], timeout: float = 3.0) -> None:
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.01)

    def names(self) -> list[str]:
        return [str(raw) for raw in self.inputs]


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    store = MemoryPairingStore()
    holder: dict[str, Harness] = {}
    policy = PairingPolicy(
        allow_pairing=lambda: holder["h"].allow_pairing,
        on_pin=lambda pin: holder["h"].pins.append(pin),
        on_paired=lambda client: holder["h"].paired.append(client),
    )
    server = CompanionServer(
        ReceiverIdentity.generate("Test TV"),
        store,
        on_input=lambda raw: holder["h"].inputs.append(raw),
        policy=policy,
        on_session_change=lambda conn, active: holder["h"].sessions.append(active),
    )
    port = await server.start(host="127.0.0.1")
    holder["h"] = Harness(server, store, port)
    yield holder["h"]
    await server.stop()


async def pair(h: Harness, pin_override: str | None = None) -> str:
    loop = asyncio.get_running_loop()
    handler = await pyatv.pair(h.config(), Protocol.Companion, loop, name="pytest phone")
    try:
        await handler.begin()
        await h.wait_for(lambda: bool(h.pins))
        handler.pin(int(pin_override or h.pins[-1]))
        await handler.finish()
        assert handler.has_paired
        return str(handler.service.credentials)
    finally:
        await handler.close()


async def test_pair_then_control(harness: Harness) -> None:
    h = harness
    credentials = await pair(h)
    assert len(h.pins) == 1 and 1000 <= int(h.pins[0]) <= 9999
    assert [c.name for c in h.paired] == ["pytest phone"]
    assert len(h.store.clients) == 1

    atv = await pyatv.connect(h.config(credentials), asyncio.get_running_loop())
    try:
        await h.wait_for(lambda: h.sessions == [True])
        rc = atv.remote_control
        for press in (rc.up, rc.down, rc.left, rc.right, rc.select, rc.menu, rc.home, rc.play_pause):
            await press()
        await atv.audio.volume_up()  # waits for our _iMC push + GetVolume reply
        await atv.audio.volume_down()
        await rc.home(InputAction.Hold)
        await rc.skip_forward()
        await atv.touch.swipe(500, 800, 500, 100, 150)
        await atv.touch.click(InputAction.SingleTap)

        expected = [
            *(
                f"button {n} {s}"
                for n in (
                    "up",
                    "down",
                    "left",
                    "right",
                    "select",
                    "menu",
                    "home",
                    "play_pause",
                    "volume_up",
                    "volume_down",
                    "home",
                )
                for s in ("down", "up")
            ),
            "media skip_by 10.0",
            "swipe up",
            "button select down",
            "button select up",
        ]
        await h.wait_for(lambda: len(h.inputs) >= len(expected))
        assert h.names() == expected
        assert all(raw.client == "pytest phone" for raw in h.inputs)

        # Hold really held: down and up are ~1 s apart.
        home_down, home_up = [r for r in h.inputs if r.name == "home"][2:4]
        assert home_up.timestamp - home_down.timestamp >= 0.9
    finally:
        await asyncio.gather(*atv.close())
    await h.wait_for(lambda: h.sessions == [True, False])


async def test_power_and_attention_state(harness: Harness) -> None:
    h = harness
    credentials = await pair(h)
    atv = await pyatv.connect(h.config(credentials), asyncio.get_running_loop())
    try:
        assert atv.power.power_state == pyatv.const.PowerState.On  # from FetchAttentionState
        await atv.power.turn_off()  # _hidC Sleep, up only (pyatv companion/__init__.py:287-292)
        await h.wait_for(lambda: bool(h.inputs))
        assert h.names() == ["button sleep up"]
    finally:
        await asyncio.gather(*atv.close())


async def test_wrong_pin_is_rejected(harness: Harness) -> None:
    h = harness
    with pytest.raises(exceptions.PairingError):
        await pair(h, pin_override="0000")
    assert h.store.clients == {}
    assert h.server.policy.failed_attempts == 1


async def test_pairing_refused_when_not_allowed(harness: Harness) -> None:
    h = harness
    h.allow_pairing = False
    loop = asyncio.get_running_loop()
    handler = await pyatv.pair(h.config(), Protocol.Companion, loop)
    try:
        with pytest.raises(exceptions.PairingError):
            await handler.begin()
    finally:
        await handler.close()
    assert h.pins == []


async def test_unpaired_client_cannot_connect(harness: Harness) -> None:
    h = harness
    credentials = await pair(h)
    h.store.remove_all()  # "forget paired devices"
    with pytest.raises((exceptions.ProtocolError, exceptions.ConnectionFailedError, asyncio.TimeoutError)):
        atv = await pyatv.connect(h.config(credentials), asyncio.get_running_loop())
        await asyncio.gather(*atv.close())
    assert h.inputs == [] and h.sessions == []


async def test_forged_credentials_cannot_connect(harness: Harness) -> None:
    """Same client id as a paired phone, different key: must be refused."""
    h = harness
    credentials = await pair(h)
    ltpk, _ltsk, atv_id, client_id = credentials.split(":")
    forged_key = "11" * 32
    forged = ":".join([ltpk, forged_key, atv_id, client_id])
    with pytest.raises((exceptions.ProtocolError, exceptions.ConnectionFailedError, asyncio.TimeoutError)):
        atv = await pyatv.connect(h.config(forged), asyncio.get_running_loop())
        await asyncio.gather(*atv.close())
    assert h.sessions == []


async def test_inputs_carry_kind(harness: Harness) -> None:
    h = harness
    atv = await pyatv.connect(h.config(await pair(h)), asyncio.get_running_loop())
    try:
        await atv.touch.swipe(100, 500, 900, 500, 100)
        await h.wait_for(lambda: bool(h.inputs))
        assert h.inputs[0].kind == InputKind.SWIPE and h.inputs[0].name == "right"
    finally:
        await asyncio.gather(*atv.close())
