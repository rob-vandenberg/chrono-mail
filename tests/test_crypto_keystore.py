import base64

import keyring.errors
import pytest

from chrono_mail.core import CryptoError
from chrono_mail.crypto import InMemoryKeyStore, KeyringKeyStore, KeyStore, generate_data_key

# --- Version ------------------------------------------------------------
__version__ = "test_crypto_keystore.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial key store tests


class MemoryBackend:
    """A credential store that keeps entries in a dictionary (not a KeyringBackend: see CredentialBackend)."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.entries:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.entries[(service, username)]


class BrokenBackend:
    """A credential store that is locked or unavailable."""

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringLocked("locked")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.PasswordSetError("refused")

    def delete_password(self, service: str, username: str) -> None:
        raise keyring.errors.KeyringLocked("locked")


def make_stores() -> list[KeyStore]:
    return [InMemoryKeyStore(), KeyringKeyStore("chrono-mail-test", MemoryBackend())]


def test_save_and_load() -> None:
    for store in make_stores():
        key = generate_data_key()
        store.save(key)
        assert store.load(key.key_id) == key


def test_missing_key_raises_crypto_error() -> None:
    for store in make_stores():
        with pytest.raises(CryptoError, match="not found"):
            store.load(b"\x00" * 8)


def test_delete_removes_the_key_and_is_idempotent() -> None:
    for store in make_stores():
        key = generate_data_key()
        store.save(key)
        store.delete(key.key_id)
        store.delete(key.key_id)
        with pytest.raises(CryptoError, match="not found"):
            store.load(key.key_id)


def test_several_keys_live_side_by_side() -> None:
    for store in make_stores():
        first, second = generate_data_key(), generate_data_key()
        store.save(first)
        store.save(second)
        assert store.load(first.key_id) == first
        assert store.load(second.key_id) == second


def test_keyring_entry_is_base64_text_under_the_service_name() -> None:
    backend = MemoryBackend()
    store = KeyringKeyStore("chrono-mail-test", backend)
    key = generate_data_key()
    store.save(key)
    ((service, name), value), = backend.entries.items()
    assert service == "chrono-mail-test"
    assert name == f"data-key-{key.key_id.hex()}"
    assert base64.b64decode(value) == key.reveal()


@pytest.mark.parametrize("damaged", ["not base64 !!!", base64.b64encode(b"short").decode(), ""])
def test_damaged_entry_raises_crypto_error(damaged: str) -> None:
    backend = MemoryBackend()
    store = KeyringKeyStore("chrono-mail-test", backend)
    key = generate_data_key()
    backend.entries[("chrono-mail-test", f"data-key-{key.key_id.hex()}")] = damaged
    with pytest.raises(CryptoError):
        store.load(key.key_id)


def test_entry_holding_another_key_is_rejected() -> None:
    backend = MemoryBackend()
    store = KeyringKeyStore("chrono-mail-test", backend)
    wanted, other = generate_data_key(), generate_data_key()
    backend.entries[("chrono-mail-test", f"data-key-{wanted.key_id.hex()}")] = base64.b64encode(other.reveal()).decode()
    with pytest.raises(CryptoError):
        store.load(wanted.key_id)


def test_unavailable_credential_store_raises_crypto_error_without_key_bytes() -> None:
    store = KeyringKeyStore("chrono-mail-test", BrokenBackend())
    key = generate_data_key()
    for action in (lambda: store.save(key), lambda: store.load(key.key_id), lambda: store.delete(key.key_id)):
        with pytest.raises(CryptoError) as raised:
            action()
        assert key.reveal().hex() not in str(raised.value)
