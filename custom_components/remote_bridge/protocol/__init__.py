"""Companion Link receiver (no Home Assistant imports)."""

from .companion_server import ClientConnection, CompanionServer
from .hap_pairing import PairingPolicy
from .hid import InputKind, RawInput
from .identity import MemoryPairingStore, PairedClient, PairingStore, ReceiverIdentity

__all__ = [
    "ClientConnection",
    "CompanionServer",
    "InputKind",
    "MemoryPairingStore",
    "PairedClient",
    "PairingPolicy",
    "PairingStore",
    "RawInput",
    "ReceiverIdentity",
]
