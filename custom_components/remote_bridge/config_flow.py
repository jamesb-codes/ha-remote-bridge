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

from .const import CONF_ALLOW_PAIRING, DOMAIN

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
    """Pairing switch (mapping UI arrives in Phase 4)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input[CONF_ALLOW_PAIRING] and self.config_entry.state is ConfigEntryState.LOADED:
                # Re-opening the window also clears the failed-PIN lockout.
                self.config_entry.runtime_data.server.policy.reset_attempts()
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_ALLOW_PAIRING,
                        default=self.config_entry.options.get(CONF_ALLOW_PAIRING, True),
                    ): selector.BooleanSelector(),
                }
            ),
        )
