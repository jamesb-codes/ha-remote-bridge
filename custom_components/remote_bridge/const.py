"""Constants for Remote Bridge."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "remote_bridge"
MANUFACTURER: Final = "Remote Bridge"
MODEL: Final = "Virtual TV"

EVENT_BUTTON: Final = "remote_bridge_button"

CONF_ALLOW_PAIRING: Final = "allow_pairing"

# Options layout (entry.options)
CONF_MAPPINGS: Final = "mappings"  # {"<control>_<press>": [actions]}
CONF_INPUT_MODE: Final = "input_mode"
CONF_INPUT_SOURCES: Final = "input_sources"  # [{"name": str, "actions": [...]}]
INPUT_MODE_SINGLE: Final = "single"
INPUT_MODE_CYCLE: Final = "cycle"
SOURCE_NAME: Final = "name"
SOURCE_ACTIONS: Final = "actions"
MAX_INPUT_SOURCES: Final = 6

CONF_LONG_PRESS_MS: Final = "long_press_ms"
CONF_DOUBLE_TAP_MS: Final = "double_tap_ms"
CONF_REPEAT_DELAY_MS: Final = "repeat_delay_ms"
CONF_REPEAT_INTERVAL_MS: Final = "repeat_interval_ms"
CONF_REPEAT_CONTROLS: Final = "repeat_controls"
CONF_SWIPE_THRESHOLD: Final = "swipe_threshold"
CONF_HOME_DOUBLE: Final = "home_double_press"

DEFAULT_TIMING: Final[dict[str, int]] = {
    CONF_LONG_PRESS_MS: 600,
    CONF_DOUBLE_TAP_MS: 300,
    CONF_REPEAT_DELAY_MS: 500,
    CONF_REPEAT_INTERVAL_MS: 250,
    CONF_SWIPE_THRESHOLD: 150,
}
REPEAT_CAPABLE: Final = ("up", "down", "left", "right", "volume_up", "volume_down")

# Options-flow mapping pages → mapping keys shown on each. Repeats reuse the
# short mapping, so they get no field of their own.
MAPPING_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "navigation": (
        "up_short",
        "down_short",
        "left_short",
        "right_short",
        "select_short",
        "select_long",
        "back_short",
        "back_long",
        "home_short",
        "home_double_double",
    ),
    "volume_power": ("volume_up_short", "volume_down_short", "mute_short", "power_short"),
    "media": (
        "play_pause_short",
        "skip_forward_short",
        "skip_back_short",
        "siri_short",
        "siri_long",
    ),
}

ATTR_CONTROL: Final = "control"
ATTR_PRESS_TYPE: Final = "press_type"
ATTR_CLIENT: Final = "client"
ATTR_TEXT: Final = "text"
ATTR_VALUE: Final = "value"

STORAGE_VERSION: Final = 1

PLATFORMS: Final = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT, Platform.SENSOR]

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
