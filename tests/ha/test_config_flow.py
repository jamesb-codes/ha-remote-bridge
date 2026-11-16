"""Config and options flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_bridge.const import CONF_ALLOW_PAIRING, DOMAIN


@pytest.fixture(autouse=True)
def _no_setup() -> Generator[None]:
    """Flow tests don't start the receiver."""
    with patch("custom_components.remote_bridge.async_setup_entry", return_value=True):
        yield


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "  Bedroom TV ", CONF_PORT: 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bedroom TV"
    assert result["data"] == {CONF_NAME: "Bedroom TV", CONF_PORT: 0}
    assert result["options"] == {CONF_ALLOW_PAIRING: True}


async def test_user_flow_validates_name(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NAME: "test tv"})
    assert result["errors"] == {CONF_NAME: "name_in_use"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_NAME: "   "})
    assert result["errors"] == {CONF_NAME: "name_required"}


async def test_options_flow_not_loaded(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "pairing"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_ALLOW_PAIRING: False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_ALLOW_PAIRING: False}
