import threading
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from chrono_mail.config import AccountSettings, AppDirectories, InMemoryPasswordStore, KeyringPasswordStore, ServerRole, profile_directories
from chrono_mail.core import Account, AccountKind, ConnectionSecurity, CredentialBackend, CredentialEntries, CredentialFailure, ServerSettings
from chrono_mail.crypto import InMemoryKeyStore, KeyringKeyStore, generate_data_key
from chrono_mail.engine import Engine
from chrono_mail.engine.walletlabels import LABEL_LOCK, key_label, label_wallet, password_label
from keyring.backends.chainer import ChainerBackend
from tests.doubles import MemoryBackend
from tests.enginefactory import make_engine
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

# --- Version ------------------------------------------------------------
__version__ = "test_wallet_labels.py 3.6.372"

# --- Version history ----------------------------------------------------
# v3.6.372: Windows: the label as the User name, the move from the bare service name, removal by the own name
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


# --- Windows Credential Manager (keyring's own Windows code, over a stand-in for Windows' credential calls) -------------------------------


class WindowsError_(Exception):  # noqa: N801, N818 - the shape of pywintypes.error
    def __init__(self, winerror: int, funcname: str) -> None:
        super().__init__(winerror, funcname)
        self.winerror: int = winerror
        self.funcname: str = funcname


class StandInVault:
    """Windows' generic credentials: one entry per target name, each with a User name and a password (UTF-16, as Windows keeps it)."""

    CRED_TYPE_GENERIC: int = 1
    CRED_PERSIST_ENTERPRISE: int = 3

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, object]] = {}
        self.fail_reading: str | None = None  # a target whose read gives back nothing (an entry written and lost)
        self.pause_reading: str | None = None  # a target whose next read waits until resumed
        self.paused: threading.Event = threading.Event()
        self.resume: threading.Event = threading.Event()

    def CredRead(self, Type: int, TargetName: str) -> dict[str, object]:  # noqa: N802, N803 - Windows' names
        if TargetName == self.pause_reading:
            self.pause_reading = None
            self.paused.set()
            assert self.resume.wait(5)
        if TargetName not in self.entries or TargetName == self.fail_reading:
            raise WindowsError_(1168, "CredRead")
        return dict(self.entries[TargetName])

    def CredWrite(self, credential: dict[str, object], flags: int) -> None:  # noqa: N802
        blob = credential["CredentialBlob"]
        assert isinstance(blob, str)
        self.entries[str(credential["TargetName"])] = {**credential, "CredentialBlob": blob.encode("utf-16")}

    def CredDelete(self, Type: int, TargetName: str) -> None:  # noqa: N802, N803
        if TargetName not in self.entries:
            raise WindowsError_(1168, "CredDelete")
        del self.entries[TargetName]

    def user_names(self) -> dict[str, object]:
        return {target: entry["UserName"] for target, entry in self.entries.items()}


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> StandInVault:
    import keyring.backends.Windows as windows

    stand_in = StandInVault()
    monkeypatch.setattr(windows, "win32cred", stand_in, raising=False)
    monkeypatch.setattr(windows, "pywintypes", type("pywintypes", (), {"error": WindowsError_}), raising=False)
    return stand_in


def _windows(vault: StandInVault) -> CredentialEntries:
    from keyring.backends.Windows import WinVaultKeyring

    return CredentialEntries("chrono-mail", cast("CredentialBackend", WinVaultKeyring()))  # cast: keyring's own types are loose; the program gets it untyped from keyring.get_keyring()


def test_windows_shows_the_label_as_the_user_name_and_moves_the_newest_entry_to_its_own_name(vault: StandInVault) -> None:
    entries = _windows(vault)
    entries.write("password-a-incoming", "first")
    entries.write("password-a-outgoing", "second")  # keyring keeps its newest entry under the bare service name
    assert vault.user_names() == {"password-a-incoming@chrono-mail": "password-a-incoming", "chrono-mail": "password-a-outgoing"}
    entries.label("password-a-incoming", "Chrono Mail: Work (incoming mail server)")
    entries.label("password-a-outgoing", "Chrono Mail: Work (outgoing mail server)")
    assert vault.user_names() == {
        "password-a-incoming@chrono-mail": "Chrono Mail: Work (incoming mail server)",
        "password-a-outgoing@chrono-mail": "Chrono Mail: Work (outgoing mail server)",
    }
    assert entries.read("password-a-incoming") == "first" and entries.read("password-a-outgoing") == "second"


def test_windows_finds_changes_and_removes_a_labelled_entry(vault: StandInVault) -> None:
    entries = _windows(vault)
    entries.write("password-a-incoming", "first")
    entries.label("password-a-incoming", "Chrono Mail: Work (incoming mail server)")
    entries.write("password-a-incoming", "changed")  # a new password: keyring writes it under the bare name again
    assert entries.read("password-a-incoming") == "changed"
    entries.label("password-a-incoming", "Chrono Mail: Work (incoming mail server)")
    assert vault.user_names() == {"password-a-incoming@chrono-mail": "Chrono Mail: Work (incoming mail server)"}
    assert entries.read("password-a-incoming") == "changed"
    entries.write("password-b-incoming", "other")
    entries.remove("password-a-incoming")
    entries.remove("password-a-incoming")  # removing it twice is no error
    assert entries.read("password-a-incoming") is None
    assert entries.read("password-b-incoming") == "other"  # another entry under the bare name stays


def test_windows_never_removes_the_old_entry_before_the_new_one_reads_back(vault: StandInVault) -> None:
    entries = _windows(vault)
    entries.write("password-a-incoming", "first")
    vault.fail_reading = "password-a-incoming@chrono-mail"
    with pytest.raises(CredentialFailure):
        entries.label("password-a-incoming", "Chrono Mail: Work (incoming mail server)")
    assert vault.entries["chrono-mail"]["UserName"] == "password-a-incoming"  # still where it was
    vault.fail_reading = None
    assert entries.read("password-a-incoming") == "first"
    entries.label("password-a-incoming", "Chrono Mail: Work (incoming mail server)")  # the next start finishes it
    assert vault.user_names() == {"password-a-incoming@chrono-mail": "Chrono Mail: Work (incoming mail server)"}


def test_windows_a_password_saved_during_a_label_is_never_lost(vault: StandInVault) -> None:
    entries = _windows(vault)
    entries.write("password-a-incoming", "first")  # under the bare name: the label moves it
    vault.pause_reading = "password-a-incoming@chrono-mail"  # the label waits between writing the new place and removing the bare one
    labelling = threading.Thread(target=entries.label, args=("password-a-incoming", "Chrono Mail: Work (incoming mail server)"))
    labelling.start()
    saving = threading.Thread(target=entries.write, args=("password-b-incoming", "second"))  # another account is added meanwhile
    try:
        assert vault.paused.wait(5)
        saving.start()
        saving.join(0.3)
        assert saving.is_alive()  # it waits for the label: one call to the store at a time
    finally:
        vault.resume.set()  # never leaves the store held for a later test
    labelling.join(5)
    saving.join(5)
    assert entries.read("password-a-incoming") == "first" and entries.read("password-b-incoming") == "second"


def test_windows_a_very_long_name_is_cut_to_what_windows_keeps(vault: StandInVault) -> None:
    entries = _windows(vault)
    entries.write("password-a-incoming", "first")
    entries.label("password-a-incoming", "Chrono Mail: " + "x" * 600)
    user_name = vault.entries["password-a-incoming@chrono-mail"]["UserName"]
    assert isinstance(user_name, str) and len(user_name) == 512 and user_name.endswith("…")
