"""SRP-6a server for Apple pair-setup.

Parameters and formulas follow srptools as pyatv configures it
(pyatv companion/server_auth.py:47-71) and atv-core's port of it
(atv-core crates/atv-core/src/srp.rs:1-171):
SHA-512, RFC 5054 3072-bit group, g = 5, username "Pair-Setup", 128-bit salt.

Differences from the references:
* ``b`` is random per session (both references reuse a fixed key seed).
* The salt and ``B`` are generated so their minimal and padded encodings are
  the same, and verifying M1 accepts both the minimal and padded encodings of
  ``A``/``S``. That way we work whichever convention the client uses.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# RFC 5054 3072-bit prime (srptools PRIME_3072; atv-core srp.rs:20-31).
N = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA6"
    "3B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F2411"
    "7C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08"
    "CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D"
    "04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864D8760273"
    "3EC86A64521F2B18177B200CBBE117577A615D6C770988C0BAD946E208E24FA074E5AB31"
    "43DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF",
    16,
)
G = 5  # srptools PRIME_3072_GEN; atv-core srp.rs:33
USERNAME = b"Pair-Setup"  # pyatv companion/server_auth.py:49
SALT_BYTES = 16  # bits_salt=128, pyatv companion/server_auth.py:55
N_BYTES = (N.bit_length() + 7) // 8  # 384


def _h(*parts: bytes) -> bytes:
    digest = hashlib.sha512()
    for part in parts:
        digest.update(part)
    return digest.digest()


def _to_bytes(value: int) -> bytes:
    """Minimal big-endian bytes (srptools int_to_bytes; atv-core srp.rs:36-42)."""
    if value == 0:
        return b"\x00"
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def _pad(value: int) -> bytes:
    return value.to_bytes(N_BYTES, "big")


_K = int.from_bytes(_h(_to_bytes(N), _pad(G)), "big")
_HN_XOR_HG = _to_bytes(int.from_bytes(_h(_to_bytes(N)), "big") ^ int.from_bytes(_h(_to_bytes(G)), "big"))
_H_USERNAME = _h(USERNAME)


class SrpError(Exception):
    """SRP failure (bad client public key)."""


class SrpServer:
    """One pair-setup SRP session (srptools SRPServerSession equivalent)."""

    def __init__(self, pin: str, *, salt: bytes | None = None, private: int | None = None) -> None:
        self.salt = salt if salt is not None else _random_salt()
        x = int.from_bytes(_h(self.salt, _h(USERNAME + b":" + pin.encode())), "big")
        self._verifier = pow(G, x, N)
        while True:
            self._b = private if private is not None else secrets.randbits(256)
            self._public = (_K * self._verifier + pow(G, self._b, N)) % N
            if private is not None or self._public.bit_length() > (N_BYTES - 1) * 8:
                break
        self._session_key: bytes | None = None
        self._m2: bytes | None = None

    @property
    def public_key(self) -> bytes:
        """B, as sent in M2 (minimal encoding)."""
        return _to_bytes(self._public)

    def verify(self, client_public: bytes, client_proof: bytes) -> bool:
        """Process A and check the client proof M1. Returns True if it matches."""
        a = int.from_bytes(client_public, "big")
        if a % N == 0:
            raise SrpError("client public key is zero mod N")
        u = int.from_bytes(_h(_pad(a), _pad(self._public)), "big")
        premaster = pow(a * pow(self._verifier, u, N) % N, self._b, N)

        a_variants = {_to_bytes(a), client_public}
        s_variants = {_to_bytes(premaster), _pad(premaster)}
        for s_bytes in s_variants:
            key = _h(s_bytes)
            for a_bytes in a_variants:
                m1 = _h(_HN_XOR_HG, _H_USERNAME, self.salt, a_bytes, self.public_key, key)
                if hmac.compare_digest(m1, client_proof):
                    self._session_key = key
                    self._m2 = _h(a_bytes, m1, key)
                    return True
        return False

    @property
    def server_proof(self) -> bytes:
        """M2 = H(A | M1 | K) (srptools key_proof_hash; atv-core srp.rs:150-162)."""
        if self._m2 is None:
            raise SrpError("proof not verified")
        return self._m2

    @property
    def session_key(self) -> bytes:
        """K = H(S), 64 bytes."""
        if self._session_key is None:
            raise SrpError("proof not verified")
        return self._session_key


def _random_salt() -> bytes:
    """128-bit salt with a non-zero first byte, so all encodings agree."""
    while True:
        salt = secrets.token_bytes(SALT_BYTES)
        if salt[0]:
            return salt
