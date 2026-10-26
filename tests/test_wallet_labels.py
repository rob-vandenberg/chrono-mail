import threading
from dataclasses import replace
from pathlib import Path

import pytest

from chrono_mail.config import AccountSettings, AppDirectories, InMemoryPasswordStore, KeyringPasswordStore, ServerRole, profile_directories
from chrono_mail.core import Account, AccountKind, ConnectionSecurity, CredentialEntries, CredentialFailure, ServerSettings
from chrono_mail.crypto import InMemoryKeyStore, KeyringKeyStore, generate_data_key
from chrono_mail.engine import Engine
from chrono_mail.engine.walletlabels import LABEL_LOCK, key_label, label_wallet, password_label
from keyring.backends.chainer import ChainerBackend
from tests.doubles import MemoryBackend
from tests.enginefactory import make_engine
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

# --- Version ------------------------------------------------------------
__version__ = "test_wallet_labels.py 3.6.371"

# --- Version history ----------------------------------------------------
# v3.6.371: Initial: readable labels for Chrono Mail's entries in the wallet (B-23, D-275)


class LabelledBackend(MemoryBackend):
    """A credential store whose entries carry a label of their own, as the Secret Service's do."""

    def __init__(self) -> None:
        super().__init__()
        self.labels: dict[tuple[str, str], str] = {}

    def set_label(self, service: str, username: str, label: str) -> None:
        if (service, username) in self.entries:
            self.labels[(service, username)] = label


class RefusingLabels(MemoryBackend):
    """A store that keeps passwords but refuses every label (a wallet whose unlock was refused), counting how often it was asked."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: int = 0

    def set_label(self, service: str, username: str, label: str) -> None:
        self.asked += 1
        raise RuntimeError("the unlock was refused")


def _passwords(engine: Engine) -> InMemoryPasswordStore:
    store = engine.passwords
    assert isinstance(store, InMemoryPasswordStore)
    return store


def _keys(engine: Engine) -> InMemoryKeyStore:
    store = engine.keys
    assert isinstance(store, InMemoryKeyStore)
    return store


def test_an_entry_gets_its_label_and_is_still_found_by_its_name() -> None:
    backend = LabelledBackend()
    entries = CredentialEntries("chrono-mail-test", backend)
    entries.write("password-one", "secret")
    entries.label("password-one", "Chrono Mail: me@example.com (incoming mail server)")
    entries.label("password-two", "nothing is made for an entry that is not there")
    assert backend.labels == {("chrono-mail-test", "password-one"): "Chrono Mail: me@example.com (incoming mail server)"}
    assert entries.read("password-one") == "secret"


def test_a_store_without_labels_is_left_as_it_is() -> None:
    backend = MemoryBackend()
    entries = CredentialEntries("chrono-mail-test", backend)
    entries.write("password-one", "secret")
    entries.label("password-one", "Chrono Mail: me@example.com (incoming mail server)")
    assert backend.entries == {("chrono-mail-test", "password-one"): "secret"}


def test_a_label_that_cannot_be_written_never_fails_the_stores() -> None:
    with pytest.raises(CredentialFailure, match="^RuntimeError: the unlock was refused$"):
        CredentialEntries("chrono-mail-test", RefusingLabels()).label("password-one", "a label")
    passwords = KeyringPasswordStore("chrono-mail-test", RefusingLabels())
    passwords.set("a" * 32, ServerRole.INCOMING, "secret")
    assert passwords.label("a" * 32, ServerRole.INCOMING, "a label") is False
    assert passwords.get("a" * 32, ServerRole.INCOMING) == "secret"
    keys = KeyringKeyStore("chrono-mail-test", RefusingLabels())
    key = generate_data_key()
    keys.save(key)
    assert keys.label(key.key_id, "a label") is False
    assert keys.load(key.key_id) == key


def test_a_new_account_and_the_key_are_labelled_as_they_are_stored(setup: Setup) -> None:
    labels = _passwords(setup.engine).labels
    assert labels == {
        (setup.account_id, ServerRole.INCOMING): "Chrono Mail: me@example.com (incoming mail server)",
        (setup.account_id, ServerRole.OUTGOING): "Chrono Mail: me@example.com (outgoing mail server)",
    }
    key_id = setup.engine.config().key_id
    assert key_id is not None
    assert _keys(setup.engine).labels == {bytes.fromhex(key_id): "Chrono Mail: the key that encrypts your mail"}


def test_a_new_name_or_address_takes_the_labels_along(setup: Setup) -> None:
    call(setup.engine, "account.edit", account=setup.account_id, account_name="Work")
    assert _passwords(setup.engine).labels[(setup.account_id, ServerRole.INCOMING)] == "Chrono Mail: Work, me@example.com (incoming mail server)"
    call(setup.engine, "account.edit", account=setup.account_id, email_address="anna@example.com")
    assert _passwords(setup.engine).labels[(setup.account_id, ServerRole.OUTGOING)] == "Chrono Mail: Work, anna@example.com (outgoing mail server)"
    call(setup.engine, "account.edit", account=setup.account_id, account_name="")
    assert _passwords(setup.engine).labels[(setup.account_id, ServerRole.INCOMING)] == "Chrono Mail: anna@example.com (incoming mail server)"


def test_the_start_labels_the_entries_of_an_older_version_and_changes_no_password(setup: Setup) -> None:
    passwords, keys = _passwords(setup.engine), _keys(setup.engine)
    expected_passwords, expected_keys = dict(passwords.labels), dict(keys.labels)
    passwords.labels.clear()
    keys.labels.clear()
    result = call(setup.engine, "wallet.label")
    assert getattr(result, "accounts") == 1
    assert passwords.labels == expected_passwords and keys.labels == expected_keys
    assert passwords.get(setup.account_id, ServerRole.INCOMING) == "secret"


def test_a_wallet_that_cannot_be_asked_is_asked_once_at_start(setup: Setup) -> None:
    backend = RefusingLabels()
    engine = Engine(setup.engine.directories, KeyringPasswordStore("chrono-mail-test", backend), KeyringKeyStore("chrono-mail-test", backend), durable=False)
    assert label_wallet(engine) == 0
    assert backend.asked == 1  # the first refusal ends it: a locked wallet never asks to be unlocked once per entry


def test_the_start_labels_every_account_of_a_fresh_engine(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    assert label_wallet(engine) == 0  # no accounts, no key: nothing to label


def test_a_profile_names_itself_and_an_account_of_calendars_only_its_user_name(tmp_path: Path) -> None:
    engine = Engine(profile_directories(tmp_path / "chrono-mail-demo"), InMemoryPasswordStore(), InMemoryKeyStore(), durable=False)
    server = ServerSettings("dav.example.com", 443, ConnectionSecurity.TLS, "anna")
    calendars = AccountSettings(Account("b" * 32, "anna", "", AccountKind.DAV, server, server))
    assert password_label(engine, calendars, ServerRole.INCOMING) == "Chrono Mail (chrono-mail-demo): anna (calendar and contacts server)"
    named = replace(calendars, account_name="Family")
    assert password_label(engine, named, ServerRole.INCOMING) == "Chrono Mail (chrono-mail-demo): Family, anna (calendar and contacts server)"
    assert key_label(engine) == "Chrono Mail (chrono-mail-demo): the key that encrypts your mail"
    own = Engine(AppDirectories(tmp_path / "config", tmp_path / "data"), InMemoryPasswordStore(), InMemoryKeyStore(), durable=False)
    assert key_label(own) == "Chrono Mail: the key that encrypts your mail"


class OneChained(ChainerBackend):
    """Several stores chained, as keyring does when more than one is usable: the first takes every new entry."""

    def __init__(self, first: LabelledBackend) -> None:
        self.first: LabelledBackend = first

    @property
    def backends(self) -> list[LabelledBackend]:  # type: ignore[override]  # keyring's is a class property over the real stores
        return [self.first]


def test_chained_stores_label_the_entries_of_the_first() -> None:
    first = LabelledBackend()
    entries = CredentialEntries("chrono-mail-test", first)
    entries.write("password-one", "secret")
    CredentialEntries("chrono-mail-test", OneChained(first)).label("password-one", "a label")
    assert first.labels == {("chrono-mail-test", "password-one"): "a label"}


def test_the_start_labels_the_older_key_of_local_folders_too(setup: Setup) -> None:
    keys = _keys(setup.engine)
    older = generate_data_key()
    keys.save(older)
    label_wallet(setup.engine, (older.key_id,))
    assert keys.labels[older.key_id] == "Chrono Mail: the key that encrypts your mail"


def test_a_rename_during_the_start_pass_is_labelled_with_the_new_name(setup: Setup) -> None:
    labels = _passwords(setup.engine).labels
    labels.clear()
    with LABEL_LOCK:  # the start pass is labelling with the settings it read: the rename's own labelling waits for it
        worker = threading.Thread(target=call, args=(setup.engine, "account.edit"), kwargs={"account": setup.account_id, "account_name": "Work"})
        worker.start()
        worker.join(0.5)
        assert worker.is_alive() and not labels  # the new name is saved, its label waits
        assert setup.engine.config().find(setup.account_id).account_name == "Work"
    worker.join(5)
    assert labels[(setup.account_id, ServerRole.INCOMING)] == "Chrono Mail: Work, me@example.com (incoming mail server)"


class VanishingChain(ChainerBackend):
    """Chained stores whose desktop service went away after the start: asking which stores there are fails."""

    def __init__(self) -> None:
        pass

    @property
    def backends(self) -> list[LabelledBackend]:  # type: ignore[override]  # keyring's is a class property over the real stores
        raise RuntimeError("The Secret Service daemon is neither running nor activatable through D-Bus")


def test_a_store_that_vanished_never_fails_a_label() -> None:
    passwords = KeyringPasswordStore("chrono-mail-test", VanishingChain())
    assert passwords.label("a" * 32, ServerRole.INCOMING, "a label") is False
