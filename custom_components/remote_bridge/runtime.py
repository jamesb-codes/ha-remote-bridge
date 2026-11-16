"""Glue between one CompanionServer, the gesture layer and Home Assistant."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .advertiser import Advertiser
from .const import (
    ATTR_CLIENT,
    ATTR_CONTROL,
    ATTR_PRESS_TYPE,
    ATTR_TEXT,
    ATTR_VALUE,
    CONF_ALLOW_PAIRING,
    EVENT_BUTTON,
    signal_update,
)
from .gesture import ControlEvent, GestureEngine, PressType
from .protocol import ClientConnection, CompanionServer, PairedClient, RawInput
from .store import EntryStore

_LOGGER = logging.getLogger(__name__)


@dataclass
class RemoteBridgeRuntime:
    """Everything one config entry (virtual TV) owns while loaded."""

    hass: HomeAssistant
    entry: ConfigEntry[RemoteBridgeRuntime]
    store: EntryStore
    device_id: str = ""
    server: CompanionServer = field(init=False)
    engine: GestureEngine = field(init=False)
    advertiser: Advertiser | None = None
    last_event: ControlEvent | None = None

    @property
    def name(self) -> str:
        return str(self.entry.title)

    @property
    def pin_notification_id(self) -> str:
        return f"remote_bridge_pairing_{self.entry.entry_id}"

    @property
    def connected_clients(self) -> list[str]:
        return sorted({conn.client_name for conn in self.server.active_sessions})

    # ---- pairing (allow_pairing is called from an executor thread) --------

    def pairing_allowed(self) -> bool:
        allowed = bool(self.entry.options.get(CONF_ALLOW_PAIRING, True))
        if not allowed:
            self.hass.loop.call_soon_threadsafe(self._notify_pairing_refused)
        return allowed

    @callback
    def _notify_pairing_refused(self) -> None:
        persistent_notification.async_create(
            self.hass,
            f"A device tried to pair with **{self.name}**, but new pairings are off. "
            "To pair a new phone, turn on *Allow new pairings* in the integration's "
            "options and try again.",
            title="Remote Bridge: pairing refused",
            notification_id=self.pin_notification_id,
        )

    @callback
    def on_pin(self, pin: str) -> None:
        persistent_notification.async_create(
            self.hass,
            f"Enter PIN **{pin}** on your iPhone or iPad to pair it with **{self.name}**.\n\n"
            "A new PIN is generated for every pairing attempt.",
            title="Remote Bridge: pairing PIN",
            notification_id=self.pin_notification_id,
        )

    @callback
    def on_paired(self, client: PairedClient) -> None:
        persistent_notification.async_dismiss(self.hass, self.pin_notification_id)
        _LOGGER.info("%s paired with %s", self.name, client.name or "a new device")
        # Close the pairing window after the first successful pairing.
        if self.entry.options.get(CONF_ALLOW_PAIRING, True):
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, CONF_ALLOW_PAIRING: False}
            )
        self._refresh_entities()

    @callback
    def forget_paired_devices(self) -> None:
        """Drop all pairings, disconnect, and reopen the pairing window."""
        self.store.remove_all()
        self.server.disconnect_all()
        self.engine.reset(flush=False)
        self.server.policy.reset_attempts()
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, CONF_ALLOW_PAIRING: True}
        )
        self._refresh_entities()

    # ---- inputs -----------------------------------------------------------

    @callback
    def on_raw_input(self, raw: RawInput) -> None:
        self.engine.feed(raw)

    @callback
    def on_control(self, event: ControlEvent) -> None:
        self.last_event = event
        data: dict[str, Any] = {
            "entry_id": self.entry.entry_id,
            ATTR_DEVICE_ID: self.device_id,
            CONF_NAME: self.name,
            ATTR_CONTROL: event.control,
            ATTR_PRESS_TYPE: event.press_type.value,
            ATTR_CLIENT: event.client,
        }
        if event.press_type is PressType.TEXT:
            data[ATTR_TEXT] = event.value
        elif event.value is not None:
            data[ATTR_VALUE] = event.value
        self.hass.bus.async_fire(EVENT_BUTTON, data)
        self._refresh_entities()

    @callback
    def on_session_change(self, conn: ClientConnection, active: bool) -> None:
        if not active:
            self.engine.reset(conn.client_name)
        self._refresh_entities()

    @callback
    def _refresh_entities(self) -> None:
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))
