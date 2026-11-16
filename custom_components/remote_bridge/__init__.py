"""Remote Bridge: drive Home Assistant from the iPhone's Control Center remote.

One config entry = one virtual TV with its own name, port, identity, pairings
and mappings. ``protocol/`` and ``gesture.py`` have no Home Assistant imports.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .advertiser import Advertiser
from .const import DOMAIN, MANUFACTURER, MODEL, PLATFORMS
from .gesture import GestureEngine
from .mapper import Mapper
from .protocol import CompanionServer, PairingPolicy
from .runtime import RemoteBridgeRuntime
from .store import EntryStore

_LOGGER = logging.getLogger(__name__)

type RemoteBridgeConfigEntry = ConfigEntry[RemoteBridgeRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: RemoteBridgeConfigEntry) -> bool:
    """Start the receiver and advertise it."""
    store = EntryStore(hass, entry.entry_id)
    identity = await store.async_load(entry.title)

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=MODEL,
    )
    runtime = RemoteBridgeRuntime(hass, entry, store, device_id=device.id)
    runtime.engine = GestureEngine(runtime.on_control, hass.loop.call_later)
    runtime.mapper = Mapper(hass, entry.title, runtime.on_input_index)
    runtime.server = CompanionServer(
        identity,
        store,
        on_input=runtime.on_raw_input,
        policy=PairingPolicy(
            allow_pairing=runtime.pairing_allowed,
            on_pin=runtime.on_pin,
            on_paired=runtime.on_paired,
        ),
        on_session_change=runtime.on_session_change,
        run_blocking=hass.async_add_executor_job,
    )

    configured_port = int(entry.data.get(CONF_PORT) or 0)
    port = await _async_start_server(runtime.server, configured_port, store.port)
    store.set_port(port)

    runtime.advertiser = Advertiser(hass, identity, port)
    try:
        await runtime.advertiser.async_start()
    except Exception as err:
        await runtime.server.stop()
        raise ConfigEntryNotReady(f"Could not advertise {entry.title}: {err}") from err

    entry.runtime_data = runtime
    await runtime.async_apply_options()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: RemoteBridgeConfigEntry) -> None:
    """Options changed: apply them without reloading (phones stay connected)."""
    await entry.runtime_data.async_apply_options()


async def _async_start_server(server: CompanionServer, configured: int, remembered: int | None) -> int:
    """Bind the configured port, else the last-used one (stable for phones), else any."""
    if configured:
        try:
            return await server.start(port=configured)
        except OSError as err:
            raise ConfigEntryNotReady(f"Port {configured} is not available: {err}") from err
    if remembered:
        try:
            return await server.start(port=remembered)
        except OSError:
            _LOGGER.debug("Remembered port %s is busy; picking a new one", remembered)
    return await server.start(port=0)


async def async_unload_entry(hass: HomeAssistant, entry: RemoteBridgeConfigEntry) -> bool:
    """Stop advertising and close every connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = entry.runtime_data
        if runtime.advertiser is not None:
            await runtime.advertiser.async_stop()
        await runtime.server.stop()
        runtime.engine.reset(flush=False)
        await runtime.store.async_flush()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: RemoteBridgeConfigEntry) -> None:
    """Delete the stored identity and pairings."""
    await EntryStore(hass, entry.entry_id).async_remove()
