import os
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from chrono_mail.config import AppDirectories, InMemoryPasswordStore, ServerRole, default_directories, load_rules, profile_directories
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, ConfigurationError
from chrono_mail.crypto import DataKey, InMemoryKeyStore, KeyNotFoundError, KeyringKeyStore
from chrono_mail.config import KeyringPasswordStore
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_folders import AccountParameters, folder_list
from chrono_mail.sync.imap_state import ImapState
from chrono_mail.ui.app import WINDOW_SETTINGS_FILE, credential_stores, directories_and_settings, take_profile
from chrono_mail.ui.prefs import read_preferences
from tests.doubles import BrokenBackend, MemoryBackend
from tools.demo import build
from tools.demo.build import BuiltDemo, build_demo, remove_demo
from tools.demo.cast import ACCOUNTS

# --- Version ------------------------------------------------------------
__version__ = "test_demo_profile.py 3.6.370"

# --- Version history ----------------------------------------------------
# v3.6.370: a profile keeps its wallet entries under a name of its own (the window's and the builder's stores, one name per folder); --fresh
#           also removes the demo's entries left under the shared name, never the person's own; a demo built before is built again
# v2.3.321: Built without mail (the mail has its own tests)
# v2.3.320: Initial: the demo profile's accounts, folders, rules and settings, and its removal (D-243)


@pytest.fixture
def stores() -> tuple[InMemoryPasswordStore, InMemoryKeyStore]:
    return InMemoryPasswordStore(), InMemoryKeyStore()


@pytest.fixture
def built(tmp_path: Path, stores: tuple[InMemoryPasswordStore, InMemoryKeyStore]) -> tuple[BuiltDemo, Engine]:
    passwords, keys = stores
    demo = build_demo(tmp_path / "demo", passwords, keys, messages=0)  # the accounts only: the mail is tested in test_demo_mail.py
    return demo, Engine(profile_directories(demo.folder), passwords, keys)


def test_the_demo_has_its_fourteen_accounts_and_local_folders(built: tuple[BuiltDemo, Engine]) -> None:
    _, engine = built
    accounts = engine.config().accounts
    kinds = [settings.account.kind for settings in accounts]
    assert kinds.count(AccountKind.IMAP) == 4 and kinds.count(AccountKind.POP3) == 10 and kinds.count(AccountKind.LOCAL) == 1
    pop3 = [settings for settings in accounts if settings.account.kind is AccountKind.POP3]
    assert sorted(settings.storage for settings in pop3) == ["local"] * 8 + ["own"] * 2
    assert all(settings.account.email_address.rpartition("@")[2].split(".")[-2] == "example" for settings in accounts if settings.account.email_address)


def test_every_account_has_its_servers_and_a_stored_password(built: tuple[BuiltDemo, Engine]) -> None:
    demo, engine = built
    for account in ACCOUNTS:
        settings = engine.resolve_account(demo.account_ids[account.key])
        assert settings.account.incoming.host == account.incoming_host and settings.account.outgoing.host == account.outgoing_host
        assert engine.passwords.get(demo.account_ids[account.key], ServerRole.INCOMING)


def test_local_folders_has_one_folder_per_account_and_a_rule_moving_its_mail_there(built: tuple[BuiltDemo, Engine]) -> None:
    demo, engine = built
    folders = {folder.path for folder in folder_list(engine, AccountParameters(LOCAL_ACCOUNT_ID))}
    local = [account for account in ACCOUNTS if account.storage == "local"]
    assert {account.local_folder for account in local} <= folders
    rules = load_rules(engine.directories.rules_file).rules
    assert [action.type for action in rules[0].actions] == ["mark_junk", "stop"]  # spam first
    moves = {rule.accounts: rule.actions[0].value for rule in rules if rule.actions[0].type == "move_to"}
    assert moves == {(demo.account_ids[account.key],): account.local_folder for account in local}


def test_the_imap_folders_are_known_on_the_server_so_nothing_waits(built: tuple[BuiltDemo, Engine]) -> None:
    demo, engine = built
    for account in ACCOUNTS:
        if account.kind != "imap":
            continue
        account_id = demo.account_ids[account.key]
        state = ImapState(engine.directories.imap_state_file(account_id))
        paths = {tuple(folder.path.split("/")) for folder in folder_list(engine, AccountParameters(account_id))}
        server_folders = {path for path in paths if path[0] not in ("Outbox", "Scheduled", "Recovered")}
        assert set(state.server_names) == server_folders and set(state.validity) == server_folders
        assert state.server_names[("Inbox",)] == "INBOX"
        assert {tuple(path.split("/")) for path in account.folders} <= server_folders


def test_the_demo_never_refreshes_by_itself(built: tuple[BuiltDemo, Engine]) -> None:
    _, engine = built
    preferences = read_preferences(QSettings(str(engine.directories.config_directory / WINDOW_SETTINGS_FILE), QSettings.Format.IniFormat))
    assert not preferences.check_on_start and not preferences.auto_check


def test_a_full_folder_is_never_built_over(tmp_path: Path, stores: tuple[InMemoryPasswordStore, InMemoryKeyStore]) -> None:
    (tmp_path / "something.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(SystemExit):
        build_demo(tmp_path, *stores)
    assert (tmp_path / "something.txt").read_text(encoding="utf-8") == "mine"


def test_removing_the_demo_takes_its_key_and_passwords_from_the_wallet(built: tuple[BuiltDemo, Engine], stores: tuple[InMemoryPasswordStore, InMemoryKeyStore]) -> None:
    demo, engine = built
    key_id = engine.config().key_id
    assert key_id is not None
    remove_demo(demo.folder, *stores)
    assert not demo.folder.exists()
    assert all(stores[0].get(account_id, role) is None for account_id in demo.account_ids.values() for role in ServerRole)
    with pytest.raises(KeyNotFoundError):
        stores[1].load(bytes.fromhex(key_id))


def test_a_folder_that_is_no_demo_is_never_removed(tmp_path: Path, stores: tuple[InMemoryPasswordStore, InMemoryKeyStore]) -> None:
    (tmp_path / "mail.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(SystemExit):
        remove_demo(tmp_path, *stores)
    assert (tmp_path / "mail.txt").exists()


def test_a_profile_keeps_its_wallet_entries_under_a_name_of_its_own(tmp_path: Path) -> None:
    """The demo's made-up passwords and key were kept under the same wallet name as the person's own, indistinguishable from them."""
    own = default_directories().credential_service
    first, second = profile_directories(tmp_path / "first").credential_service, profile_directories(tmp_path / "second").credential_service
    assert own == "chrono-mail" and len({own, first, second}) == 3 and str(tmp_path / "first") in first
    assert directories_and_settings(tmp_path / "first")[0].credential_service == first


def test_building_the_demo_again_also_removes_its_entries_left_under_the_shared_name(built: tuple[BuiltDemo, Engine], stores: tuple[InMemoryPasswordStore, InMemoryKeyStore]) -> None:
    """Before 3.6.370 every profile used the shared wallet name: --fresh removes the demo's own entries there, and only those."""
    demo, engine = built
    key_id = engine.config().key_id
    assert key_id is not None
    shared_passwords, shared_keys = InMemoryPasswordStore(), InMemoryKeyStore()
    for account_id in demo.account_ids.values():
        shared_passwords.set(account_id, ServerRole.INCOMING, "made up")
    shared_passwords.set("f" * 32, ServerRole.INCOMING, "the person's own")
    shared_keys.save(stores[1].load(bytes.fromhex(key_id)))
    remove_demo(demo.folder, *stores, shared=(shared_passwords, shared_keys))
    assert all(shared_passwords.get(account_id, role) is None for account_id in demo.account_ids.values() for role in ServerRole)
    assert shared_passwords.get("f" * 32, ServerRole.INCOMING) == "the person's own"
    with pytest.raises(KeyNotFoundError):
        shared_keys.load(bytes.fromhex(key_id))


def test_the_window_and_the_builder_keep_a_profile_under_its_wallet_name_and_one_folder_has_one_name(tmp_path: Path) -> None:
    backend = MemoryBackend()
    directories = profile_directories(tmp_path / "demo")
    passwords, keys = credential_stores(directories, backend)
    passwords.set("a" * 32, ServerRole.INCOMING, "made up")
    keys.save(DataKey(os.urandom(32)))
    assert {service for service, _ in backend.entries} == {directories.credential_service}
    assert take_profile(["chrono-mail", f"--profile={tmp_path}/x/../demo"]) == take_profile(["chrono-mail", "--profile", str(tmp_path / "demo")]) == (tmp_path / "demo").resolve()


def test_the_clean_up_never_removes_what_the_persons_own_settings_name(built: tuple[BuiltDemo, Engine], stores: tuple[InMemoryPasswordStore, InMemoryKeyStore]) -> None:
    """A backup of the person's own restored into the demo would make the demo's settings name their accounts and key."""
    demo, engine = built
    key_id = engine.config().key_id
    assert key_id is not None
    theirs = next(iter(demo.account_ids.values()))
    shared_passwords, shared_keys = InMemoryPasswordStore(), InMemoryKeyStore()
    shared_passwords.set(theirs, ServerRole.INCOMING, "the person's own")
    shared_keys.save(stores[1].load(bytes.fromhex(key_id)))
    remove_demo(demo.folder, *stores, shared=(shared_passwords, shared_keys), keep=frozenset([theirs, key_id]))
    assert shared_passwords.get(theirs, ServerRole.INCOMING) == "the person's own"
    assert shared_keys.load(bytes.fromhex(key_id)).key_id.hex() == key_id


def test_a_demo_built_before_it_had_its_own_wallet_name_is_built_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The window looks for the demo's key under the demo's own name: an older demo, its key under the shared name, could not open."""
    backend = MemoryBackend()

    def stores_in_memory(directories: AppDirectories) -> tuple[KeyringPasswordStore, KeyringKeyStore]:
        return credential_stores(directories, backend)

    monkeypatch.setattr(build, "credential_stores", stores_in_memory)
    monkeypatch.setattr(build, "default_directories", lambda: AppDirectories(tmp_path / "own" / "config", tmp_path / "own" / "data"))
    folder = (tmp_path / "demo").resolve()
    old = build_demo(folder, KeyringPasswordStore(backend=backend), KeyringKeyStore(backend=backend), messages=0)
    assert old.account_ids and all(service == "chrono-mail" for service, _ in backend.entries)
    assert build.main(["--folder", str(folder), "--messages", "0"]) == 0
    directories = profile_directories(folder)
    assert {service for service, _ in backend.entries} == {directories.credential_service}
    key_id = Engine(directories, *credential_stores(directories, backend)).config().key_id
    assert key_id is not None and credential_stores(directories, backend)[1].load(bytes.fromhex(key_id)).key_id.hex() == key_id


def test_a_wallet_that_cannot_be_read_never_makes_the_demo_build_again(built: tuple[BuiltDemo, Engine]) -> None:
    """A locked wallet stopped ./demo.sh with an error before the window opened; it now decides nothing and the window says what is wrong."""
    demo, _ = built
    assert not build.built_before_own_name(demo.folder, KeyringKeyStore(backend=BrokenBackend()))


def test_the_persons_own_settings_are_only_read(tmp_path: Path) -> None:
    """Damaged settings of the person's own stop the clean-up, and no copy is ever written in their folder."""
    own = AppDirectories(tmp_path / "config", tmp_path / "data")
    assert build.own_entries(own) == frozenset()
    own.config_directory.mkdir()
    own.config_file.write_text("{damaged", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        build.own_entries(own)
    assert sorted(path.name for path in own.config_directory.iterdir()) == ["config.json"]
