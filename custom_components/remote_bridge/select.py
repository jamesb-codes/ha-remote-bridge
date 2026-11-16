"""Current input source (cycle mode): visible, settable, survives restarts."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import RemoteBridgeConfigEntry
from .entity import RemoteBridgeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RemoteBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([InputSourceSelect(entry.runtime_data, "input_source")])


class InputSourceSelect(RemoteBridgeEntity, SelectEntity, RestoreEntity):
    """The input the TV is on. A long press of the TV button moves to the next
    one; picking one here only corrects the pointer (it sends nothing)."""

    @property
    def available(self) -> bool:
        return self.runtime.mapper.cycle_mode

    @property
    def options(self) -> list[str]:
        return self.runtime.mapper.source_names or ["-"]

    @property
    def current_option(self) -> str | None:
        names = self.runtime.mapper.source_names
        return names[self.runtime.mapper.input_index] if names else None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        names = self.runtime.mapper.source_names
        if last is not None and last.state in names:
            self.runtime.mapper.set_input_index(names.index(last.state))

    async def async_select_option(self, option: str) -> None:
        self.runtime.mapper.set_input_index(self.runtime.mapper.source_names.index(option))
        self.async_write_ha_state()
