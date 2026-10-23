from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from chrono_mail.config import InMemoryPasswordStore, ServerRole, load_rules, profile_directories
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind
from chrono_mail.crypto import InMemoryKeyStore, KeyNotFoundError
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_folders import AccountParameters, folder_list
from chrono_mail.sync.imap_state import ImapState
from chrono_mail.ui.app import WINDOW_SETTINGS_FILE
from chrono_mail.ui.prefs import read_preferences
from tools.demo.build import BuiltDemo, build_demo, remove_demo
from tools.demo.cast import ACCOUNTS

# --- Version ------------------------------------------------------------
__version__ = "test_demo_profile.py 2.3.320"

# --- Version history ----------------------------------------------------
# v2.3.320: Initial: the demo profile's accounts, folders, rules and settings, and its removal (D-243)


@pytest.fixture
def stores() -> tuple[InMemoryPasswordStore, InMemoryKeyStore]:
    return InMemoryPasswordStore(), InMemoryKeyStore()


@pytest.fixture
def built(tmp_path: Path, stores: tuple[InMemoryPasswordStore, InMemoryKeyStore]) -> tuple[BuiltDemo, Engine]:
    passwords, keys = stores
    demo = build_demo(tmp_path / "demo", passwords, keys)
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
