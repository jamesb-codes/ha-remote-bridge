"""HID decoding and touchpad tap/swipe detection."""

from __future__ import annotations

import pytest

from remote_bridge.protocol.hid import InputKind, TouchTracker, decode_hid_command
from remote_bridge.protocol.mrp_state import ReceiverState


@pytest.mark.parametrize(
    ("code", "name"), [(1, "up"), (5, "menu"), (7, "home"), (18, "mute"), (19, "power"), (99, "unknown_99")]
)
def test_decode_hid(code: int, name: str) -> None:
    down = decode_hid_command({"_hidC": code, "_hBtS": 1}, 1.0, "phone")
    up = decode_hid_command({"_hidC": code, "_hBtS": 2}, 2.0)
    assert down is not None and up is not None
    assert (down.kind, down.name, down.pressed, down.client) == (InputKind.BUTTON, name, True, "phone")
    assert up.pressed is False
    assert str(down) == f"button {name} down"


def test_decode_hid_rejects_garbage() -> None:
    assert decode_hid_command({"_hidC": "x", "_hBtS": 1}, 0) is None
    assert decode_hid_command({"_hidC": 1, "_hBtS": 3}, 0) is None


def _gesture(tracker: TouchTracker, points: list[tuple[int, float, float]]) -> object:
    result = None
    for phase, x, y in points:
        result = tracker.feed({"_tPh": phase, "_cx": x, "_cy": y}, 0.0)
    return result


@pytest.mark.parametrize(
    ("end", "expected"),
    [
        ((500, 300), "up"),
        ((500, 700), "down"),
        ((300, 500), "left"),
        ((700, 500), "right"),
    ],
)
def test_swipes(end: tuple[int, int], expected: str) -> None:
    raw = _gesture(TouchTracker(), [(1, 500, 500), (3, 500, 500), (4, *end)])
    assert raw is not None and raw.kind == InputKind.SWIPE and raw.name == expected  # type: ignore[attr-defined]


def test_tap_and_dead_zone_and_threshold() -> None:
    tracker = TouchTracker()
    tap = _gesture(tracker, [(1, 500, 500), (4, 520, 510)])
    assert tap is not None and tap.kind == InputKind.TAP  # type: ignore[attr-defined]
    assert _gesture(tracker, [(1, 500, 500), (4, 600, 500)]) is None  # 100: between tap and swipe
    tracker.swipe_min = 80
    swipe = _gesture(tracker, [(1, 500, 500), (4, 600, 500)])
    assert swipe is not None and swipe.name == "right"  # type: ignore[attr-defined]


def test_touch_ignores_click_release_without_press_and_bad_coords() -> None:
    tracker = TouchTracker()
    assert _gesture(tracker, [(5, 1000, 1000)]) is None
    assert _gesture(tracker, [(4, 100, 100)]) is None
    assert tracker.feed({"_tPh": 1}, 0) is None


def test_media_commands() -> None:
    state = ReceiverState()
    assert state.handle_media_command({"_mcc": 5}, 0) == ({"_vol": 0.5}, None)
    assert state.handle_media_command({"_mcc": 12}, 0) == ({"_cse": False}, None)
    _, raw = state.handle_media_command({"_mcc": 6, "_vol": 1.7}, 0)
    assert raw is not None and raw.name == "set_volume" and raw.value == 1.0 and state.volume == 1.0
    _, raw = state.handle_media_command({"_mcc": 7, "_skpS": -10.0}, 0)
    assert raw is not None and raw.name == "skip_by" and raw.value == -10.0
    _, raw = state.handle_media_command({"_mcc": 1}, 0)
    assert raw is not None and raw.name == "play"
    assert state.handle_media_command({}, 0) == ({}, None)
