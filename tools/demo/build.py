import argparse
import random
import secrets
import shutil
import sys
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings

from chrono_mail.config import AppDirectories, KeyringPasswordStore, Rule, RuleSet, ServerRole, profile_directories, save_rules
from chrono_mail.config import Action, Condition, new_rule_id
from chrono_mail.config.passwords import PasswordStore
from chrono_mail.core import LOCAL_ACCOUNT_ID, FolderRole
from chrono_mail.crypto import KeyringKeyStore, KeyStore
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_accounts import AccountAddParameters, account_add
from chrono_mail.engine.commands_folders import FolderParameters, folder_create
from chrono_mail.sync.imap_state import ImapState
from chrono_mail.ui.app import WINDOW_SETTINGS_FILE
from chrono_mail.ui.prefs import read_preferences, save_preferences
from tools.demo.cast import ACCOUNTS, PERSON, DemoAccount
from tools.demo.mail import TOTAL_MESSAGES, build_mail
from tools.demo.people import make_people
from tools.demo.pim import PimMaker

# --- Version ------------------------------------------------------------
__version__ = "build.py 2.3.321"

# --- Version history ----------------------------------------------------
# v2.3.321: The mail (100,000 messages by default, --messages for fewer), the calendars, tasks and contacts, with progress in the terminal
# v2.3.320: Initial: builds the demo profile of the documentation: its 14 accounts, their folders, Local Folders and the rules (D-243)

# The demo profile: a Chrono Mail of a made-up person, for the screenshots of the documentation, in a folder of its own
# (started with chrono-mail --profile <folder>). It never touches the person's own mail or settings, and it never contacts a
# server: its servers do not exist, and automatic refresh is off.

DEFAULT_FOLDER: Path = Path.home() / "chrono-mail-demo"
MARKER: str = "chrono-mail-demo"  # the file that says a folder is a demo profile, so --fresh never removes anything else

SPAM_RULES: tuple[tuple[str, tuple[Condition, ...], str], ...] = (  # name, conditions, match
    ("Spam marked by the server", (Condition("header", "equals", "YES", "X-Spam-Flag"),), "all"),
    ("Spam in the subject", (Condition("subject", "starts_with", "***SPAM***"), Condition("subject", "starts_with", "[SPAM]")), "any"),
)


@dataclass(frozen=True, slots=True)
class BuiltDemo:
    folder: Path
    account_ids: dict[str, str]  # DemoAccount.key -> account id
    messages: int = 0
    events: int = 0
    tasks: int = 0
    contacts: int = 0


def _quiet(what: str, done: int, total: int) -> None:
    del what, done, total


def show_progress(what: str, done: int, total: int) -> None:
    """One line in the terminal that keeps changing: what is being made and how far it is."""
    print(f"\r{what}: {done:,} of {total:,}   ", end="" if done < total else "\n", flush=True)


def remove_demo(folder: Path, passwords: PasswordStore, keys: KeyStore) -> None:
    """Remove a demo profile with its key and passwords from the wallet, so they never pile up. Refuses a folder that is no demo."""
    if not folder.exists():
        return
    if not (folder / MARKER).is_file():
        raise SystemExit(f"{folder} is not a demo profile (it has no {MARKER} file): nothing was removed.")
    directories = profile_directories(folder)
    if directories.config_file.exists():
        config = Engine(directories, passwords, keys).config()
        for settings in config.accounts:
            for role in ServerRole:
                passwords.delete(settings.account.account_id, role)
        if config.key_id is not None:
            keys.delete(bytes.fromhex(config.key_id))
    shutil.rmtree(folder)


def _server_validity(account: DemoAccount, folder: tuple[str, ...]) -> int:
    """A UIDVALIDITY that stays the same for every build, as a server's would."""
    return zlib.crc32(f"{account.address}/{'/'.join(folder)}".encode()) or 1


def _imap_in_step(engine: Engine, account: DemoAccount, account_id: str) -> None:
    """The folders of an IMAP account made as a synchronisation would have made them from the server's list: here and in the account's
    record of the server, so nothing waits to be made there and nothing is marked as waiting."""
    settings = engine.resolve_account(account_id)
    store = engine.open_store(settings)
    store.ensure_folder(("Archive",), FolderRole.ARCHIVE)
    for path in account.folders:
        store.ensure_folder(tuple(path.split("/")))
    state = ImapState(engine.directories.imap_state_file(account_id), durable=engine.durable)
    for folder in store.list_folders():
        if folder.path[0] in ("Outbox", "Scheduled", "Recovered"):
            continue  # on this computer only (D-051)
        name = "INBOX" if folder.role is FolderRole.INBOX else "/".join(folder.path)
        state.set_server_name(folder.path, name)
        state.set_validity(folder.path, _server_validity(account, folder.path))


def _add_account(engine: Engine, account: DemoAccount) -> str:
    password = secrets.token_urlsafe(12)  # a made-up password, kept in the wallet like a real one, so nothing asks for one
    result = account_add(engine, AccountAddParameters(
        display_name=PERSON, email_address=account.address, username=account.address, password=password,
        incoming_host=account.incoming_host, outgoing_host=account.outgoing_host,
        incoming_port=account.incoming_port, incoming_security=account.incoming_security,
        outgoing_port=account.outgoing_port, outgoing_security=account.outgoing_security,
        kind=account.kind, storage=account.storage, account_name=account.account_name,
    ))
    return result.account_id


def _rules(directories: AppDirectories, account_ids: dict[str, str]) -> None:
    """The rules of a person who keeps most POP3 accounts in Local Folders: the spam ones first, then one per account that moves its mail
    from the shared Inbox to the account's own folder there."""
    rules = [Rule(new_rule_id(), name, conditions, (Action("mark_junk"), Action("stop")), match) for name, conditions, match in SPAM_RULES]
    for account in ACCOUNTS:
        if account.storage == "local":
            rules.append(Rule(new_rule_id(), f"{account.account_name} to its folder", (), (Action("move_to", account.local_folder),),
                              accounts=(account_ids[account.key],)))
    save_rules(directories.rules_file, RuleSet(tuple(rules)))


def _window_settings(directories: AppDirectories) -> None:
    """The demo never tries to reach its servers, which do not exist: no refresh at the start, no automatic refresh."""
    settings = QSettings(str(directories.config_directory / WINDOW_SETTINGS_FILE), QSettings.Format.IniFormat)
    save_preferences(settings, replace(read_preferences(settings), check_on_start=False, auto_check=False))
    settings.sync()


def build_demo(folder: Path, passwords: PasswordStore, keys: KeyStore, messages: int = TOTAL_MESSAGES, progress: Callable[[str, int, int], None] = _quiet,
               today: datetime | None = None) -> BuiltDemo:
    """Build the demo profile in an empty or missing folder: its accounts, its mail over five years up to today, its calendars, tasks and
    contacts. The same demo every time, with its dates counted back from today."""
    if folder.exists() and any(folder.iterdir()):
        raise SystemExit(f"{folder} is not empty: build the demo again with --fresh.")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MARKER).write_text("A Chrono Mail demo profile, made by tools/demo/build.py. Everything in it is made up.\n", encoding="utf-8")
    directories = profile_directories(folder)
    engine = Engine(directories, passwords, keys)
    account_ids: dict[str, str] = {}
    for account in ACCOUNTS:
        account_ids[account.key] = _add_account(engine, account)
        if account.kind == "imap":
            _imap_in_step(engine, account, account_ids[account.key])
        else:
            for path in account.folders:
                folder_create(engine, FolderParameters(account_ids[account.key], path))
    for account in ACCOUNTS:
        if account.storage == "local":
            folder_create(engine, FolderParameters(LOCAL_ACCOUNT_ID, account.local_folder))
    _rules(directories, account_ids)
    _window_settings(directories)
    now = (today or datetime.now().astimezone()).replace(second=0, microsecond=0)
    people = make_people(random.Random(2025))
    filling = Engine(directories, passwords, keys, durable=False)  # a demo is built again rather than repaired: no wait for the disk per message
    stored = build_mail(filling, account_ids, people, now, messages, progress) if messages else 0
    events, tasks, contacts = PimMaker(filling, account_ids, people, now).build(progress)
    return BuiltDemo(folder, account_ids, stored, events, tasks, contacts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Chrono Mail demo profile for the screenshots of the documentation.")
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER, help=f"where the demo profile goes (default {DEFAULT_FOLDER})")
    parser.add_argument("--fresh", action="store_true", help="remove the demo profile first, with its key and passwords in the wallet")
    parser.add_argument("--messages", type=int, default=TOTAL_MESSAGES, help=f"how many messages (default {TOTAL_MESSAGES:,})")
    arguments = parser.parse_args(argv)
    folder: Path = arguments.folder.expanduser().absolute()
    passwords, keys = KeyringPasswordStore(), KeyringKeyStore()
    if arguments.fresh:
        remove_demo(folder, passwords, keys)
    if (folder / MARKER).is_file():
        print(f"The demo profile is ready in {folder}.")
        return 0
    print(f"Building the demo profile in {folder} (about a quarter of an hour for 100,000 messages)...")
    began = time.monotonic()
    built = build_demo(folder, passwords, keys, arguments.messages, show_progress)
    minutes = (time.monotonic() - began) / 60
    print(f"The demo profile is built in {built.folder} in {minutes:.0f} minutes: {len(built.account_ids)} accounts, {built.messages:,} messages, "
          f"{built.events} calendar entries, {built.tasks} tasks, {built.contacts} contacts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
