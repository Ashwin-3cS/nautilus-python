"""
Cryptographic utilities for nautilus-python.

Ed25519 key generation, blake2b256 hashing, and signing.
Uses pynacl (libsodium bindings) for Ed25519.
"""

import hashlib
from nacl.signing import SigningKey


class KeyPair:
    """Ed25519 keypair for enclave signing."""

    def __init__(self):
        self._signing_key = SigningKey.generate()
        self._verify_key = self._signing_key.verify_key

    @property
    def public_key(self) -> bytes:
        """32-byte Ed25519 public key."""
        return bytes(self._verify_key)

    @property
    def public_key_hex(self) -> str:
        return self.public_key.hex()

    def sign(self, data: bytes) -> bytes:
        """
        Sign data using blake2b256 hash + Ed25519.

        Matches the TS template pattern:
        1. Hash data with blake2b-256
        2. Sign the 32-byte hash with Ed25519
        3. Return 64-byte signature

        This is compatible with on-chain verify_signed_data<T>.
        """
        digest = blake2b256(data)
        signed = self._signing_key.sign(digest)
        # pynacl returns signature + message; extract just the 64-byte signature
        return signed.signature


def blake2b256(data: bytes) -> bytes:
    """Compute blake2b-256 hash (32 bytes). Matches Sui's blake2b256."""
    return hashlib.blake2b(data, digest_size=32).digest()
