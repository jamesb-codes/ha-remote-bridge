"""Turn raw phone inputs into logical controls with press types.

``RawInput`` (button down/up, swipe, tap, media command) goes in and
``ControlEvent(control, press_type)`` comes out. No Home Assistant imports:
time comes from an injectable ``clock`` and timers from an injectable
``scheduler`` (``loop.call_later`` in production, a fake in tests).

Per-control behaviour (precedence: long > repeat > double > instant):

* **long** controls (default select, back, home, siri): nothing on press.
  Held for ``long_press_ms`` → ``long`` fires *at the threshold* and the short
  is suppressed; released earlier → ``short`` (or a double-tap wait, below).
  ``home`` long is reported as control ``input``.
* **repeat** controls (default the four directions and volume): ``short`` on
  press (navigation stays instant), then ``repeat`` every
  ``repeat_interval_ms`` once held for ``repeat_delay_ms``.
* **double** controls (default home): on release the ``short`` waits
  ``double_tap_ms``; a second press inside that window fires ``double``
  instead. ``home`` double is reported as control ``home_double``. Only
  controls listed here pay that delay.
* everything else: ``short`` on press.

Touchpad swipes are direction ``short`` presses. A touchpad tap is a
``select`` ``short``, unless a select button was released within
``tap_dedupe_ms`` (iOS sends a trailing touch after a click; see
docs/protocol-notes.md, "Manual iPhone results").
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .protocol.hid import InputKind, RawInput

# Raw HID names (protocol/hid.py) whose control name differs.
RAW_TO_CONTROL: dict[str, str] = {"menu": "back"}
LONG_RENAMES: dict[str, str] = {"home": "input"}
DOUBLE_RENAMES: dict[str, str] = {"home": "home_double"}

NAVIGATION = frozenset({"up", "down", "left", "right"})
VOLUME = frozenset({"volume_up", "volume_down"})


class PressType(StrEnum):
    SHORT = "short"
    LONG = "long"
    DOUBLE = "double"
    REPEAT = "repeat"
    TEXT = "text"


@dataclass(frozen=True)
class ControlEvent:
    control: str
    press_type: PressType
    client: str = ""
    timestamp: float = 0.0
    value: float | str | None = None


@dataclass(frozen=True)
class GestureOptions:
    long_press_ms: int = 600
    double_tap_ms: int = 300
    repeat_delay_ms: int = 500
    repeat_interval_ms: int = 250
    tap_dedupe_ms: int = 300
    tap_selects: bool = True
    long_controls: frozenset[str] = frozenset({"select", "back", "home", "siri"})
    double_controls: frozenset[str] = frozenset({"home"})
    repeat_controls: frozenset[str] = NAVIGATION | VOLUME


class Cancellable(Protocol):
    def cancel(self) -> None: ...


Scheduler = Callable[[float, Callable[[], None]], Cancellable]


@dataclass
class _ButtonState:
    down: bool = False
    long_fired: bool = False
    swallow_release: bool = False  # release that ends a double press
    long_timer: Cancellable | None = None
    repeat_timer: Cancellable | None = None
    double_timer: Cancellable | None = None  # pending short awaiting a 2nd press

    def cancel_hold_timers(self) -> None:
        for timer in (self.long_timer, self.repeat_timer):
            if timer is not None:
                timer.cancel()
        self.long_timer = self.repeat_timer = None


@dataclass
class _ClientState:
    buttons: dict[str, _ButtonState] = field(default_factory=dict)
    last_select_release: float | None = None
    volume: float | None = None


class GestureEngine:
    """Stateful per-client gesture recogniser. Call ``feed`` on the event loop."""

    def __init__(
        self,
        emit: Callable[[ControlEvent], None],
        scheduler: Scheduler,
        clock: Callable[[], float] = time.monotonic,
        options: GestureOptions | None = None,
    ) -> None:
        self._emit_cb = emit
        self._schedule = scheduler
        self._clock = clock
        self.options = options or GestureOptions()
        self._clients: dict[str, _ClientState] = {}

    # ---- public -----------------------------------------------------------

    def feed(self, raw: RawInput) -> None:
        client = self._clients.setdefault(raw.client, _ClientState())
        if raw.kind is InputKind.BUTTON:
            control = RAW_TO_CONTROL.get(raw.name, raw.name)
            if raw.pressed:
                self._press(raw.client, client, control)
            else:
                self._release(raw.client, client, control)
        elif raw.kind is InputKind.SWIPE:
            self._emit(raw.client, raw.name, PressType.SHORT)
        elif raw.kind is InputKind.TAP:
            self._tap(raw.client, client)
        elif raw.kind is InputKind.MEDIA:
            self._media(raw, client)
        else:  # InputKind.TEXT
            self._emit(raw.client, "keyboard_text", PressType.TEXT, raw.value)

    def reset(self, client: str | None = None, *, flush: bool = True) -> None:
        """Forget state (e.g. the phone disconnected mid-press).

        With ``flush``, shorts still waiting out a double-tap window are
        emitted rather than lost.
        """
        names = list(self._clients) if client is None else [client]
        for name in names:
            state = self._clients.pop(name, None)
            if state is None:
                continue
            for control, button in state.buttons.items():
                button.cancel_hold_timers()
                if button.double_timer is not None:
                    button.double_timer.cancel()
                    if flush:
                        self._emit(name, control, PressType.SHORT)

    # ---- buttons ----------------------------------------------------------

    def _press(self, client_name: str, client: _ClientState, control: str) -> None:
        opts = self.options
        button = client.buttons.setdefault(control, _ButtonState())
        if button.down:  # missed release (e.g. dropped packet): start over
            button.cancel_hold_timers()
        if button.double_timer is not None:  # second press inside the window
            button.double_timer.cancel()
            button.double_timer = None
            button.down, button.swallow_release = True, True
            self._emit(client_name, DOUBLE_RENAMES.get(control, control), PressType.DOUBLE)
            return

        button.down, button.long_fired, button.swallow_release = True, False, False
        if control in opts.long_controls:
            button.long_timer = self._schedule(
                opts.long_press_ms / 1000, lambda: self._fire_long(client_name, control, button)
            )
        elif control in opts.repeat_controls:
            self._emit(client_name, control, PressType.SHORT)
            button.repeat_timer = self._schedule(
                opts.repeat_delay_ms / 1000, lambda: self._fire_repeat(client_name, control, button)
            )
        elif control not in opts.double_controls:
            self._emit(client_name, control, PressType.SHORT)

    def _release(self, client_name: str, client: _ClientState, control: str) -> None:
        opts = self.options
        if control == "select":
            client.last_select_release = self._clock()
        button = client.buttons.get(control)
        if button is None or not button.down:
            return  # release without a press
        button.down = False
        button.cancel_hold_timers()
        if button.swallow_release or button.long_fired:
            return
        is_long = control in opts.long_controls
        if not is_long and (control in opts.repeat_controls or control not in opts.double_controls):
            return  # already emitted on press
        if control in opts.double_controls:
            button.double_timer = self._schedule(
                opts.double_tap_ms / 1000, lambda: self._fire_pending_short(client_name, control, button)
            )
        else:
            self._emit(client_name, control, PressType.SHORT)

    def _fire_long(self, client_name: str, control: str, button: _ButtonState) -> None:
        button.long_timer = None
        button.long_fired = True
        self._emit(client_name, LONG_RENAMES.get(control, control), PressType.LONG)

    def _fire_repeat(self, client_name: str, control: str, button: _ButtonState) -> None:
        self._emit(client_name, control, PressType.REPEAT)
        button.repeat_timer = self._schedule(
            self.options.repeat_interval_ms / 1000,
            lambda: self._fire_repeat(client_name, control, button),
        )

    def _fire_pending_short(self, client_name: str, control: str, button: _ButtonState) -> None:
        button.double_timer = None
        self._emit(client_name, control, PressType.SHORT)

    # ---- touchpad and media -----------------------------------------------

    def _tap(self, client_name: str, client: _ClientState) -> None:
        if not self.options.tap_selects:
            return
        last = client.last_select_release
        if last is not None and (self._clock() - last) * 1000 < self.options.tap_dedupe_ms:
            return
        self._emit(client_name, "select", PressType.SHORT)

    def _media(self, raw: RawInput, client: _ClientState) -> None:
        value = raw.value if isinstance(raw.value, float) else None
        if raw.name in ("play", "pause"):
            self._emit(raw.client, "play_pause", PressType.SHORT)
        elif raw.name == "skip_by":
            if value:
                control = "skip_forward" if value > 0 else "skip_back"
                self._emit(raw.client, control, PressType.SHORT, abs(value))
        elif raw.name == "set_volume":
            # Absolute slider → relative steps (IR is relative-only).
            if value is not None and client.volume is not None and value != client.volume:
                control = "volume_up" if value > client.volume else "volume_down"
                self._emit(raw.client, control, PressType.SHORT, value)
            client.volume = value
        else:
            self._emit(raw.client, raw.name, PressType.SHORT, raw.value)

    def _emit(
        self, client: str, control: str, press_type: PressType, value: float | str | None = None
    ) -> None:
        self._emit_cb(ControlEvent(control, press_type, client, self._clock(), value))
