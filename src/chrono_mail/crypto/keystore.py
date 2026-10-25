import base64
import binascii
from typing import Protocol

from chrono_mail.core import SERVICE_NAME, CredentialBackend, CredentialEntries, CredentialFailure, CryptoError
from chrono_mail.crypto.keys import DataKey

# --- Version ------------------------------------------------------------
__version__ = "keystore.py 1.8.263"

# --- Version history ----------------------------------------------------
# v1.8.263: R-16: the credential store is reached through core's one copy (CredentialEntries); the texts are unchanged
# v1.1.197: KeyNotFoundError (the store answered: no such key) and CredentialStoreError (the store could not be asked), each with the store's own reason
# v0.5.133: Every failure of the credential store is a normal error with its reason (KDE Wallet's own errors were "unexpected")
# v0.0.9: CredentialBackend lives in core
# v0.0.6: Initial key stores


class KeyNotFoundError(CryptoError):
    """The credential store answered, and it does not hold the key (a reset wallet, a new computer)."""


class CredentialStoreError(CryptoError):
    """The credential store could not be asked at all (locked, not running yet, refused): the key may well be there."""


class KeyStore(Protocol):
    """Where data keys are kept. Implementations never write keys into plain-text files."""

    def save(self, key: DataKey) -> None: ...

    def load(self, key_id: bytes) -> DataKey:
        """The key with this id. Raises CryptoError when it is not there."""
        ...

    def delete(self, key_id: bytes) -> None:
        """Remove the key. Removing a key that is not there is not an error."""
        ...


class InMemoryKeyStore:
    """Keeps keys in memory only. For tests."""

    def __init__(self) -> None:
        self._keys: dict[bytes, DataKey] = {}

    def save(self, key: DataKey) -> None:
        self._keys[key.key_id] = key

    def load(self, key_id: bytes) -> DataKey:
        try:
            return self._keys[key_id]
        except KeyError:
            raise KeyNotFoundError(f"Key {key_id.hex()} was not found") from None

    def delete(self, key_id: bytes) -> None:
        self._keys.pop(key_id, None)


class KeyringKeyStore:
    """Keeps keys in the operating system's credential store (KWallet, Secret Service, ...)."""

    def __init__(self, service: str = SERVICE_NAME, backend: CredentialBackend | None = None) -> None:
        self._entries: CredentialEntries = CredentialEntries(service, backend)

    @staticmethod
    def _entry_name(key_id: bytes) -> str:
        return f"data-key-{key_id.hex()}"

    def save(self, key: DataKey) -> None:
        encoded = base64.b64encode(key.reveal()).decode("ascii")
        try:
            self._entries.write(self._entry_name(key.key_id), encoded)
        except CredentialFailure as failure:
            raise CryptoError(f"The credential store refused the key: {failure.reason}") from None

    def load(self, key_id: bytes) -> DataKey:
        try:
            encoded = self._entries.read(self._entry_name(key_id))
        except CredentialFailure as failure:
            raise CredentialStoreError(f"The credential store could not be read: {failure.cause}") from None
        if encoded is None:
            raise KeyNotFoundError(f"Key {key_id.hex()} was not found in the credential store")
        try:
            key = DataKey(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError):
            raise CryptoError(f"Key {key_id.hex()} in the credential store is damaged") from None
        if key.key_id != key_id:
            raise CryptoError(f"Key {key_id.hex()} in the credential store is damaged")
        return key

    def delete(self, key_id: bytes) -> None:
        try:
            self._entries.remove(self._entry_name(key_id))
        except CredentialFailure as failure:
            raise CryptoError(f"The credential store could not delete the key: {failure.reason}") from None
