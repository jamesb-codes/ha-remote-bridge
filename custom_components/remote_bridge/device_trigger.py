"""Device triggers: "<control> <press type>" for each virtual TV."""

from __future__ import annotations

from typing import Final

import probatio
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    ATTR_DEVICE_ID,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_EVENT,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import ATTR_CONTROL, ATTR_PRESS_TYPE, DOMAIN, EVENT_BUTTON, TRIGGER_TYPES

TRIGGER_SCHEMA: Final = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {probatio.Required(CONF_TYPE): probatio.In(list(TRIGGER_TYPES))}
)


async def async_validate_trigger_config(hass: HomeAssistant, config: ConfigType) -> ConfigType:
    return TRIGGER_SCHEMA(config)  # type: ignore[no-any-return]


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, str]]:
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in TRIGGER_TYPES
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    control, press_type = TRIGGER_TYPES[config[CONF_TYPE]]
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            CONF_PLATFORM: CONF_EVENT,
            event_trigger.CONF_EVENT_TYPE: EVENT_BUTTON,
            "event_data": {
                ATTR_DEVICE_ID: config[CONF_DEVICE_ID],
                ATTR_CONTROL: control,
                ATTR_PRESS_TYPE: press_type,
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
