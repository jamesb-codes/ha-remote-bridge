"""Table-driven tests for the gesture layer, on a fake clock."""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from remote_bridge.gesture import ControlEvent, GestureEngine, GestureOptions, PressType
from remote_bridge.protocol.hid import InputKind, RawInput


@dataclass(order=True)
class _Timer:
    due: float
    seq: int
    callback: Callable[[], None] = field(compare=False)
    cancelled: bool = field(default=False, compare=False)

    def cancel(self) -> None:
        self.cancelled = True


class FakeLoop:
    """Clock + call_later that only moves when told to."""

    def __init__(self) -> None:
        self.now = 0.0
        self._timers: list[_Timer] = []
        self._seq = 0

    def clock(self) -> float:
        return self.now

    def call_later(self, delay: float, callback: Callable[[], None]) -> _Timer:
        self._seq += 1
        timer = _Timer(self.now + delay, self._seq, callback)
        heapq.heappush(self._timers, timer)
        return timer

    def advance_to(self, t: float) -> None:
        while self._timers and self._timers[0].due <= t + 1e-9:
            timer = heapq.heappop(self._timers)
            if not timer.cancelled:
                self.now = timer.due
                timer.callback()
        self.now = max(self.now, t)

    @property
    def pending(self) -> int:
        return sum(not t.cancelled for t in self._timers)


def make(options: GestureOptions | None = None) -> tuple[GestureEngine, FakeLoop, list[ControlEvent]]:
    loop = FakeLoop()
    events: list[ControlEvent] = []
    engine = GestureEngine(events.append, loop.call_later, loop.clock, options)
    return engine, loop, events


# Script steps (time in ms):
#   ("down"|"up", raw_button, t)   ("swipe", dir, t)   ("tap", t)
#   ("media", name, value, t)      ("text", value, t)  ("wait", t)
Step = tuple[Any, ...]


def run(
    script: list[Step], options: GestureOptions | None = None, client: str = "phone"
) -> list[tuple[Any, ...]]:
    engine, loop, events = make(options)
    for step in script:
        kind, t = step[0], step[-1]
        loop.advance_to(t / 1000)
        if kind in ("down", "up"):
            engine.feed(RawInput(InputKind.BUTTON, step[1], loop.now, pressed=kind == "down", client=client))
        elif kind == "swipe":
            engine.feed(RawInput(InputKind.SWIPE, step[1], loop.now, client=client))
        elif kind == "tap":
            engine.feed(RawInput(InputKind.TAP, "tap", loop.now, client=client))
        elif kind == "media":
            engine.feed(RawInput(InputKind.MEDIA, step[1], loop.now, value=step[2], client=client))
        elif kind == "text":
            engine.feed(RawInput(InputKind.TEXT, "text", loop.now, value=step[1], client=client))
    loop.advance_to(10.0)  # flush anything pending
    assert all(e.client == client for e in events)
    return [
        (e.control, e.press_type.value, round(e.timestamp * 1000))
        + ((e.value,) if e.value is not None else ())
        for e in events
    ]


def press(button: str, at: int, held: int) -> list[Step]:
    return [("down", button, at), ("up", button, at + held)]


CASES: list[tuple[str, list[Step], dict[str, Any], list[tuple[Any, ...]]]] = [
    # --- navigation: instant short, repeat while held -----------------------
    ("nav tap is instant", press("up", 0, 80), {}, [("up", "short", 0)]),
    (
        "nav hold repeats after delay",
        press("left", 0, 1100),
        {},
        [("left", "short", 0), ("left", "repeat", 500), ("left", "repeat", 750), ("left", "repeat", 1000)],
    ),
    (
        "nav repeat off for this control",
        press("down", 0, 1100),
        {"repeat_controls": frozenset({"up"})},
        [("down", "short", 0)],
    ),
    (
        "volume repeats with custom timing",
        press("volume_up", 0, 450),
        {"repeat_delay_ms": 200, "repeat_interval_ms": 100},
        [("volume_up", "short", 0), *[("volume_up", "repeat", t) for t in (200, 300, 400)]],
    ),
    # --- long-press controls ------------------------------------------------
    ("select short on release", press("select", 0, 120), {}, [("select", "short", 120)]),
    ("select long at threshold, no short", press("select", 0, 2000), {}, [("select", "long", 600)]),
    ("back is menu; long", press("menu", 0, 700), {}, [("back", "long", 600)]),
    ("back short", press("menu", 0, 100), {}, [("back", "short", 100)]),
    ("long threshold option", press("select", 0, 350), {"long_press_ms": 300}, [("select", "long", 300)]),
    # --- home: long = input, double = home_double, short after window -------
    ("home short waits for double window", press("home", 0, 100), {}, [("home", "short", 400)]),
    ("TV long press is input, no stray home", press("home", 0, 3900), {}, [("input", "long", 600)]),
    (
        "home double fires on second press",
        press("home", 0, 80) + press("home", 200, 80),
        {},
        [("home_double", "double", 200)],
    ),
    (
        "second press after window = two shorts",
        press("home", 0, 80) + press("home", 500, 80),
        {},
        [("home", "short", 380), ("home", "short", 880)],
    ),
    (
        "double window option",
        press("home", 0, 50) + press("home", 400, 50),
        {"double_tap_ms": 500},
        [("home_double", "double", 400)],
    ),
    (
        "home without double mapping is not delayed",
        press("home", 0, 100),
        {"double_controls": frozenset()},
        [("home", "short", 100)],
    ),
    (
        "double-only control (not long) on release",
        press("play_pause", 0, 50) + press("play_pause", 150, 50),
        {"double_controls": frozenset({"play_pause"})},
        [("play_pause", "double", 150)],
    ),
    (
        "double-only control single press",
        press("play_pause", 0, 50),
        {"double_controls": frozenset({"play_pause"})},
        [("play_pause", "short", 350)],
    ),
    # --- instant controls ---------------------------------------------------
    ("play_pause instant", press("play_pause", 0, 300), {}, [("play_pause", "short", 0)]),
    ("power instant", press("power", 0, 40), {}, [("power", "short", 0)]),
    ("mute instant", press("mute", 0, 80), {}, [("mute", "short", 0)]),
    ("unknown button passes through", press("channel_up", 0, 80), {}, [("channel_up", "short", 0)]),
    # --- robustness ---------------------------------------------------------
    ("release without press ignored", [("up", "select", 0)], {}, []),
    (
        "missed release: new press restarts",
        [("down", "select", 0), ("down", "select", 300), ("up", "select", 400)],
        {},
        [("select", "short", 400)],
    ),
    (
        "missed release on repeat control stops old repeat",
        [("down", "up", 0), ("down", "up", 300), ("up", "up", 600)],
        {},
        [("up", "short", 0), ("up", "short", 300)],
    ),
    # --- touchpad -----------------------------------------------------------
    ("swipe", [("swipe", "right", 10)], {}, [("right", "short", 10)]),
    ("tap selects", [("tap", 0)], {}, [("select", "short", 0)]),
    ("tap select disabled", [("tap", 0)], {"tap_selects": False}, []),
    (
        "click's trailing touch is not a second select",
        [*press("select", 0, 10), ("tap", 11)],
        {},
        [("select", "short", 10)],
    ),
    (
        "tap long after a click still selects",
        [*press("select", 0, 10), ("tap", 1000)],
        {},
        [("select", "short", 10), ("select", "short", 1000)],
    ),
    # --- media commands -----------------------------------------------------
    ("play → play_pause", [("media", "play", None, 0)], {}, [("play_pause", "short", 0)]),
    ("pause → play_pause", [("media", "pause", None, 0)], {}, [("play_pause", "short", 0)]),
    ("skip forward", [("media", "skip_by", 10.0, 0)], {}, [("skip_forward", "short", 0, 10.0)]),
    ("skip back", [("media", "skip_by", -10.0, 0)], {}, [("skip_back", "short", 0, 10.0)]),
    ("skip by zero ignored", [("media", "skip_by", 0.0, 0)], {}, []),
    (
        "volume slider → relative steps",
        [
            ("media", "set_volume", 0.5, 0),
            ("media", "set_volume", 0.6, 10),
            ("media", "set_volume", 0.6, 20),
            ("media", "set_volume", 0.2, 30),
        ],
        {},
        [("volume_up", "short", 10, 0.6), ("volume_down", "short", 30, 0.2)],
    ),
    ("set_volume without value", [("media", "set_volume", None, 0)], {}, []),
    ("other media passes through", [("media", "next_track", None, 0)], {}, [("next_track", "short", 0)]),
    ("keyboard text", [("text", "hello", 0)], {}, [("keyboard_text", "text", 0, "hello")]),
]


@pytest.mark.parametrize(
    ("script", "overrides", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_gestures(script: list[Step], overrides: dict[str, Any], expected: list[tuple[Any, ...]]) -> None:
    assert run(script, replace(GestureOptions(), **overrides)) == expected


def test_clients_are_independent() -> None:
    engine, loop, events = make()
    engine.feed(RawInput(InputKind.BUTTON, "home", 0, pressed=True, client="a"))
    engine.feed(RawInput(InputKind.BUTTON, "home", 0, pressed=True, client="b"))
    loop.advance_to(0.1)
    engine.feed(RawInput(InputKind.BUTTON, "home", 0.1, pressed=False, client="a"))
    loop.advance_to(1.0)
    engine.feed(RawInput(InputKind.BUTTON, "home", 1.0, pressed=False, client="b"))
    assert [(e.client, e.control, e.press_type) for e in events] == [
        ("a", "home", PressType.SHORT),
        ("b", "input", PressType.LONG),
    ]


def test_reset_flushes_pending_short_and_cancels_timers() -> None:
    engine, loop, events = make()
    engine.feed(RawInput(InputKind.BUTTON, "home", 0, pressed=True, client="a"))
    engine.feed(RawInput(InputKind.BUTTON, "home", 0, pressed=False, client="a"))  # awaiting double
    engine.feed(RawInput(InputKind.BUTTON, "up", 0, pressed=True, client="a"))  # repeating
    engine.feed(RawInput(InputKind.BUTTON, "select", 0, pressed=True, client="b"))  # long pending
    events.clear()
    engine.reset("a")
    engine.reset("missing")
    assert [(e.control, e.press_type) for e in events] == [("home", PressType.SHORT)]
    engine.reset(flush=False)
    loop.advance_to(5)
    assert loop.pending == 0 and len(events) == 1


def test_reset_without_flush_drops_pending_short() -> None:
    engine, loop, events = make()
    engine.feed(RawInput(InputKind.BUTTON, "home", 0, pressed=True))
    engine.feed(RawInput(InputKind.BUTTON, "home", 0, pressed=False))
    engine.reset(flush=False)
    loop.advance_to(5)
    assert events == []


def test_options_change_live() -> None:
    engine, loop, events = make()
    engine.options = replace(engine.options, long_press_ms=100)
    engine.feed(RawInput(InputKind.BUTTON, "select", 0, pressed=True))
    loop.advance_to(0.15)
    assert [(e.control, e.press_type, e.timestamp) for e in events] == [("select", PressType.LONG, 0.1)]


def test_default_clock_and_options() -> None:
    events: list[ControlEvent] = []
    engine = GestureEngine(events.append, lambda delay, cb: _Timer(delay, 0, cb))
    engine.feed(RawInput(InputKind.SWIPE, "up", 0))
    assert events[0].timestamp > 0 and engine.options == GestureOptions()
