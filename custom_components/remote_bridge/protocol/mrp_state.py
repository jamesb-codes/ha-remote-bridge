"""Minimal device state we report to the phone, and media-control decoding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .hid import InputKind, RawInput

# MediaControlFlags.Volume (pyatv companion/__init__.py:87-99; atv-core
# session.rs:28). Advertising it is what makes iOS route volume keys to us.
MEDIA_CONTROL_VOLUME = 0x0100

# SystemStatus.Awake (pyatv companion/api.py:77-85).
SYSTEM_STATUS_AWAKE = 0x03

# MediaControlCommand values (pyatv companion/api.py:59-74).
MCC_NAMES: dict[int, str] = {
    1: "play",
    2: "pause",
    3: "next_track",
    4: "previous_track",
    5: "get_volume",
    6: "set_volume",
    7: "skip_by",
    8: "fast_forward_begin",
    9: "fast_forward_end",
    10: "rewind_begin",
    11: "rewind_end",
    12: "get_caption_settings",
    13: "set_caption_settings",
}
MCC_GET_VOLUME = 5
MCC_SET_VOLUME = 6
MCC_SKIP_BY = 7
MCC_GET_CAPTION_SETTINGS = 12


@dataclass
class ReceiverState:
    """What we pretend about ourselves. Volume is a placeholder: IR TVs are
    relative-only, so it just gives the phone's HUD something to show."""

    volume: float = 0.5
    system_status: int = SYSTEM_STATUS_AWAKE
    media_control_flags: int = MEDIA_CONTROL_VOLUME

    def media_control_status(self) -> dict[str, Any]:
        """``MediaControlStatus`` event body (atv-core session.rs:248-256)."""
        return {"MediaControlFlags": self.media_control_flags}

    def imc(self) -> dict[str, Any]:
        """``_iMC`` event body (atv-core session.rs:258-265; pyatv __init__.py:439-446)."""
        return {"_mcF": self.media_control_flags}

    def system_status_event(self) -> dict[str, Any]:
        """``SystemStatus``/``TVSystemStatus`` body (atv-core session.rs:295-301)."""
        return {"state": self.system_status}

    def handle_media_command(
        self, content: dict[str, Any], timestamp: float, client: str = ""
    ) -> tuple[dict[str, Any], RawInput | None]:
        """Answer a ``_mcc`` request. Returns (response content, input to emit).

        Replies from atv-core session.rs:908-949.
        """
        command = content.get("_mcc", content.get("MediaControlCommand"))
        if command == MCC_GET_VOLUME:
            return {"_vol": self.volume}, None
        if command == MCC_GET_CAPTION_SETTINGS:
            return {"_cse": False}, None
        if not isinstance(command, int):
            return {}, None
        name = MCC_NAMES.get(command, f"unknown_{command}")
        value: float | None = None
        if command == MCC_SET_VOLUME and isinstance(content.get("_vol"), (int, float)):
            value = max(0.0, min(1.0, float(content["_vol"])))
            self.volume = value
        elif command == MCC_SKIP_BY and isinstance(content.get("_skpS"), (int, float)):
            value = float(content["_skpS"])  # pyatv companion/__init__.py:359-380
        return {}, RawInput(InputKind.MEDIA, name, timestamp, value=value, client=client)
