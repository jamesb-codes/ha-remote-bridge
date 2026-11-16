"""Persistent identity of one virtual TV, plus the paired-client key store."""

from __future__ import annotations

import json
import os
import secrets
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


@dataclass
class ReceiverIdentity:
    """Everything that must stay stable across restarts for one virtual TV."""

    name: str
    server_id: str  # Upper-case UUID; pairing identifier (atv-core identity.rs:20-31)
    device_id: str  # MAC-like "AA:BB:CC:DD:EE:FF"; rpBA/rpHA source
    ltsk: bytes = field(repr=False)  # Ed25519 private seed (32 bytes)

    @classmethod
    def generate(cls, name: str) -> ReceiverIdentity:
        mac = bytearray(secrets.token_bytes(6))
        mac[0] = (mac[0] | 0x02) & 0xFE  # locally administered, unicast
        return cls(
            name=name,
            server_id=str(uuid.uuid4()).upper(),
            device_id=":".join(f"{b:02X}" for b in mac),
            ltsk=secrets.token_bytes(32),
        )

    @property
    def pairing_id(self) -> bytes:
        """Identifier bytes used in pairing TLVs (pyatv server_auth.py:80)."""
        return self.server_id.encode()

    @property
    def mac_bytes(self) -> bytes:
        return bytes(int(part, 16) for part in self.device_id.split(":"))

    @property
    def signing_key(self) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(self.ltsk)

    @property
    def ltpk(self) -> bytes:
        return self.signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "server_id": self.server_id,
            "device_id": self.device_id,
            "ltsk": self.ltsk.hex(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReceiverIdentity:
        return cls(
            name=str(data["name"]),
            server_id=str(data["server_id"]),
            device_id=str(data["device_id"]),
            ltsk=bytes.fromhex(str(data["ltsk"])),
        )


@dataclass(frozen=True)
class PairedClient:
    """A phone that completed pair-setup."""

    client_id: str  # pairing identifier the phone sent (decoded as UTF-8)
    ltpk: bytes  # its Ed25519 public key
    name: str | None = None


class PairingStore(Protocol):
    """Where paired-client public keys are kept. HA uses helpers.storage.Store."""

    def get(self, client_id: str) -> PairedClient | None: ...

    def add(self, client: PairedClient) -> None: ...


class MemoryPairingStore:
    """In-memory store; base for the JSON file store and for tests."""

    def __init__(self, clients: dict[str, PairedClient] | None = None) -> None:
        self.clients: dict[str, PairedClient] = dict(clients or {})

    def get(self, client_id: str) -> PairedClient | None:
        return self.clients.get(client_id)

    def add(self, client: PairedClient) -> None:
        self.clients[client.client_id] = client
        self._changed()

    def remove_all(self) -> None:
        self.clients.clear()
        self._changed()

    def _changed(self) -> None:
        """Hook for persistence."""

    @staticmethod
    def client_to_dict(client: PairedClient) -> dict[str, str | None]:
        return {"client_id": client.client_id, "ltpk": client.ltpk.hex(), "name": client.name}

    @staticmethod
    def client_from_dict(data: dict[str, Any]) -> PairedClient:
        return PairedClient(
            client_id=str(data["client_id"]),
            ltpk=bytes.fromhex(str(data["ltpk"])),
            name=data.get("name"),
        )


class JsonFileState(MemoryPairingStore):
    """Identity and pairings in one JSON file (dev CLI only; HA uses Store)."""

    def __init__(self, path: Path, default_name: str) -> None:
        self.path = path
        data: dict[str, Any] = {}
        if path.exists():
            data = json.loads(path.read_text())
        if "identity" in data:
            self.identity = ReceiverIdentity.from_dict(data["identity"])
        else:
            self.identity = ReceiverIdentity.generate(default_name)
        super().__init__({c["client_id"]: self.client_from_dict(c) for c in data.get("clients", [])})
        self._changed()

    def _changed(self) -> None:
        payload = {
            "identity": self.identity.to_dict(),
            "clients": [self.client_to_dict(c) for c in self.clients.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        # Secrets inside: keep the file private to the user.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
        tmp.replace(self.path)
