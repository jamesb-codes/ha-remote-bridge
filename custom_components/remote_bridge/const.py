"""Constants for Remote Bridge."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "remote_bridge"
MANUFACTURER: Final = "Remote Bridge"
MODEL: Final = "Virtual TV"

EVENT_BUTTON: Final = "remote_bridge_button"

CONF_ALLOW_PAIRING: Final = "allow_pairing"

ATTR_CONTROL: Final = "control"
ATTR_PRESS_TYPE: Final = "press_type"
ATTR_CLIENT: Final = "client"
ATTR_TEXT: Final = "text"
ATTR_VALUE: Final = "value"

STORAGE_VERSION: Final = 1

PLATFORMS: Final = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

# Every (control, press type) the phone can produce with default gesture
# options. Device triggers, translations and the mapping UI derive from this.
CONTROL_PRESS_TYPES: Final[dict[str, tuple[str, ...]]] = {
    "up": ("short", "repeat"),
    "down": ("short", "repeat"),
    "left": ("short", "repeat"),
    "right": ("short", "repeat"),
    "select": ("short", "long"),
    "back": ("short", "long"),
    "home": ("short",),
    "input": ("long",),
    "home_double": ("double",),
    "play_pause": ("short",),
    "volume_up": ("short", "repeat"),
    "volume_down": ("short", "repeat"),
    "mute": ("short",),
    "power": ("short",),
    "siri": ("short", "long"),
    "skip_forward": ("short",),
    "skip_back": ("short",),
    "keyboard_text": ("text",),
}

# Device trigger "type" → (control, press type).
TRIGGER_TYPES: Final[dict[str, tuple[str, str]]] = {
    f"{control}_{press}": (control, press)
    for control, presses in CONTROL_PRESS_TYPES.items()
    for press in presses
}


def signal_update(entry_id: str) -> str:
    """Dispatcher signal for entity refreshes of one virtual TV."""
    return f"{DOMAIN}_update_{entry_id}"
