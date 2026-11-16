"""Persist one virtual TV's identity, port and paired phones in HA storage."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .protocol.identity import MemoryPairingStore, ReceiverIdentity

SAVE_DELAY = 1.0


class EntryStore(MemoryPairingStore):
    """Pairing store backed by ``.storage/remote_bridge.<entry_id>`` (0600).

    The signing key never goes into the config entry, so it isn't exported
    with diagnostics or entry dumps.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        super().__init__()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}", private=True
        )
        self.identity: ReceiverIdentity | None = None
        self.port: int | None = None

    async def async_load(self, name: str) -> ReceiverIdentity:
        """Load or create the identity; keeps its name in sync with ``name``."""
        data = await self._store.async_load() or {}
        if "identity" in data:
            identity = ReceiverIdentity.from_dict(data["identity"])
        else:
            identity = ReceiverIdentity.generate(name)
        identity.name = name
        self.identity = identity
        self.port = data.get("port")
        self.clients = {c["client_id"]: self.client_from_dict(c) for c in data.get("clients", [])}
        self._changed()
        return identity

    def set_port(self, port: int) -> None:
        if port != self.port:
            self.port = port
            self._changed()

    def _changed(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def _data(self) -> dict[str, Any]:
        assert self.identity is not None
        return {
            "identity": self.identity.to_dict(),
            "port": self.port,
            "clients": [self.client_to_dict(c) for c in self.clients.values()],
        }

    async def async_flush(self) -> None:
        """Write now (unload/reload must not lose a pending delayed save)."""
        await self._store.async_save(self._data())

    async def async_remove(self) -> None:
        await self._store.async_remove()
