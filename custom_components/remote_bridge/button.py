"""Forget-paired-devices button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RemoteBridgeConfigEntry
from .entity import RemoteBridgeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RemoteBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ForgetPairedDevicesButton(entry.runtime_data, "forget_paired_devices")])


class ForgetPairedDevicesButton(RemoteBridgeEntity, ButtonEntity):
    """Remove every paired phone, disconnect them, and re-open pairing."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        self.runtime.forget_paired_devices()
