import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from chrono_mail.config import Action, Condition, Rule, RuleSet, ServerRole, load_rules, save_rules
from chrono_mail.core import LOCAL_ACCOUNT_ID, CollectionKind, MessageFlag
from chrono_mail.engine import Engine, build_registry
from chrono_mail.engine.backup import cancel_backup
from chrono_mail.engine.commands_backup import (
    BACKUP_CHECK_ACCOUNT,
    BACKUP_CONTENTS,
    BACKUP_MAKE,
    BACKUP_NEW_PASSWORD,
    BACKUP_RESTORE,
    KEY_ENSURE,
    CheckAccountParameters,
    ContentsResult,
    ReadParameters,
    RestoreParameters,
    RestoreResult,
)
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.restore import BackupPasswordNeeded, NotABackup, RestoreCancelled, WrongBackupPassword
from chrono_mail.sync.imap_state import ImapState
from tests.enginefactory import make_engine
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_engine_restore.py 2.4.324"

# --- Version history ----------------------------------------------------
# v2.4.324: Initial: Restore (D-244): a backup put back on an empty computer looks the same; the backup password; not a backup; an
#           account that is here gets only what it lacks, never twice and never what its server holds; Remove all data first and a
#           cancel put everything back; the connection test of a restored account

REGISTRY = build_registry()
EVENT: str = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Example//EN\r\nBEGIN:VEVENT\r\nUID:{uid}@example.com\r\nDTSTAMP:20261020T100000Z\r\nDTSTART:20261024T100000Z\r\nDTEND:20261024T110000Z\r\nSUMMARY:{summary}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
RULES: RuleSet = RuleSet((Rule("0123456789abcdef", "From the club", (Condition("from", "contains", "club@example.org"),), (Action("mark_read"),)),))


def _computer(root: Path, messages: int = 3) -> Engine:
    """A computer with Local Folders holding read and flagged messages (one tagged), an appointment on this computer, a rule and a
    POP3 account with its password."""
    engine = make_engine(root)
    ensure_local_pim(engine)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        for number in range(messages):
            raw = raw_message(subject=f"Message {number}", body=f"body {number}\r\n", message_id=f"<r{number}@example.com>")
            reference = mailbox.deliver(mailbox.folder("Inbox"), raw, frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}))
            if number == 0:
                mailbox.set_tags(reference, ("Family",))
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        store.put(store.collections(CollectionKind.EVENTS)[0].collection_id, EVENT.format(uid="dentist", summary="Dentist"))
    save_rules(engine.directories.rules_file, RULES)
    REGISTRY.run(engine, "account.add", {
        "display_name": "Anna", "email_address": "anna@example.com", "username": "anna", "password": "letmein", "incoming_host": "127.0.0.1",
        "incoming_port": 1, "incoming_security": "none", "outgoing_host": "127.0.0.1", "outgoing_port": 1, "outgoing_security": "none",
    })
    return engine


def _backup(engine: Engine, target: Path, protected: bool = True) -> str:
    password = BACKUP_NEW_PASSWORD.answer(REGISTRY.run(engine, BACKUP_NEW_PASSWORD.id, {})).password if protected else ""
    rows = REGISTRY.run(engine, "backup.choices", {})
    accounts = tuple(row.entry_id for row in getattr(rows, "rows"))  # getattr: the choices' rows, by name
    values: dict[str, object] = {"path": str(target), "accounts": accounts, "passwords": protected}
    if protected:
        values["password"] = password
    BACKUP_MAKE.answer(REGISTRY.run(engine, BACKUP_MAKE.id, values))
    return password


def _restore(engine: Engine, target: Path, password: str, **choices: bool) -> RestoreResult:
    return REGISTRY.ask(engine, BACKUP_RESTORE, RestoreParameters(str(target), password or None, **choices))


def _mail(engine: Engine, account_id: str = LOCAL_ACCOUNT_ID) -> dict[str, tuple[str, str, tuple[str, ...]]]:
    """Every message by its id: its folder, a digest of its text, its marks; and its tags."""
    found: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    with open_mailbox(engine, account_id) as mailbox:
        for folder in mailbox.store.list_folders():
            for message in mailbox.store.list_messages(folder.path):
                reference = message.ref
                marks = tuple(sorted(flag.name for flag in mailbox.store.flags(reference)))
                found[reference.store_id] = ("/".join(folder.path), hashlib.sha256(mailbox.store.read(reference)).hexdigest(), marks + mailbox.tags_of(reference.store_id))
    return found


def _items(engine: Engine) -> list[str]:
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        return sorted(store.get(info.collection_id, item) for info in store.collections() for item in store.item_ids(info.collection_id))


def _tree(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob("*")) if path.is_file()}


def test_a_backup_put_back_on_an_empty_computer_looks_the_same(tmp_path: Path) -> None:
    first = _computer(tmp_path / "first")
    password = _backup(first, tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")  # a fresh installation: no key, no accounts
    assert KEY_ENSURE.answer(REGISTRY.run(second, KEY_ENSURE.id, {})).made  # this computer's own key first
    result = _restore(second, tmp_path / "all.cmz", password)
    assert set(result.made) == {settings.account.account_id for settings in first.config().accounts} and result.messages == 3 and result.items == 1
    assert [settings.account.account_id for settings in second.config().accounts] == [settings.account.account_id for settings in first.config().accounts]
    assert second.config().key_id != first.config().key_id  # its own key: the backup's password and this key are separate
    assert _mail(second) == _mail(first) and _items(second) == _items(first)
    assert load_rules(second.directories.rules_file) == load_rules(first.directories.rules_file)
    anna = next(settings.account.account_id for settings in second.config().accounts if settings.account.email_address == "anna@example.com")
    assert second.passwords.get(anna, ServerRole.INCOMING) == "letmein"


def test_the_backup_password_is_asked_and_checked(tmp_path: Path) -> None:
    first = _computer(tmp_path / "first")
    password = _backup(first, tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")
    with pytest.raises(BackupPasswordNeeded):
        REGISTRY.ask(second, BACKUP_CONTENTS, ReadParameters(str(tmp_path / "all.cmz")))
    with pytest.raises(WrongBackupPassword, match="does not open"):
        REGISTRY.ask(second, BACKUP_CONTENTS, ReadParameters(str(tmp_path / "all.cmz"), "A" * 24))
    contents: ContentsResult = REGISTRY.ask(second, BACKUP_CONTENTS, ReadParameters(str(tmp_path / "all.cmz"), password))
    assert contents.protected and contents.passwords and contents.messages == 3 and contents.items == 1
    assert [(row.name, row.here) for row in contents.accounts] == [("Local Folders", False), ("anna@example.com", False)]


def test_a_file_that_is_not_a_backup_is_told_so(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    (tmp_path / "notes.cmz").write_text("hello", "utf-8")
    with zipfile.ZipFile(tmp_path / "other.cmz", "w") as archive:
        archive.writestr("readme.txt", "a zip of something else")
    for name in ("notes.cmz", "other.cmz"):
        with pytest.raises(NotABackup, match="not a backup"):
            REGISTRY.ask(engine, BACKUP_CONTENTS, ReadParameters(str(tmp_path / name)))


def test_a_restore_on_the_same_computer_doubles_nothing(tmp_path: Path) -> None:
    engine = _computer(tmp_path / "first")
    password = _backup(engine, tmp_path / "all.cmz")
    before = _mail(engine), _items(engine), load_rules(engine.directories.rules_file)
    result = _restore(engine, tmp_path / "all.cmz", password)
    assert result.made == () and result.messages == 0 and result.items == 0 and result.skipped == 4
    assert (_mail(engine), _items(engine), load_rules(engine.directories.rules_file)) == before


def test_what_is_missing_here_comes_back_and_replace_puts_back_the_tags(tmp_path: Path) -> None:
    engine = _computer(tmp_path / "first")
    password = _backup(engine, tmp_path / "all.cmz")
    everything = _mail(engine)
    tagged = next(store_id for store_id, kept in everything.items() if "Family" in kept[2])
    gone = next(store_id for store_id in sorted(everything) if store_id != tagged)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.delete(mailbox.reference(("Inbox",), gone), permanent=True)
        mailbox.set_tags(mailbox.reference(("Inbox",), tagged), ())
    result = _restore(engine, tmp_path / "all.cmz", password)
    assert result.messages == 1 and gone in _mail(engine) and "Family" not in _mail(engine)[tagged][2]  # without Replace what is here stays
    _restore(engine, tmp_path / "all.cmz", password, replace_existing=True)
    assert _mail(engine) == everything


def test_remove_all_data_first_leaves_only_the_backup(tmp_path: Path) -> None:
    engine = _computer(tmp_path / "first")
    password = _backup(engine, tmp_path / "all.cmz")
    REGISTRY.run(engine, "account.add", {
        "display_name": "Later", "email_address": "later@example.com", "username": "later", "password": "secret", "incoming_host": "127.0.0.2",
        "incoming_port": 1, "incoming_security": "none", "outgoing_host": "127.0.0.2", "outgoing_port": 1, "outgoing_security": "none",
    })
    later = next(settings.account.account_id for settings in engine.config().accounts if settings.account.email_address == "later@example.com")
    _restore(engine, tmp_path / "all.cmz", password, remove_first=True)
    assert later not in {settings.account.account_id for settings in engine.config().accounts}
    assert engine.passwords.get(later, ServerRole.INCOMING) is None and not engine.directories.account_directory(later).exists()
    assert len(_mail(engine)) == 3 and not list(engine.directories.data_directory.glob("restore-aside-*"))


def test_a_cancelled_restore_after_remove_all_data_first_puts_everything_back(tmp_path: Path) -> None:
    engine = _computer(tmp_path / "first", messages=150)
    password = _backup(engine, tmp_path / "all.cmz")
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:  # this computer now differs from the backup
        mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject="Only here", message_id="<only@example.com>"), frozenset())
    before = _tree(engine.directories.data_directory), _tree(engine.directories.config_directory)

    def cancel(what: str, done: int, total: int | None) -> None:
        cancel_backup()  # at the 100th message

    engine.progress = cancel
    with pytest.raises(RestoreCancelled):
        _restore(engine, tmp_path / "all.cmz", password, remove_first=True)
    assert (_tree(engine.directories.data_directory), _tree(engine.directories.config_directory)) == before


def test_a_cancelled_restore_leaves_no_half_account(tmp_path: Path) -> None:
    first = _computer(tmp_path / "first", messages=150)
    password = _backup(first, tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")

    def cancel(what: str, done: int, total: int | None) -> None:
        cancel_backup()

    second.progress = cancel
    with pytest.raises(RestoreCancelled):
        _restore(second, tmp_path / "all.cmz", password)
    assert second.config().accounts == () and not list((second.directories.data_directory / "accounts").glob(".restoring-*"))


def test_an_imap_account_here_gets_only_the_mail_its_server_never_had(tmp_path: Path) -> None:
    def imap_computer(root: Path) -> tuple[Engine, str]:
        engine = make_engine(root)
        added = REGISTRY.run(engine, "account.add", {
            "display_name": "Anna", "email_address": "anna@example.com", "username": "anna", "password": "letmein", "incoming_host": "imap.example.com",
            "incoming_port": 993, "incoming_security": "tls", "outgoing_host": "smtp.example.com", "outgoing_port": 587, "outgoing_security": "starttls",
            "kind": "imap",
        })
        return engine, str(getattr(added, "account_id"))  # getattr: the answer of account.add, by name

    first, account = imap_computer(tmp_path / "first")
    with open_mailbox(first, account) as mailbox:
        on_server = mailbox.restore_here(("Inbox",), raw_message(subject="On the server", message_id="<s@example.com>"), frozenset(), "a" * 32)
        mailbox.restore_here(("Drafts",), raw_message(subject="Written offline", message_id="<d@example.com>"), frozenset(), "b" * 32)
    state = ImapState(first.directories.imap_state_file(account), durable=False)
    state.map(("Inbox",), 7, on_server.store_id, frozenset())
    password = _backup(first, tmp_path / "all.cmz")
    second, _ = imap_computer(tmp_path / "second")  # the same mailbox, added by hand on the other computer
    result = _restore(second, tmp_path / "all.cmz", password)
    assert result.made == () and result.messages == 1
    assert [kept[0] for kept in _mail(second, second.config().accounts[0].account.account_id).values()] == ["Drafts"]


def test_a_restored_account_without_password_or_connection_says_why(tmp_path: Path) -> None:
    first = _computer(tmp_path / "first")
    _backup(first, tmp_path / "plain.cmz", protected=False)
    second = make_engine(tmp_path / "second")
    result = _restore(second, tmp_path / "plain.cmz", "")
    anna = next(account_id for account_id in result.made if account_id != LOCAL_ACCOUNT_ID)
    check = REGISTRY.ask(second, BACKUP_CHECK_ACCOUNT, CheckAccountParameters(anna))
    assert not check.connected and "No password" in check.text
    second.passwords.set(anna, ServerRole.INCOMING, "letmein")
    check = REGISTRY.ask(second, BACKUP_CHECK_ACCOUNT, CheckAccountParameters(anna))
    assert not check.connected and check.text.startswith("Incoming:")  # nothing listens on port 1


def test_the_contents_name_the_accounts_already_here(tmp_path: Path) -> None:
    engine = _computer(tmp_path / "first")
    password = _backup(engine, tmp_path / "all.cmz")
    contents = REGISTRY.ask(engine, BACKUP_CONTENTS, ReadParameters(str(tmp_path / "all.cmz"), password))
    assert all(row.here for row in contents.accounts) and json.loads(json.dumps(contents.parts)) == ["messages", "rules", "calendars", "contacts", "tasks", "settings"]
    assert REGISTRY.ask(engine, KEY_ENSURE, NoParameters()).made is False
