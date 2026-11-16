"""Decode HID button and touchpad messages into ``RawInput`` events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# _hidC codes. 1-17 from pyatv companion/api.py:35-56 (HidCommand). 18/19 are
# pyatv's PageUp/PageDown, which atv-core says the iOS 26/27 Control Center
# remote uses for Mute and Power (atv-core session.rs:60-63).
HID_BUTTONS: dict[int, str] = {
    1: "up",
    2: "down",
    3: "left",
    4: "right",
    5: "menu",
    6: "select",
    7: "home",
    8: "volume_up",
    9: "volume_down",
    10: "siri",
    11: "screensaver",
    12: "sleep",
    13: "wake",
    14: "play_pause",
    15: "channel_up",
    16: "channel_down",
    17: "guide",
    18: "mute",
    19: "power",
}

# _hBtS values: pyatv companion/api.py:308.
BUTTON_DOWN = 1
BUTTON_UP = 2

# _tPh phases: pyatv const.py:460-466 (TouchAction).
TOUCH_PRESS = 1
TOUCH_HOLD = 3
TOUCH_RELEASE = 4
TOUCH_CLICK = 5

# Touchpad is 1000x1000 (pyatv companion/api.py:88-89). Tap/swipe distances
# from atv-core session.rs:30-31.
DEFAULT_TAP_MAX_DISTANCE = 60.0
DEFAULT_SWIPE_MIN_DISTANCE = 150.0


class InputKind(StrEnum):
    BUTTON = "button"  # name + pressed (down/up)
    SWIPE = "swipe"  # name = up/down/left/right
    TAP = "tap"  # touchpad tap (no movement)
    MEDIA = "media"  # MediaControlCommand: play, pause, skip_by, set_volume, ...
    TEXT = "text"  # keyboard text (reserved)


@dataclass(frozen=True)
class RawInput:
    """One decoded input from the phone, before any gesture interpretation."""

    kind: InputKind
    name: str
    timestamp: float
    pressed: bool | None = None
    value: float | str | None = None
    client: str = ""

    def __str__(self) -> str:
        parts = [self.kind.value, self.name]
        if self.pressed is not None:
            parts.append("down" if self.pressed else "up")
        if self.value is not None:
            parts.append(repr(self.value))
        return " ".join(parts)


def decode_hid_command(content: dict[str, Any], timestamp: float, client: str = "") -> RawInput | None:
    """Decode a ``_hidC`` request body."""
    code = content.get("_hidC")
    state = content.get("_hBtS")
    if not isinstance(code, int) or state not in (BUTTON_DOWN, BUTTON_UP):
        return None
    return RawInput(
        kind=InputKind.BUTTON,
        name=HID_BUTTONS.get(code, f"unknown_{code}"),
        pressed=state == BUTTON_DOWN,
        timestamp=timestamp,
        client=client,
    )


class TouchTracker:
    """Turn a ``_hidT`` touch sequence into a tap or a swipe (on release).

    Mirrors atv-core session.rs:953-1052 (direction mode): measure the largest
    axis distance between press and release; below ``tap_max`` it's a tap, at
    or above ``swipe_min`` it's a swipe in the dominant direction (+y = down),
    in between it's ignored. Phase 5 (click) is ignored, because the click also
    arrives as a ``_hidC`` select (pyatv companion/api.py:378-386).
    """

    def __init__(
        self,
        swipe_min: float = DEFAULT_SWIPE_MIN_DISTANCE,
        tap_max: float = DEFAULT_TAP_MAX_DISTANCE,
    ) -> None:
        self.swipe_min = swipe_min
        self.tap_max = tap_max
        self._origin: tuple[float, float] | None = None
        self._last: tuple[float, float] | None = None

    def feed(self, content: dict[str, Any], timestamp: float, client: str = "") -> RawInput | None:
        phase = content.get("_tPh")
        x, y = content.get("_cx"), content.get("_cy")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None
        point = (float(x), float(y))
        if phase == TOUCH_PRESS:
            self._origin = self._last = point
            return None
        if phase == TOUCH_HOLD:
            self._last = point
            return None
        if phase != TOUCH_RELEASE or self._origin is None:
            return None

        origin, self._origin, self._last = self._origin, None, None
        dx, dy = point[0] - origin[0], point[1] - origin[1]
        distance = max(abs(dx), abs(dy))
        if distance < self.tap_max:
            return RawInput(InputKind.TAP, "tap", timestamp, client=client)
        if distance < self.swipe_min:
            return None
        if abs(dx) > abs(dy):
            direction = "right" if dx > 0 else "left"
        else:
            direction = "down" if dy > 0 else "up"
        return RawInput(InputKind.SWIPE, direction, timestamp, client=client)
