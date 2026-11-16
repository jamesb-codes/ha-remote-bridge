"""Last-button sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RemoteBridgeConfigEntry
from .const import ATTR_CLIENT, ATTR_PRESS_TYPE
from .entity import RemoteBridgeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RemoteBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([LastButtonSensor(entry.runtime_data, "last_button")])


class LastButtonSensor(RemoteBridgeEntity, SensorEntity):
    """State is the control name; use the event/device triggers for automations
    (pressing the same button twice doesn't change this state)."""

    @property
    def native_value(self) -> str | None:
        event = self.runtime.last_event
        return event.control if event else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        event = self.runtime.last_event
        if event is None:
            return None
        return {ATTR_PRESS_TYPE: event.press_type.value, ATTR_CLIENT: event.client}
