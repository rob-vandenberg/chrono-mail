import sys
from contextlib import closing

import keyring
import pytest

from chrono_mail.core import CryptoError
from chrono_mail.crypto import KeyringKeyStore, generate_data_key

# --- Version ------------------------------------------------------------
__version__ = "test_os_keyring.py 4.2.459"

# --- Version history ----------------------------------------------------
# v4.2.459: a profile's entry keeps its secret when it is labelled (its wallet name has no "/", D-274)
# v3.6.371: the real wallet shows the label Chrono Mail gave an entry, and the entry is still found by its name (B-23)
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


@pytest.mark.skipif(sys.platform != "linux", reason="labels are kept by the Secret Service (Linux) only")
def test_real_credential_store_shows_the_label() -> None:
    """Creates one entry in the user's real credential store, labels it, reads the label back and removes the entry."""
    from keyring.backends.SecretService import Keyring as SecretServiceKeyring

    backend = keyring.get_keyring()
    if not isinstance(backend, SecretServiceKeyring):
        pytest.skip("the wallet is not reached through the Secret Service")
    store = KeyringKeyStore("chrono-mail-test")
    key = generate_data_key()
    try:
        store.save(key)
        assert store.label(key.key_id, "Chrono Mail test: a label") is True
        collection = backend.get_preferred_collection()
        with closing(collection.connection):
            labels = [item.get_label() for item in collection.search_items(backend._query("chrono-mail-test", f"data-key-{key.key_id.hex()}"))]  # noqa: SLF001
        assert labels == ["Chrono Mail test: a label"]
        assert store.load(key.key_id) == key
    finally:
        store.delete(key.key_id)


@pytest.mark.skipif(sys.platform != "linux", reason="labels are kept by the Secret Service (Linux) only")
def test_a_profiles_entry_keeps_its_secret_when_it_is_labelled(tmp_path: object) -> None:
    """KDE's wallet garbled an entry of a wallet name with "/" when its label changed (D-274): a profile's name has none, and its entry
    reads back the same after the label. Creates one entry in the user's real wallet and removes it."""
    from pathlib import Path

    from chrono_mail.config import profile_directories
    from chrono_mail.core.credentials import CredentialEntries

    entries = CredentialEntries(profile_directories(Path(str(tmp_path)) / "demo").credential_service)
    try:
        entries.write("zz-label-test", "aGVsbG8gd29ybGQ=")
        entries.label("zz-label-test", "Chrono Mail (demo): a test entry")
        assert entries.read("zz-label-test") == "aGVsbG8gd29ybGQ="
    finally:
        entries.remove("zz-label-test")
