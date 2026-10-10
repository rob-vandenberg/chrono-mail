import base64
import binascii
from typing import Protocol

import keyring
import keyring.errors

from chrono_mail.core import CryptoError
from chrono_mail.crypto.keys import DataKey

# --- Version ------------------------------------------------------------
__version__ = "keystore.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial key stores

SERVICE_NAME: str = "chrono-mail"


class CredentialBackend(Protocol):
    """What KeyringKeyStore needs from a credential store. Every keyring backend provides it.

    A protocol, not a KeyringBackend subclass: keyring registers every subclass globally,
    which would let a test double leak into the real credential store lookup.
    """

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


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
            raise CryptoError(f"Key {key_id.hex()} was not found") from None

    def delete(self, key_id: bytes) -> None:
        self._keys.pop(key_id, None)


class KeyringKeyStore:
    """Keeps keys in the operating system's credential store (KWallet, Secret Service, ...)."""

    def __init__(self, service: str = SERVICE_NAME, backend: CredentialBackend | None = None) -> None:
        self._service: str = service
        self._backend: CredentialBackend = backend if backend is not None else keyring.get_keyring()

    @staticmethod
    def _entry_name(key_id: bytes) -> str:
        return f"data-key-{key_id.hex()}"

    def save(self, key: DataKey) -> None:
        encoded = base64.b64encode(key.reveal()).decode("ascii")
        try:
            self._backend.set_password(self._service, self._entry_name(key.key_id), encoded)
        except keyring.errors.KeyringError as error:
            raise CryptoError(f"The credential store refused the key: {type(error).__name__}") from None

    def load(self, key_id: bytes) -> DataKey:
        try:
            encoded = self._backend.get_password(self._service, self._entry_name(key_id))
        except keyring.errors.KeyringError as error:
            raise CryptoError(f"The credential store could not be read: {type(error).__name__}") from None
        if encoded is None:
            raise CryptoError(f"Key {key_id.hex()} was not found in the credential store")
        try:
            key = DataKey(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError):
            raise CryptoError(f"Key {key_id.hex()} in the credential store is damaged") from None
        if key.key_id != key_id:
            raise CryptoError(f"Key {key_id.hex()} in the credential store is damaged")
        return key

    def delete(self, key_id: bytes) -> None:
        try:
            self._backend.delete_password(self._service, self._entry_name(key_id))
        except keyring.errors.PasswordDeleteError:
            return  # already gone
        except keyring.errors.KeyringError as error:
            raise CryptoError(f"The credential store could not delete the key: {type(error).__name__}") from None
