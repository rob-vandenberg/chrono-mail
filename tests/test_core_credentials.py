import pytest

from chrono_mail.config.passwords import KeyringPasswordStore, ServerRole
from chrono_mail.core import ConfigurationError, CredentialEntries, CredentialFailure, CryptoError
from chrono_mail.crypto import CredentialStoreError, KeyringKeyStore, generate_data_key
from tests.doubles import BrokenBackend, MemoryBackend, SessionLostBackend

# --- Version ------------------------------------------------------------
__version__ = "test_core_credentials.py 2.0.295"

# --- Version history ----------------------------------------------------
# v2.0.295: a store that never answers gives a clear failure after the time limit; a late answer is ignored
# v1.8.263: Initial tests of the one copy of the credential store access (R-16)


def test_an_entry_is_written_read_and_removed_and_removing_it_twice_is_no_error() -> None:
    entries = CredentialEntries("chrono-mail-test", MemoryBackend())
    entries.write("one", "secret")
    assert entries.read("one") == "secret"
    entries.remove("one")
    entries.remove("one")
    assert entries.read("one") is None


def test_every_failure_of_the_store_is_a_credential_failure_with_its_own_reason() -> None:
    for backend in (BrokenBackend(), SessionLostBackend()):
        entries = CredentialEntries("chrono-mail-test", backend)
        for action in (lambda: entries.read("one"), lambda: entries.write("one", "pa55-w0rd-x7"), lambda: entries.remove("one")):
            with pytest.raises(CredentialFailure) as raised:
                action()
            assert raised.value.cause and "pa55-w0rd-x7" not in raised.value.cause
            assert raised.value.reason == f"{raised.value.cause}. Try again"
    with pytest.raises(CredentialFailure, match="^KeyringLocked: locked$"):
        CredentialEntries("chrono-mail-test", BrokenBackend()).read("one")


def test_the_key_store_and_the_password_store_keep_their_own_words() -> None:
    key = generate_data_key()
    keys = KeyringKeyStore("chrono-mail-test", BrokenBackend())
    with pytest.raises(CredentialStoreError, match="^The credential store could not be read: KeyringLocked: locked$"):
        keys.load(key.key_id)
    with pytest.raises(CryptoError, match="^The credential store refused the key: PasswordSetError: refused. Try again$"):
        keys.save(key)
    with pytest.raises(CryptoError, match="^The credential store could not delete the key: KeyringLocked: locked. Try again$"):
        keys.delete(key.key_id)
    passwords = KeyringPasswordStore("chrono-mail-test", BrokenBackend())
    with pytest.raises(ConfigurationError, match="^The credential store could not be read: KeyringLocked: locked. Try again$"):
        passwords.get("a" * 32, ServerRole.INCOMING)
    with pytest.raises(ConfigurationError, match="^The credential store refused the password: PasswordSetError: refused. Try again$"):
        passwords.set("a" * 32, ServerRole.INCOMING, "secret")
    with pytest.raises(ConfigurationError, match="^The credential store could not delete the password: KeyringLocked: locked. Try again$"):
        passwords.delete("a" * 32, ServerRole.INCOMING)


class SilentBackend(MemoryBackend):
    """A store that never answers (a wallet waiting to be unlocked, a desktop service that hangs) until it is let go."""

    def __init__(self) -> None:
        super().__init__()
        import threading

        self.let_go = threading.Event()

    def get_password(self, service: str, username: str) -> str | None:
        self.let_go.wait(10)
        return super().get_password(service, username)


def test_a_store_that_never_answers_is_a_clear_failure_after_the_time_limit() -> None:
    backend = SilentBackend()
    entries = CredentialEntries("chrono-mail-test", backend, time_limit=0.2)  # the real limit is 60 s: time to unlock the wallet
    with pytest.raises(CredentialFailure, match="did not answer within 0.2 seconds"):
        entries.read("key")
    backend.let_go.set()  # its late answer goes nowhere
    entries.write("key", "value")  # writing never waited: the store still works
    assert CredentialEntries("chrono-mail-test", backend).read("key") == "value"
