"""Fixtures for the Home Assistant integration tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_bridge.const import CONF_ALLOW_PAIRING, DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
def zeroconf(mock_async_zeroconf: MagicMock) -> Generator[MagicMock]:
    """Shared HA zeroconf (mocked) + a fixed loopback announce address."""
    with patch(
        "homeassistant.components.network.async_get_announce_addresses",
        return_value=["127.0.0.1", "fe80::1"],
    ):
        yield mock_async_zeroconf


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test TV",
        data={CONF_NAME: "Test TV", CONF_PORT: 0},
        options={CONF_ALLOW_PAIRING: True},
    )
    config_entry.add_to_hass(hass)
    return config_entry
