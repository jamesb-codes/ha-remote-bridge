"""Connected binary sensor: is any phone using this virtual TV right now."""

from __future__ import annotations

from typing import Any

# Package-root import: binary_sensor.const only exists from HA 2026.10.
from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined]
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RemoteBridgeConfigEntry
from .entity import RemoteBridgeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RemoteBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ConnectedSensor(entry.runtime_data, "connected")])


class ConnectedSensor(RemoteBridgeEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool:
        return bool(self.runtime.server.active_sessions)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "clients": self.runtime.connected_clients,
            "paired_devices": len(self.runtime.store.clients),
        }
