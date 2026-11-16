"""Setup, advertising, pairing via a real pyatv client, events, triggers, unload."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

import pyatv
import pytest
from homeassistant.components import automation, persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pyatv import conf
from pyatv.const import InputAction, Protocol
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_mock_service,
)

from custom_components.remote_bridge.const import CONF_ALLOW_PAIRING, DOMAIN, EVENT_BUTTON
from custom_components.remote_bridge.device_trigger import async_get_triggers

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> int:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    port = entry.runtime_data.server.port
    assert port
    return port


def device_of(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    return devices[0]


def phone_config(port: int, credentials: str | None = None) -> conf.AppleTV:
    config = conf.AppleTV("127.0.0.1", "Test TV")
    config.add_service(conf.ManualService("test-tv", Protocol.Companion, port, {}, credentials=credentials))
    return config


async def wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def pair_phone(hass: HomeAssistant, port: int, notifications: list[dict[str, Any]]) -> str:
    handler = await pyatv.pair(phone_config(port), Protocol.Companion, hass.loop, name="Test iPhone")
    try:
        await handler.begin()
        await wait_for(lambda: bool(pins_from(notifications)))
        handler.pin(int(pins_from(notifications)[-1]))
        await handler.finish()
        assert handler.has_paired
        return str(handler.service.credentials)
    finally:
        await handler.close()


@pytest.fixture
def notifications() -> Any:
    """Capture persistent notifications while still creating them."""
    created: list[dict[str, Any]] = []
    real = persistent_notification.async_create

    def _create(
        hass: HomeAssistant, message: str, title: str | None = None, notification_id: str | None = None
    ) -> None:
        created.append({"message": message, "title": title, "id": notification_id})
        real(hass, message, title, notification_id)

    with patch.object(persistent_notification, "async_create", _create):
        yield created


def pins_from(notifications: list[dict[str, Any]]) -> list[str]:
    return [m.group(1) for n in notifications if (m := re.search(r"PIN \*\*(\d{4})\*\*", n["message"]))]


async def test_setup_advertises_and_unload_unregisters(
    hass: HomeAssistant, entry: MockConfigEntry, zeroconf: MagicMock
) -> None:
    port = await setup(hass, entry)

    zeroconf.async_register_service.assert_awaited_once()
    info = zeroconf.async_register_service.call_args.args[0]
    assert info.type == "_companion-link._tcp.local."
    assert info.name == "Test TV._companion-link._tcp.local."
    assert info.port == port
    assert info.addresses == [bytes([127, 0, 0, 1])]  # IPv4 only
    assert info.properties[b"rpFl"] == b"0x36782"
    assert zeroconf.async_register_service.call_args.kwargs == {"allow_name_change": True}

    device = device_of(hass, entry)
    assert device.name == "Test TV" and device.model == "Virtual TV"
    assert hass.states.get("binary_sensor.test_tv_connected").state == STATE_OFF
    assert hass.states.get("sensor.test_tv_last_button") is not None
    assert hass.states.get("button.test_tv_forget_paired_devices") is not None
    assert hass.states.get("select.test_tv_input_source").state == "unavailable"  # single mode

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    zeroconf.async_unregister_service.assert_awaited_once_with(info)
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)


async def test_port_is_remembered(hass: HomeAssistant, entry: MockConfigEntry, zeroconf: MagicMock) -> None:
    port = await setup(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.server.port == port


async def test_pair_press_events_and_triggers(
    hass: HomeAssistant, entry: MockConfigEntry, zeroconf: MagicMock, notifications: list[dict[str, Any]]
) -> None:
    port = await setup(hass, entry)
    events = async_capture_events(hass, EVENT_BUTTON)
    device = device_of(hass, entry)

    # Device trigger: TV button held → input/long.
    calls = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: {
                "trigger": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": device.id,
                    "type": "input_long",
                },
                "action": {
                    "service": "test.automation",
                    "data": {"control": "{{ trigger.event.data.control }}"},
                },
            }
        },
    )

    credentials = await pair_phone(hass, port, notifications)
    assert len(pins_from(notifications)) == 1
    assert entry.options[CONF_ALLOW_PAIRING] is False  # window closed after first pair
    assert len(entry.runtime_data.store.clients) == 1

    atv = await pyatv.connect(phone_config(port, credentials), hass.loop)
    try:
        await wait_for(lambda: hass.states.get("binary_sensor.test_tv_connected").state == STATE_ON)
        assert hass.states.get("binary_sensor.test_tv_connected").attributes["clients"] == ["Test iPhone"]
        await atv.remote_control.select()
        await atv.remote_control.home(InputAction.Hold)  # held 1 s > 600 ms
        await atv.remote_control.up()
        await wait_for(lambda: len(events) >= 3)
        await hass.async_block_till_done()
    finally:
        await asyncio.gather(*atv.close())

    got = [(e.data["control"], e.data["press_type"]) for e in events]
    assert got == [("select", "short"), ("input", "long"), ("up", "short")]
    assert events[0].data == {
        "entry_id": entry.entry_id,
        "device_id": device.id,
        "name": "Test TV",
        "control": "select",
        "press_type": "short",
        "client": "Test iPhone",
    }
    assert [c.data for c in calls] == [{"control": "input"}]
    state = hass.states.get("sensor.test_tv_last_button")
    assert state.state == "up" and state.attributes["press_type"] == "short"
    await wait_for(lambda: hass.states.get("binary_sensor.test_tv_connected").state == STATE_OFF)


async def test_pairing_refused_when_window_closed(
    hass: HomeAssistant, entry: MockConfigEntry, zeroconf: MagicMock, notifications: list[dict[str, Any]]
) -> None:
    hass.config_entries.async_update_entry(entry, options={CONF_ALLOW_PAIRING: False})
    port = await setup(hass, entry)
    handler = await pyatv.pair(phone_config(port), Protocol.Companion, hass.loop)
    try:
        with pytest.raises(pyatv.exceptions.PairingError):
            await handler.begin()
    finally:
        await handler.close()
    await hass.async_block_till_done()
    assert pins_from(notifications) == []
    assert notifications[-1]["title"] == "Remote Bridge: pairing refused"


async def test_forget_button_and_reopen_window(
    hass: HomeAssistant, entry: MockConfigEntry, zeroconf: MagicMock, notifications: list[dict[str, Any]]
) -> None:
    port = await setup(hass, entry)
    await pair_phone(hass, port, notifications)
    assert entry.options[CONF_ALLOW_PAIRING] is False
    runtime = entry.runtime_data
    runtime.server.policy.failed_attempts = 3

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.test_tv_forget_paired_devices"}, blocking=True
    )
    assert runtime.store.clients == {}
    assert entry.options[CONF_ALLOW_PAIRING] is True
    assert runtime.server.policy.failed_attempts == 0


async def test_options_reenable_pairing_resets_lockout(
    hass: HomeAssistant, entry: MockConfigEntry, zeroconf: MagicMock
) -> None:
    await setup(hass, entry)
    entry.runtime_data.server.policy.failed_attempts = 10
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "pairing"})
    await hass.config_entries.options.async_configure(result["flow_id"], {CONF_ALLOW_PAIRING: True})
    assert entry.runtime_data.server.policy.failed_attempts == 0


async def test_device_triggers_listed(
    hass: HomeAssistant, entry: MockConfigEntry, zeroconf: MagicMock
) -> None:
    await setup(hass, entry)
    device = device_of(hass, entry)
    types = {t["type"] for t in await async_get_triggers(hass, device.id)}
    assert {
        "select_short",
        "select_long",
        "input_long",
        "home_double_double",
        "up_repeat",
        "power_short",
    } <= types


async def test_configured_port_busy_is_not_ready(hass: HomeAssistant, zeroconf: MagicMock) -> None:
    blocker = await asyncio.start_server(lambda r, w: None, "0.0.0.0", 0)
    busy = blocker.sockets[0].getsockname()[1]
    entry = MockConfigEntry(domain=DOMAIN, title="Busy TV", data={"name": "Busy TV", "port": busy})
    entry.add_to_hass(hass)
    try:
        await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state is ConfigEntryState.SETUP_RETRY
    finally:
        blocker.close()


async def test_advertise_failure_is_not_ready(
    hass: HomeAssistant, entry: MockConfigEntry, zeroconf: MagicMock
) -> None:
    zeroconf.async_register_service.side_effect = RuntimeError("boom")
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_remove_entry_deletes_storage(
    hass: HomeAssistant, entry: MockConfigEntry, zeroconf: MagicMock
) -> None:
    await setup(hass, entry)
    await hass.async_block_till_done()
    with patch("homeassistant.helpers.storage.Store.async_remove") as remove:
        await hass.config_entries.async_remove(entry.entry_id)
    remove.assert_awaited_once()
