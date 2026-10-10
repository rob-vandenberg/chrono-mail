import pytest

from chrono_mail.core import CryptoError
from chrono_mail.crypto import KeyringKeyStore, generate_data_key

# --- Version ------------------------------------------------------------
__version__ = "test_os_keyring.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial operating system credential store tests

pytestmark = pytest.mark.oskeyring


def test_real_credential_store_round_trip() -> None:
    """Creates one entry in the user's real credential store, reads it back and removes it."""
    store = KeyringKeyStore("chrono-mail-test")
    key = generate_data_key()
    try:
        store.save(key)
        assert store.load(key.key_id) == key
    finally:
        store.delete(key.key_id)
    with pytest.raises(CryptoError, match="not found"):
        store.load(key.key_id)
