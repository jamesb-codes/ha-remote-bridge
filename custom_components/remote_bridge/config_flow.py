"""Config and options flow for Remote Bridge."""

from __future__ import annotations

from typing import Any, override

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOW_PAIRING,
    CONF_DOUBLE_TAP_MS,
    CONF_HOME_DOUBLE,
    CONF_INPUT_MODE,
    CONF_INPUT_SOURCES,
    CONF_LONG_PRESS_MS,
    CONF_MAPPINGS,
    CONF_REPEAT_CONTROLS,
    CONF_REPEAT_DELAY_MS,
    CONF_REPEAT_INTERVAL_MS,
    CONF_SWIPE_THRESHOLD,
    DEFAULT_TIMING,
    DOMAIN,
    INPUT_MODE_CYCLE,
    INPUT_MODE_SINGLE,
    MAPPING_GROUPS,
    MAX_INPUT_SOURCES,
    REPEAT_CAPABLE,
    SOURCE_ACTIONS,
    SOURCE_NAME,
)
from .mapper import async_validate_actions

DEFAULT_NAME = "Living Room TV"


class RemoteBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create one virtual TV per entry."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input[CONF_NAME]).strip()
            port = int(user_input.get(CONF_PORT) or 0)
            if not name:
                errors[CONF_NAME] = "name_required"
            elif any(e.title.casefold() == name.casefold() for e in self._async_current_entries()):
                errors[CONF_NAME] = "name_in_use"
            else:
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name, CONF_PORT: port},
                    options={CONF_ALLOW_PAIRING: True},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_NAME, default=DEFAULT_NAME): selector.TextSelector(),
                    probatio.Optional(CONF_PORT, default=0): selector.NumberSelector(
                        selector.NumberSelectorConfig(min=0, max=65535, mode=selector.NumberSelectorMode.BOX)
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RemoteBridgeOptionsFlow()


class RemoteBridgeOptionsFlow(OptionsFlow):
    """Menu of small pages: button mappings, input mode, timing, pairing."""

    def _options(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[*MAPPING_GROUPS, "input", "timing", "pairing"],
        )

    # ---- button mappings ------------------------------------------------

    async def async_step_navigation(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._mapping_step("navigation", user_input)

    async def async_step_volume_power(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._mapping_step("volume_power", user_input)

    async def async_step_media(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._mapping_step("media", user_input)

    async def _mapping_step(self, group: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        keys = MAPPING_GROUPS[group]
        mappings: dict[str, Any] = dict(self.config_entry.options.get(CONF_MAPPINGS) or {})
        errors: dict[str, str] = {}
        if user_input is not None:
            for key in keys:
                actions = user_input.get(key)
                if not actions:
                    mappings.pop(key, None)
                    continue
                try:
                    await async_validate_actions(self.hass, actions)
                except Exception:
                    errors[key] = "invalid_actions"
                mappings[key] = actions
            if not errors:
                return self.async_create_entry(data={**self._options(), CONF_MAPPINGS: mappings})
        current = user_input if user_input is not None else mappings
        return self.async_show_form(
            step_id=group,
            data_schema=probatio.Schema(
                {
                    probatio.Optional(key, description={"suggested_value": current.get(key)}): (
                        selector.ActionSelector()
                    )
                    for key in keys
                }
            ),
            errors=errors,
        )

    # ---- input / source -------------------------------------------------

    async def async_step_input(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self._options()
        mappings: dict[str, Any] = dict(options.get(CONF_MAPPINGS) or {})
        errors: dict[str, str] = {}
        if user_input is not None:
            to_check: dict[str, Any] = {"input_long": user_input.get("input_long")}
            sources = []
            for n in range(1, MAX_INPUT_SOURCES + 1):
                actions = user_input.get(f"source_{n}_actions")
                if actions:
                    to_check[f"source_{n}_actions"] = actions
                    name = str(user_input.get(f"source_{n}_name") or "").strip() or f"Input {n}"
                    sources.append({SOURCE_NAME: name, SOURCE_ACTIONS: actions})
            for field, actions in to_check.items():
                if actions:
                    try:
                        await async_validate_actions(self.hass, actions)
                    except Exception:
                        errors[field] = "invalid_actions"
            mode = user_input[CONF_INPUT_MODE]
            if mode == INPUT_MODE_CYCLE and not sources:
                errors[CONF_INPUT_MODE] = "no_sources"
            if not errors:
                if user_input.get("input_long"):
                    mappings["input_long"] = user_input["input_long"]
                else:
                    mappings.pop("input_long", None)
                return self.async_create_entry(
                    data={
                        **options,
                        CONF_MAPPINGS: mappings,
                        CONF_INPUT_MODE: mode,
                        CONF_INPUT_SOURCES: sources,
                    }
                )

        if user_input is not None:
            current = user_input
        else:
            current = {
                CONF_INPUT_MODE: options.get(CONF_INPUT_MODE, INPUT_MODE_SINGLE),
                "input_long": mappings.get("input_long"),
            }
            for n, source in enumerate(options.get(CONF_INPUT_SOURCES) or [], start=1):
                current[f"source_{n}_name"] = source.get(SOURCE_NAME)
                current[f"source_{n}_actions"] = source.get(SOURCE_ACTIONS)
        schema: dict[Any, Any] = {
            probatio.Required(CONF_INPUT_MODE, default=current.get(CONF_INPUT_MODE) or INPUT_MODE_SINGLE): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[INPUT_MODE_SINGLE, INPUT_MODE_CYCLE],
                        translation_key=CONF_INPUT_MODE,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            ),
            probatio.Optional("input_long", description={"suggested_value": current.get("input_long")}): (
                selector.ActionSelector()
            ),
        }
        for n in range(1, MAX_INPUT_SOURCES + 1):
            schema[
                probatio.Optional(
                    f"source_{n}_name", description={"suggested_value": current.get(f"source_{n}_name")}
                )
            ] = selector.TextSelector()
            schema[
                probatio.Optional(
                    f"source_{n}_actions", description={"suggested_value": current.get(f"source_{n}_actions")}
                )
            ] = selector.ActionSelector()
        return self.async_show_form(step_id="input", data_schema=probatio.Schema(schema), errors=errors)

    # ---- timing -----------------------------------------------------------

    async def async_step_timing(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self._options()
        if user_input is not None:
            values = {k: int(user_input[k]) for k in DEFAULT_TIMING}
            return self.async_create_entry(
                data={
                    **options,
                    **values,
                    CONF_REPEAT_CONTROLS: list(user_input.get(CONF_REPEAT_CONTROLS, [])),
                    CONF_HOME_DOUBLE: bool(user_input.get(CONF_HOME_DOUBLE, False)),
                }
            )

        def ms(low: int, high: int, step: int = 50) -> selector.NumberSelector:
            return selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=low,
                    max=high,
                    step=step,
                    unit_of_measurement="ms",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        limits = {
            CONF_LONG_PRESS_MS: ms(200, 3000),
            CONF_DOUBLE_TAP_MS: ms(100, 1000),
            CONF_REPEAT_DELAY_MS: ms(100, 3000),
            CONF_REPEAT_INTERVAL_MS: ms(50, 2000),
            CONF_SWIPE_THRESHOLD: selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=40, max=600, step=10, mode=selector.NumberSelectorMode.SLIDER
                )
            ),
        }
        schema: dict[Any, Any] = {
            probatio.Required(key, default=options.get(key, default)): limits[key]
            for key, default in DEFAULT_TIMING.items()
        }
        schema[
            probatio.Optional(
                CONF_REPEAT_CONTROLS, default=list(options.get(CONF_REPEAT_CONTROLS, REPEAT_CAPABLE))
            )
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(REPEAT_CAPABLE), multiple=True, translation_key=CONF_REPEAT_CONTROLS
            )
        )
        schema[probatio.Optional(CONF_HOME_DOUBLE, default=bool(options.get(CONF_HOME_DOUBLE, False)))] = (
            selector.BooleanSelector()
        )
        return self.async_show_form(step_id="timing", data_schema=probatio.Schema(schema))

    # ---- pairing ------------------------------------------------------------

    async def async_step_pairing(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input[CONF_ALLOW_PAIRING] and self.config_entry.state is ConfigEntryState.LOADED:
                # Re-opening the window also clears the failed-PIN lockout.
                self.config_entry.runtime_data.server.policy.reset_attempts()
            return self.async_create_entry(data={**self._options(), **user_input})
        return self.async_show_form(
            step_id="pairing",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_ALLOW_PAIRING,
                        default=self.config_entry.options.get(CONF_ALLOW_PAIRING, True),
                    ): selector.BooleanSelector(),
                }
            ),
        )
