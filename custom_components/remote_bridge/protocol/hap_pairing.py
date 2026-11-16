"""Receiver side of Companion pair-setup and pair-verify.

Message flow and key derivations: docs/protocol-notes.md sections 3-4, from
pyatv companion/server_auth.py:99-217 and atv-core session.rs:371-567.

Unlike both references this module
* verifies the phone's Ed25519 signature in pair-setup M5 and stores its key,
* verifies pair-verify M3 against that stored key (unknown phones are refused),
* uses a random SRP ``b``, a fresh X25519 key per verify, and a random PIN
  per pair-setup attempt that is only accepted while pairing is allowed.

Everything here is synchronous and CPU-bound (SRP takes a few ms), so callers
should run it in an executor, then act on ``pending_pin``/``new_client`` (and
call the policy hooks) back on the event loop.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import opack, tlv8
from .crypto import ChaChaCipher, hkdf_sha512
from .identity import PairedClient, PairingStore, ReceiverIdentity
from .srp import SrpError, SrpServer
from .tlv8 import ErrorCode, Tag

_LOGGER = logging.getLogger(__name__)

# Device model we claim (atv-core session.rs:27). Also used for rpMd.
DEVICE_MODEL = "AppleTV14,1"

# Session-key labels. pyatv companion/protocol.py:40-42 (client view: its
# output is "ClientEncrypt-main"), server_auth.py:131-132.
SERVER_ENCRYPT_INFO = "ServerEncrypt-main"
CLIENT_ENCRYPT_INFO = "ClientEncrypt-main"


def generate_pin() -> str:
    """Random 4-digit PIN in 1000-9999.

    No leading zero, so the zero-padded (pyatv companion/pairing.py:77) and
    unpadded (atv-core srp.rs:78) password encodings agree.
    """
    return str(1000 + secrets.randbelow(9000))


@dataclass
class PairingPolicy:
    """Hooks the host (dev CLI or HA) gives the pairing layer."""

    allow_pairing: Callable[[], bool] = lambda: True
    on_pin: Callable[[str], None] = lambda pin: None
    on_paired: Callable[[PairedClient], None] = lambda client: None
    max_failed_attempts: int = 10
    failed_attempts: int = field(default=0, init=False)

    def reset_attempts(self) -> None:
        self.failed_attempts = 0


def _error(seq: int, code: ErrorCode) -> dict[str, Any]:
    return {"_pd": tlv8.write({Tag.SEQ_NO: bytes([seq]), Tag.ERROR: bytes([code])})}


class PairSetupHandler:
    """Pair-setup state for one connection."""

    def __init__(self, identity: ReceiverIdentity, store: PairingStore, policy: PairingPolicy) -> None:
        self._identity = identity
        self._store = store
        self._policy = policy
        self._srp: SrpServer | None = None
        self._verified = False
        # Outputs for the caller to act on *on the event loop* after handle():
        self.pending_pin: str | None = None
        self.new_client: PairedClient | None = None

    def handle(self, tlv: dict[int, bytes]) -> dict[str, Any]:
        """Handle one incoming pair-setup message. Returns the OPACK reply."""
        self.pending_pin = None
        self.new_client = None
        seq = tlv8.seq_no(tlv)
        if seq == 1:
            return self._m1()
        if seq == 3:
            return self._m3(tlv)
        if seq == 5:
            return self._m5(tlv)
        _LOGGER.debug("Unexpected pair-setup state %s", seq)
        return _error(seq + 1, ErrorCode.UNKNOWN)

    def _m1(self) -> dict[str, Any]:
        self._srp = None
        self._verified = False
        if not self._policy.allow_pairing():
            _LOGGER.info("Pairing request refused: new pairings are not allowed")
            return _error(2, ErrorCode.UNAVAILABLE)
        if self._policy.failed_attempts >= self._policy.max_failed_attempts:
            _LOGGER.warning("Pairing request refused: too many failed attempts")
            return _error(2, ErrorCode.MAX_TRIES)
        pin = generate_pin()
        self._srp = SrpServer(pin)
        self.pending_pin = pin
        reply = tlv8.write(
            {
                Tag.SEQ_NO: b"\x02",
                Tag.SALT: self._srp.salt,
                Tag.PUBLIC_KEY: self._srp.public_key,
                Tag.FLAGS_27: b"\x01",  # pyatv server_auth.py:150
            }
        )
        return {"_pd": reply, "_pwTy": 1}  # _pwTy: pyatv server_auth.py:153

    def _m3(self, tlv: dict[int, bytes]) -> dict[str, Any]:
        if self._srp is None or Tag.PUBLIC_KEY not in tlv or Tag.PROOF not in tlv:
            return _error(4, ErrorCode.UNKNOWN)
        try:
            ok = self._srp.verify(tlv[Tag.PUBLIC_KEY], tlv[Tag.PROOF])
        except SrpError:
            ok = False
        if not ok:
            self._policy.failed_attempts += 1
            self._srp = None  # this PIN is burned; the phone must start over
            _LOGGER.info("Pairing failed: wrong PIN")
            return _error(4, ErrorCode.AUTHENTICATION)
        self._verified = True
        return {"_pd": tlv8.write({Tag.SEQ_NO: b"\x04", Tag.PROOF: self._srp.server_proof})}

    def _m5(self, tlv: dict[int, bytes]) -> dict[str, Any]:
        if self._srp is None or not self._verified or Tag.ENCRYPTED_DATA not in tlv:
            return _error(6, ErrorCode.UNKNOWN)
        shared = self._srp.session_key
        self._srp = None
        self._verified = False
        session_key = hkdf_sha512("Pair-Setup-Encrypt-Salt", "Pair-Setup-Encrypt-Info", shared)
        try:
            inner = tlv8.read(
                ChaChaCipher(session_key, session_key).decrypt(tlv[Tag.ENCRYPTED_DATA], nonce=b"PS-Msg05")
            )
            client_id = inner[Tag.IDENTIFIER]
            client_ltpk = inner[Tag.PUBLIC_KEY]
            signature = inner[Tag.SIGNATURE]
            # Controller signature, pyatv auth/hap_srp.py:171-184.
            ios_device_x = hkdf_sha512(
                "Pair-Setup-Controller-Sign-Salt", "Pair-Setup-Controller-Sign-Info", shared
            )
            Ed25519PublicKey.from_public_bytes(client_ltpk).verify(
                signature, ios_device_x + client_id + client_ltpk
            )
        except InvalidTag, InvalidSignature, KeyError, ValueError:
            self._policy.failed_attempts += 1
            _LOGGER.warning("Pairing failed: client signature or encryption invalid")
            return _error(6, ErrorCode.AUTHENTICATION)

        client = PairedClient(
            client_id=client_id.decode("utf-8", "replace"),
            ltpk=client_ltpk,
            name=_client_name(inner.get(Tag.EXTRA_INFO)),
        )
        self.new_client = client  # caller stores it before sending M6
        self._policy.reset_attempts()

        # Accessory signature, pyatv companion/server_auth.py:177-206.
        identity = self._identity
        accessory_x = hkdf_sha512("Pair-Setup-Accessory-Sign-Salt", "Pair-Setup-Accessory-Sign-Info", shared)
        signature = identity.signing_key.sign(accessory_x + identity.pairing_id + identity.ltpk)
        inner_reply = tlv8.write(
            {
                Tag.IDENTIFIER: identity.pairing_id,
                Tag.PUBLIC_KEY: identity.ltpk,
                Tag.SIGNATURE: signature,
                Tag.EXTRA_INFO: opack.pack(_extra_info(identity)),
            }
        )
        encrypted = ChaChaCipher(session_key, session_key).encrypt(inner_reply, nonce=b"PS-Msg06")
        _LOGGER.info("Paired with %s", client.name or "a new device")
        return {"_pd": tlv8.write({Tag.SEQ_NO: b"\x06", Tag.ENCRYPTED_DATA: encrypted})}


def _extra_info(identity: ReceiverIdentity) -> dict[str, Any]:
    """Device info in M6 TLV 0x11. Keys from pyatv server_auth.py:190-197 and
    atv-core session.rs:449-462. Values are stable per virtual TV instead of
    the references' fixed/random ones."""
    seed = hashlib.sha256(b"remote-bridge:" + identity.ltsk).digest()
    return {
        "altIRK": seed[:16],
        "accountID": str(uuid.UUID(bytes=seed[16:32])).upper(),
        "model": DEVICE_MODEL,
        "wifiMAC": identity.mac_bytes,
        "name": identity.name,
        "mac": identity.mac_bytes,
    }


def _client_name(extra: bytes | None) -> str | None:
    if not extra:
        return None
    try:
        info, _ = opack.unpack(extra)
    except TypeError, ValueError, IndexError, UnicodeDecodeError:
        return None
    if isinstance(info, dict) and isinstance(info.get("name"), str):
        return str(info["name"])
    return None


@dataclass(frozen=True)
class SessionKeys:
    """Keys for the encrypted main channel, from the receiver's point of view."""

    output_key: bytes
    input_key: bytes
    client: PairedClient


class PairVerifyHandler:
    """Pair-verify state for one connection."""

    def __init__(self, identity: ReceiverIdentity, store: PairingStore) -> None:
        self._identity = identity
        self._store = store
        self._shared: bytes | None = None
        self._our_pub: bytes | None = None
        self._client_pub: bytes | None = None

    def handle(self, tlv: dict[int, bytes]) -> tuple[dict[str, Any], SessionKeys | None]:
        seq = tlv8.seq_no(tlv)
        if seq == 1:
            return self._m1(tlv), None
        if seq == 3:
            return self._m3(tlv)
        return _error(seq + 1, ErrorCode.UNKNOWN), None

    def _m1(self, tlv: dict[int, bytes]) -> dict[str, Any]:
        client_pub = tlv.get(Tag.PUBLIC_KEY)
        if client_pub is None or len(client_pub) != 32:
            return _error(2, ErrorCode.UNKNOWN)
        ephemeral = X25519PrivateKey.generate()
        our_pub = ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(client_pub))
        self._shared, self._our_pub, self._client_pub = shared, our_pub, client_pub

        identity = self._identity
        signature = identity.signing_key.sign(our_pub + identity.pairing_id + client_pub)
        session_key = hkdf_sha512("Pair-Verify-Encrypt-Salt", "Pair-Verify-Encrypt-Info", shared)
        encrypted = ChaChaCipher(session_key, session_key).encrypt(
            tlv8.write({Tag.IDENTIFIER: identity.pairing_id, Tag.SIGNATURE: signature}),
            nonce=b"PV-Msg02",
        )
        return {
            "_pd": tlv8.write({Tag.SEQ_NO: b"\x02", Tag.PUBLIC_KEY: our_pub, Tag.ENCRYPTED_DATA: encrypted})
        }

    def _m3(self, tlv: dict[int, bytes]) -> tuple[dict[str, Any], SessionKeys | None]:
        shared, our_pub, client_pub = self._shared, self._our_pub, self._client_pub
        self._shared = self._our_pub = self._client_pub = None
        if shared is None or our_pub is None or client_pub is None:
            return _error(4, ErrorCode.UNKNOWN), None
        session_key = hkdf_sha512("Pair-Verify-Encrypt-Salt", "Pair-Verify-Encrypt-Info", shared)
        try:
            inner = tlv8.read(
                ChaChaCipher(session_key, session_key).decrypt(tlv[Tag.ENCRYPTED_DATA], nonce=b"PV-Msg03")
            )
            client_id = inner[Tag.IDENTIFIER]
            signature = inner[Tag.SIGNATURE]
        except InvalidTag, KeyError:
            _LOGGER.info("Pair-verify failed: cannot decrypt client proof")
            return _error(4, ErrorCode.AUTHENTICATION), None

        client = self._store.get(client_id.decode("utf-8", "replace"))
        if client is None:
            _LOGGER.info("Pair-verify refused: device is not paired")
            return _error(4, ErrorCode.AUTHENTICATION), None
        try:
            # Client signs client_pub + id + server_pub (pyatv hap_srp.py:111-115).
            Ed25519PublicKey.from_public_bytes(client.ltpk).verify(
                signature, client_pub + client_id + our_pub
            )
        except InvalidSignature, ValueError:
            _LOGGER.warning("Pair-verify refused: bad signature from a paired id")
            return _error(4, ErrorCode.AUTHENTICATION), None

        keys = SessionKeys(
            output_key=hkdf_sha512("", SERVER_ENCRYPT_INFO, shared),
            input_key=hkdf_sha512("", CLIENT_ENCRYPT_INFO, shared),
            client=client,
        )
        return {"_pd": tlv8.write({Tag.SEQ_NO: b"\x04"})}, keys
