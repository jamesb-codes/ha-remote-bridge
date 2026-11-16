"""HKDF and ChaCha20-Poly1305 helpers for Companion pairing and framing."""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NONCE_LENGTH = 12  # pyatv support/chacha20.py:9
AUTH_TAG_LENGTH = 16  # pyatv companion/connection.py:16


def hkdf_sha512(salt: str, info: str, secret: bytes) -> bytes:
    """HKDF-SHA512 with a 32-byte output (pyatv auth/hap_srp.py:32-41)."""
    return HKDF(
        algorithm=hashes.SHA512(),
        length=32,
        salt=salt.encode(),
        info=info.encode(),
    ).derive(secret)


def _pad_nonce(nonce: bytes) -> bytes:
    """Left-pad with zeros to 12 bytes (pyatv chacha20.py:49-51)."""
    return b"\x00" * (NONCE_LENGTH - len(nonce)) + nonce


class ChaChaCipher:
    """ChaCha20-Poly1305 with separate in/out keys and counter nonces.

    ``counter_bytes`` = 8 gives pair-setup/verify behaviour (4 zero bytes +
    8-byte LE counter, pyatv chacha20.py:76-106). 12 gives the main session
    channel (12-byte LE counter, pyatv companion/connection.py:92).
    """

    def __init__(self, out_key: bytes, in_key: bytes, counter_bytes: int = 8) -> None:
        self._out = ChaCha20Poly1305(out_key)
        self._in = ChaCha20Poly1305(in_key)
        self._out_counter = 0
        self._in_counter = 0
        self._counter_bytes = counter_bytes

    def _counter_nonce(self, counter: int) -> bytes:
        return _pad_nonce(counter.to_bytes(self._counter_bytes, "little"))

    def encrypt(self, data: bytes, nonce: bytes | None = None, aad: bytes | None = None) -> bytes:
        if nonce is None:
            nonce = self._counter_nonce(self._out_counter)
            self._out_counter += 1
        return self._out.encrypt(_pad_nonce(nonce), data, aad)

    def decrypt(self, data: bytes, nonce: bytes | None = None, aad: bytes | None = None) -> bytes:
        if nonce is None:
            nonce = self._counter_nonce(self._in_counter)
            self._in_counter += 1
        return self._in.decrypt(_pad_nonce(nonce), data, aad)
