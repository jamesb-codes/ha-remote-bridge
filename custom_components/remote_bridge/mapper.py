"""Run the user's mapped Home Assistant actions for each control press."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.script import (
    SCRIPT_MODE_PARALLEL,
    Script,
    async_validate_actions_config,
)

from .const import (
    CONF_INPUT_MODE,
    CONF_INPUT_SOURCES,
    CONF_MAPPINGS,
    DOMAIN,
    INPUT_MODE_CYCLE,
    SOURCE_ACTIONS,
    SOURCE_NAME,
)
from .gesture import ControlEvent, PressType

_LOGGER = logging.getLogger(__name__)

MAX_PARALLEL_RUNS = 10


async def async_validate_actions(hass: HomeAssistant, actions: Any) -> list[dict[str, Any]]:
    """Validate an action-selector value; raises on invalid config."""
    validated: list[dict[str, Any]] = await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(actions))
    return validated


class Mapper:
    """Maps ``(control, press_type)`` to a Script and runs it.

    * ``repeat`` presses fall back to the control's ``short`` mapping, so a held
      volume key keeps stepping without a separate mapping.
    * In cycle mode, ``input``/``long`` runs the next input source (wrapping) and
      reports the new index through ``on_input_index``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        on_input_index: Callable[[int], None],
    ) -> None:
        self._hass = hass
        self._name = name
        self._on_input_index = on_input_index
        self._scripts: dict[str, Script] = {}
        self._sources: list[tuple[str, Script]] = []
        self.cycle_mode = False
        self.input_index = 0

    @property
    def source_names(self) -> list[str]:
        return [name for name, _ in self._sources]

    def has_mapping(self, key: str) -> bool:
        return key in self._scripts

    async def async_update(self, options: Mapping[str, Any]) -> None:
        """(Re)build scripts from entry options. Invalid ones are skipped and logged."""
        scripts: dict[str, Script] = {}
        for key, actions in (options.get(CONF_MAPPINGS) or {}).items():
            if script := await self._build(key, actions):
                scripts[key] = script
        sources: list[tuple[str, Script]] = []
        for i, source in enumerate(options.get(CONF_INPUT_SOURCES) or []):
            name = str(source.get(SOURCE_NAME) or f"Input {i + 1}")
            if script := await self._build(f"input source {name}", source.get(SOURCE_ACTIONS)):
                sources.append((name, script))
        self._scripts = scripts
        self._sources = sources
        self.cycle_mode = options.get(CONF_INPUT_MODE) == INPUT_MODE_CYCLE and bool(sources)
        if self.input_index >= len(sources):
            self.input_index = 0

    async def _build(self, label: str, actions: Any) -> Script | None:
        if not actions:
            return None
        try:
            sequence = await async_validate_actions(self._hass, actions)
        except Exception as err:  # invalid stored config must not break the TV
            _LOGGER.error("%s: ignoring invalid actions for %s: %s", self._name, label, err)
            return None
        return Script(
            self._hass,
            sequence,
            f"{self._name} {label}",
            DOMAIN,
            script_mode=SCRIPT_MODE_PARALLEL,
            max_runs=MAX_PARALLEL_RUNS,
            logger=_LOGGER,
        )

    @callback
    def set_input_index(self, index: int) -> None:
        """Set the current input (e.g. restored state or picked in the UI); runs nothing."""
        if 0 <= index < len(self._sources):
            self.input_index = index

    @callback
    def handle(self, event: ControlEvent, variables: dict[str, Any]) -> None:
        if event.control == "input" and event.press_type is PressType.LONG and self.cycle_mode:
            self.input_index = (self.input_index + 1) % len(self._sources)
            name, source_script = self._sources[self.input_index]
            self._on_input_index(self.input_index)
            self._start(source_script, {**variables, "input_source": name})
            return
        key = f"{event.control}_{event.press_type.value}"
        script = self._scripts.get(key)
        if script is None and event.press_type is PressType.REPEAT:
            script = self._scripts.get(f"{event.control}_{PressType.SHORT.value}")
        if script is not None:
            self._start(script, variables)

    def _start(self, script: Script, variables: dict[str, Any]) -> None:
        self._hass.async_create_background_task(
            self._run(script, variables), f"{DOMAIN} {script.name}", eager_start=True
        )

    async def _run(self, script: Script, variables: dict[str, Any]) -> None:
        try:
            await script.async_run(variables, context=Context())
        except Exception:
            # A failing action (e.g. IR hub offline) must never drop the session.
            _LOGGER.exception("%s: action %s failed", self._name, script.name)
