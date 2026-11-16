"""Advertise a virtual TV through Home Assistant's shared zeroconf instance."""

from __future__ import annotations

import logging
from ipaddress import IPv4Address, ip_address

from homeassistant.components import network, zeroconf
from homeassistant.core import HomeAssistant
from zeroconf.asyncio import AsyncServiceInfo

from .protocol.identity import ReceiverIdentity
from .protocol.mdns import service_specs

_LOGGER = logging.getLogger(__name__)


class Advertiser:
    """Register/unregister the Companion service. Never creates its own Zeroconf."""

    def __init__(self, hass: HomeAssistant, identity: ReceiverIdentity, port: int) -> None:
        self._hass = hass
        self._identity = identity
        self._port = port
        self._infos: list[AsyncServiceInfo] = []

    async def async_start(self) -> None:
        aiozc = await zeroconf.async_get_async_instance(self._hass)
        # The Companion server listens on IPv4 only (protocol/companion_server.py).
        addresses = [
            addr.packed
            for addr in map(ip_address, await network.async_get_announce_addresses(self._hass))
            if isinstance(addr, IPv4Address)
        ]
        if not addresses:
            _LOGGER.warning("No IPv4 address to advertise %s on", self._identity.name)
        instance = self._identity.name.replace(".", " ")
        host = f"remote-bridge-{self._identity.device_id.replace(':', '').lower()}.local."
        self._infos = [
            AsyncServiceInfo(
                spec.service_type,
                f"{instance}.{spec.service_type}",
                addresses=addresses,
                port=spec.port,
                properties=spec.properties,
                server=host,
            )
            for spec in service_specs(self._identity, self._port)
        ]
        for info in self._infos:
            await aiozc.async_register_service(info, allow_name_change=True)
        _LOGGER.debug("Advertised %s on port %s", self._identity.name, self._port)

    async def async_stop(self) -> None:
        if not self._infos:
            return
        aiozc = await zeroconf.async_get_async_instance(self._hass)
        for info in self._infos:
            await aiozc.async_unregister_service(info)
        self._infos = []
