"""A profile's wallet name holds no "/" (KDE's wallet garbles an entry of such a name when its label changes), and the entries of a profile
made before move to it: written, read back, only then removed; a garbled one stays where it is (D-274). All made-up data."""

from pathlib import Path
from typing import override

import pytest

from chrono_mail.config import AppDirectories, KeyringPasswordStore, ServerRole, profile_directories
from chrono_mail.core.credentials import CredentialEntries, CredentialFailure, move_entry
from chrono_mail.crypto import KeyringKeyStore
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_accounts import AccountAddParameters, account_add
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.walletlabels import MOVED_FILE, move_profile_entries
from chrono_mail.ui.app import credential_stores
from tests.doubles import MemoryBackend

# --- Version ------------------------------------------------------------
__version__ = "test_profile_wallet_name.py 4.2.459"

# --- Version history ----------------------------------------------------
# v4.2.459: Initial: a profile's wallet name without "/", one per place; its entries of before moved, a garbled one left (D-274)


def test_a_profiles_wallet_name_has_no_slash_and_two_places_of_one_folder_name_differ(tmp_path: Path) -> None:
    first, second = profile_directories(tmp_path / "a" / "demo"), profile_directories(tmp_path / "b" / "demo")
    assert "/" not in first.credential_service and "\\" not in first.credential_service and "demo" in first.credential_service
    assert first.credential_service != second.credential_service
    assert first.credential_service == profile_directories(tmp_path / "a" / "demo").credential_service  # one place, one name, every start
    assert first.former_credential_service == f"chrono-mail ({tmp_path / 'a' / 'demo'})"


def made_before(tmp_path: Path, backend: MemoryBackend) -> tuple[Engine, str, str]:
    """A profile whose key and account password are kept under its former wallet name, as before 4.2.459. Returns the engine, the
    account's id and the key's id."""
    directories = profile_directories(tmp_path / "demo")
    engine = Engine(directories, *credential_stores(directories, backend), durable=False)
    ensure_local_folders(engine)
    account = account_add(engine, AccountAddParameters(
        display_name="Anna", email_address="anna@example.com", username="anna", password="made up", incoming_host="pop.example.com", outgoing_host="smtp.example.com",
    )).account_id
    for (service, name), value in list(backend.entries.items()):
        del backend.entries[(service, name)]
        backend.entries[(directories.former_credential_service, name)] = value
    key_id = engine.config().key_id
    assert key_id is not None
    return engine, account, key_id


def test_a_profile_made_before_has_its_entries_moved_to_its_new_name(tmp_path: Path) -> None:
    backend = MemoryBackend()
    engine, account, key_id = made_before(tmp_path, backend)
    moved, left = move_profile_entries(engine)
    directories = engine.directories
    assert left == () and moved == 3  # the key, the incoming and the outgoing password
    assert {service for service, _ in backend.entries} == {directories.credential_service}
    passwords, keys = credential_stores(directories, backend)
    assert passwords.get(account, ServerRole.INCOMING) == "made up" and keys.load(bytes.fromhex(key_id)).key_id.hex() == key_id
    assert (directories.config_directory / MOVED_FILE).exists()  # done: later starts never ask the wallet for it again
    backend.entries[(directories.former_credential_service, "data-key-" + key_id)] = "stale"
    assert move_profile_entries(engine) == (0, ())


def test_a_move_stopped_half_way_is_finished_and_a_garbled_entry_stays_where_it_is(tmp_path: Path) -> None:
    backend = MemoryBackend()
    engine, account, _ = made_before(tmp_path, backend)
    directories = engine.directories
    incoming = KeyringPasswordStore.entry_name(account, ServerRole.INCOMING)
    backend.entries[(directories.credential_service, incoming)] = "made up"  # written to the new name, the old one not yet removed
    outgoing = KeyringPasswordStore.entry_name(account, ServerRole.OUTGOING)
    backend.entries[(directories.former_credential_service, outgoing)] = "\x00\x00\x00 garbled"
    moved, left = move_profile_entries(engine)
    assert moved == 2 and left == ("anna@example.com (outgoing mail server)",)
    assert (directories.former_credential_service, incoming) not in backend.entries
    assert backend.entries[(directories.former_credential_service, outgoing)] == "\x00\x00\x00 garbled"  # never moved, never removed


def test_an_entry_is_removed_only_after_it_is_read_back_from_its_new_place(tmp_path: Path) -> None:
    class Forgetting(MemoryBackend):
        @override
        def set_password(self, service: str, username: str, password: str) -> None:
            pass  # says yes, keeps nothing

    backend = Forgetting()
    backend.entries[("old", "data-key-00")] = "secret"
    source, target = CredentialEntries("old", backend), CredentialEntries("new", backend)
    with pytest.raises(CredentialFailure, match="did not give back"):
        move_entry(source, target, "data-key-00")
    assert backend.entries[("old", "data-key-00")] == "secret"


def test_the_person_s_own_entries_are_never_moved(tmp_path: Path) -> None:
    backend = MemoryBackend()
    engine = Engine(AppDirectories(tmp_path / "config", tmp_path / "data"), KeyringPasswordStore(backend=backend), KeyringKeyStore(backend=backend))
    assert move_profile_entries(engine) == (0, ())


def test_a_garbled_old_copy_goes_once_the_new_place_holds_the_entry(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.entries[("old", "password-x-incoming")] = "\x00\x00\x00 garbled"
    source, target = CredentialEntries("old", backend), CredentialEntries("new", backend)
    assert move_entry(source, target, "password-x-incoming") == "garbled"  # nothing better anywhere: left as it is
    backend.entries[("new", "password-x-incoming")] = "typed again"
    assert move_entry(source, target, "password-x-incoming") == "moved"
    assert backend.entries == {("new", "password-x-incoming"): "typed again"}  # never said again at the next start


def test_before_the_move_a_profiles_key_and_passwords_are_found_under_the_former_name(tmp_path: Path) -> None:
    backend = MemoryBackend()
    engine, account, key_id = made_before(tmp_path, backend)
    passwords, keys = credential_stores(engine.directories, backend)
    assert keys.load(bytes.fromhex(key_id)).key_id.hex() == key_id  # a wallet that failed the move never makes the key look missing
    assert passwords.get(account, ServerRole.INCOMING) == "made up"


def test_a_good_old_copy_is_kept_when_the_new_place_holds_another_value(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.entries[("old", "password-x-incoming")] = "the first"
    backend.entries[("new", "password-x-incoming")] = "read back wrong"
    assert move_entry(CredentialEntries("old", backend), CredentialEntries("new", backend), "password-x-incoming") == "kept"
    assert backend.entries[("old", "password-x-incoming")] == "the first"  # never the only good copy removed


def test_a_bad_new_copy_never_hides_the_good_old_one(tmp_path: Path) -> None:
    class Twisting(MemoryBackend):
        @override
        def set_password(self, service: str, username: str, password: str) -> None:
            super().set_password(service, username, password + "?" if service == "new" else password)  # reads back other than written

    backend = Twisting()
    backend.entries[("old", "data-key-00")] = "secret"
    with pytest.raises(CredentialFailure):
        move_entry(CredentialEntries("old", backend), CredentialEntries("new", backend), "data-key-00")
    assert backend.entries == {("old", "data-key-00"): "secret"}  # the wrong copy taken away again
    backend.entries[("new", "data-key-00")] = "\x00\x00\x00 garbled"
    fixed = MemoryBackend()
    fixed.entries = backend.entries
    assert move_entry(CredentialEntries("old", fixed), CredentialEntries("new", fixed), "data-key-00") == "moved"
    assert fixed.entries == {("new", "data-key-00"): "secret"}  # a garbled new copy replaced by the good one


def test_garbled_in_both_places_is_said_and_a_garbled_new_copy_never_hides_a_good_old_one(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.entries[("old", "password-x-incoming")] = backend.entries[("new", "password-x-incoming")] = "\x00\x00\x00 garbled"
    assert move_entry(CredentialEntries("old", backend), CredentialEntries("new", backend), "password-x-incoming") == "garbled"
    backend.entries[("old", "password-x-incoming")] = "good"
    assert CredentialEntries("new", backend, former="old").read("password-x-incoming") == "good"


def test_a_target_with_a_former_name_never_takes_the_source_for_its_own_copy(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.entries[("old", "data-key-00")] = "secret"
    assert move_entry(CredentialEntries("old", backend), CredentialEntries("new", backend, former="old"), "data-key-00") == "moved"
    assert backend.entries == {("new", "data-key-00"): "secret"}
