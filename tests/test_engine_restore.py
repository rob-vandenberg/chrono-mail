import dataclasses
import hashlib
import json
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from chrono_mail.config import AccountSettings, Action, AppConfig, Condition, Rule, RuleSet, ServerRole, load_rules, save_rules
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, ChronoMailError, CollectionKind, MessageFlag
from chrono_mail.engine import Engine, backup, build_registry, restore
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
from chrono_mail.engine.commands_schedule import MAIL_SCHEDULED, AccountOptional
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.restore import BackupPasswordNeeded, NotABackup, RestoreCancelled, WrongBackupPassword
from chrono_mail.engine.session import ServerWorkPaused
from chrono_mail.sync.imap_state import ImapState
from chrono_mail.sync.pop3_state import Pop3State
from tests.enginefactory import make_engine
from tests.test_config import make_account
from chrono_mail.mime import with_extra_headers
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_engine_restore.py 3.3.343"

# --- Version history ----------------------------------------------------
# v3.3.343: the code review's batch 3: what was downloaded names only messages that came back (RV-61); size limits (RV-63); damaged
#           descriptions and settings are left out (RV-64, RV-65); Replace keeps storage and encryption (RV-67); a failed restore leaves
#           nothing it wrote (RV-68); a deleted message searches the mailbox once (RV-69); a Calendars-and-contacts account by its
#           login (RV-73)
# v3.0.339: a name in a backup that leads out of its folder is never written (RV-59); a restore waits for a check that runs and holds new
#           ones back (RV-60)
# v2.4.334: the report says where the damaged message was
# v2.4.328: an account without files comes back (W1); a damaged message is left out and the rest comes back (W5); a cut-off backup is
#           damaged or incomplete (W4); only the rules of the accounts that came back (R2)
# v2.4.327: a restore sends nothing by itself: the Outbox's mail comes back in Drafts, an overdue scheduled message paused (D-245)
# v2.4.326: only the chosen accounts, parts and passwords come back; the contents per account with their counts
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


def _restore(engine: Engine, target: Path, password: str, remove_first: bool = False, replace_existing: bool = False) -> RestoreResult:
    return REGISTRY.ask(engine, BACKUP_RESTORE, RestoreParameters(str(target), password or None, remove_first, replace_existing))


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
    assert [(row.name, row.here) for row in contents.accounts] == [("anna@example.com", False), ("Local Folders", False), ("On this computer", False)]


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


def test_only_the_chosen_accounts_parts_and_passwords_come_back(tmp_path: Path) -> None:
    first = _computer(tmp_path / "first")
    password = _backup(first, tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")
    contents = REGISTRY.ask(second, BACKUP_CONTENTS, ReadParameters(str(tmp_path / "all.cmz"), password))
    assert [(row.entry_id, row.parts, row.messages, row.items) for row in contents.accounts][1:] == [
        ("local-folders", ("messages",), 3, 0), ("this-computer", ("calendars", "contacts", "tasks"), 0, 1),
    ]
    result = REGISTRY.ask(second, BACKUP_RESTORE, RestoreParameters(
        str(tmp_path / "all.cmz"), password, accounts=("local-folders", contents.accounts[0].entry_id), rules=False, settings=False, passwords=False,
    ))
    assert result.messages == 3 and result.items == 0  # this computer's calendars were not ticked
    anna = contents.accounts[0].entry_id
    assert {settings.account.account_id for settings in second.config().accounts} == {LOCAL_ACCOUNT_ID, anna}
    assert second.passwords.get(anna, ServerRole.INCOMING) is None and not second.directories.rules_file.exists()
    assert len(_mail(second)) == 3 and not (second.directories.pim_directory(LOCAL_ACCOUNT_ID) / "pim.json").exists()


def _waiting(engine: Engine) -> tuple[str, str]:
    """A reply in the Outbox and a message scheduled for a time that has passed, in Local Folders; their ids."""
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        outbox = mailbox.deliver(mailbox.folder("Outbox"), raw_message(subject="Old reply", message_id="<old-reply@example.com>"), frozenset({MessageFlag.SEEN}))
        scheduled, _ = mailbox.ensure_folder(("Scheduled",))
        raw = with_extra_headers(raw_message(subject="Old greeting", message_id="<old-greeting@example.com>"), {
            "X-Chrono-Mail-Kind": "scheduled", "X-Chrono-Mail-Send-At": str(int(engine.clock()) - 86_400),
        })
        late = mailbox.deliver(scheduled.path, raw, frozenset({MessageFlag.SEEN}))
    return outbox.store_id, late.store_id


def _sends_nothing(engine: Engine, result: RestoreResult, outbox: str, late: str) -> None:
    assert (result.to_drafts, result.paused) == (1, 1)
    mail = _mail(engine)
    assert mail[outbox][0] == "Drafts" and mail[late][0] == "Scheduled"
    (info,) = REGISTRY.ask(engine, MAIL_SCHEDULED, AccountOptional())
    assert info.message_id == late and info.paused


def test_a_restore_sends_nothing_by_itself(tmp_path: Path) -> None:
    """The Windows computer's W6: a restore sent the old Outbox. What waited to be sent comes back waiting for the person (D-245): the Outbox's
    mail in Drafts, a scheduled message whose time passed paused; on an empty computer (whole) and on one that lacks them (filled)."""
    first = _computer(tmp_path / "first")
    outbox, late = _waiting(first)
    password = _backup(first, tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")
    REGISTRY.run(second, KEY_ENSURE.id, {})
    _sends_nothing(second, _restore(second, tmp_path / "all.cmz", password), outbox, late)
    with open_mailbox(first, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.delete(mailbox.reference(("Outbox",), outbox), permanent=True)
        mailbox.delete(mailbox.reference(("Scheduled",), late), permanent=True)
    _sends_nothing(first, _restore(first, tmp_path / "all.cmz", password), outbox, late)


def _add_local_pop3(engine: Engine) -> str:
    """A POP3 account that keeps its mail in Local Folders and has fetched nothing yet: it has no files of its own."""
    REGISTRY.run(engine, "account.add", {
        "display_name": "Shop", "email_address": "shop@example.com", "username": "shop", "password": "secret", "incoming_host": "127.0.0.3",
        "incoming_port": 1, "incoming_security": "none", "outgoing_host": "127.0.0.3", "outgoing_port": 1, "outgoing_security": "none", "storage": "local",
    })
    return next(settings.account.account_id for settings in engine.config().accounts if settings.account.email_address == "shop@example.com")


def test_an_account_without_any_files_comes_back(tmp_path: Path) -> None:
    """The Windows computer's W1: such an account stopped every restore with FileNotFoundError."""
    first = _computer(tmp_path / "first")
    shop = _add_local_pop3(first)
    password = _backup(first, tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")
    REGISTRY.run(second, KEY_ENSURE.id, {})
    result = _restore(second, tmp_path / "all.cmz", password)
    assert shop in result.made and shop in {settings.account.account_id for settings in second.config().accounts}
    assert _mail(second) == _mail(first) and load_rules(second.directories.rules_file) == load_rules(first.directories.rules_file)
    assert second.passwords.get(shop, ServerRole.INCOMING) == "secret"


def _damage_one_message(backup: Path) -> str:
    """Flip bytes in the middle of one message's data in the backup (its contents page and directory stay intact); its name."""
    return _damage(backup, lambda name: "/cur/" in name or "/new/" in name)


def _damage(backup: Path, chosen: Callable[[str], bool]) -> str:
    """Flip bytes in the middle of the data of the first entry chosen; its name."""
    with zipfile.ZipFile(backup) as archive:
        entry = next(info for info in archive.infolist() if chosen(info.filename))
        local = entry.header_offset
    data = bytearray(backup.read_bytes())
    name_length, extra_length = int.from_bytes(data[local + 26:local + 28], "little"), int.from_bytes(data[local + 28:local + 30], "little")
    middle = local + 30 + name_length + extra_length + max(0, entry.compress_size // 2 - 4)
    for offset in range(middle, middle + min(8, entry.compress_size)):
        data[offset] ^= 0xFF
    backup.write_bytes(bytes(data))
    return entry.filename


def test_a_damaged_message_is_left_out_and_everything_else_comes_back(tmp_path: Path) -> None:
    """The Windows computer's W5 and W4: one damaged message stopped the whole restore, and the report showed its raw bytes."""
    first = _computer(tmp_path / "first")
    _backup(first, tmp_path / "all.cmz", protected=False)
    _damage_one_message(tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")
    REGISTRY.run(second, KEY_ENSURE.id, {})
    result = _restore(second, tmp_path / "all.cmz", "")
    assert result.damaged == 1 and result.messages == 2 and result.items == 1
    assert result.damaged_places == ("1 message in Inbox of Local Folders",)  # where it was: the person can tell what is missing
    assert {settings.account.account_id for settings in second.config().accounts} == {settings.account.account_id for settings in first.config().accounts}
    assert load_rules(second.directories.rules_file) == load_rules(first.directories.rules_file)
    before = _mail(first)
    result = _restore(first, tmp_path / "all.cmz", "", remove_first=True)  # nothing may be lost: what was here is kept aside
    assert result.damaged == 1 and any("is kept in" in note for note in result.notes)
    assert len(list((first.directories.data_directory).glob("restore-aside-*"))) == 1 and len(_mail(first)) == len(before) - 1


def test_a_cut_off_backup_is_damaged_or_incomplete(tmp_path: Path) -> None:
    first = _computer(tmp_path / "first")
    _backup(first, tmp_path / "all.cmz")
    whole = (tmp_path / "all.cmz").read_bytes()
    (tmp_path / "half.cmz").write_bytes(whole[: len(whole) // 2])
    with pytest.raises(NotABackup, match="damaged or incomplete"):
        REGISTRY.ask(make_engine(tmp_path / "second"), BACKUP_CONTENTS, ReadParameters(str(tmp_path / "half.cmz")))


def test_only_the_rules_of_the_accounts_that_come_back_are_restored(tmp_path: Path) -> None:
    first = _computer(tmp_path / "first")
    anna = next(settings.account.account_id for settings in first.config().accounts if settings.account.email_address == "anna@example.com")
    own = Rule("fedcba9876543210", "Anna's invoices", (Condition("subject", "contains", "invoice"),), (Action("mark_read"),), accounts=(anna,))
    save_rules(first.directories.rules_file, RuleSet((*RULES.rules, own)))
    password = _backup(first, tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")
    REGISTRY.run(second, KEY_ENSURE.id, {})
    REGISTRY.ask(second, BACKUP_RESTORE, RestoreParameters(str(tmp_path / "all.cmz"), password, accounts=("local-folders",), settings=False, passwords=False))
    assert load_rules(second.directories.rules_file) == RULES  # the rule of Anna, not ticked, stays out


def test_a_name_in_a_backup_that_leads_out_of_its_folder_is_never_written(tmp_path: Path) -> None:
    """RV-59: a crafted backup could write files anywhere the person may write, for example a program that starts with the desktop."""
    first = _computer(tmp_path / "first")
    _backup(first, tmp_path / "all.cmz", protected=False)
    with zipfile.ZipFile(tmp_path / "all.cmz") as archive:
        message = next(name for name in archive.namelist() if "/cur/" in name)
    prefix = message[: message.index("/store/") + 1]
    with zipfile.ZipFile(tmp_path / "all.cmz", "a") as archive:
        archive.writestr(f"{prefix}store/../../../escaped.txt", b"written outside")
        archive.writestr(f"{prefix}store/../../../../escaped-too.txt", b"written outside")
    second = make_engine(tmp_path / "second")
    REGISTRY.run(second, KEY_ENSURE.id, {})
    result = _restore(second, tmp_path / "all.cmz", "")
    assert not [path for path in tmp_path.rglob("escaped*")]
    assert result.messages == 3 and _mail(second) == _mail(first)  # the rest of the backup came back


def test_a_restore_waits_for_a_check_that_runs_and_holds_new_ones_back(tmp_path: Path) -> None:
    """RV-60: Remove all data first set this computer's mail aside while a POP3 check was still running; the check then deleted the mail
    on the server, and the copy set aside was removed after the restore."""
    engine = _computer(tmp_path / "first")
    password = _backup(engine, tmp_path / "all.cmz")
    anna = next(settings.account.account_id for settings in engine.config().accounts if settings.account.email_address == "anna@example.com")
    checking, release = threading.Event(), threading.Event()

    def a_check_that_runs() -> None:
        with engine.server_work.running():
            checking.set()
            release.wait(10)

    check = threading.Thread(target=a_check_that_runs)
    check.start()
    assert checking.wait(5)
    restored: list[RestoreResult] = []
    restore = threading.Thread(target=lambda: restored.append(_restore(engine, tmp_path / "all.cmz", password, remove_first=True)))
    restore.start()
    time.sleep(0.5)
    try:
        assert not restored and not list(engine.directories.data_directory.glob("restore-aside-*"))  # waiting: nothing set aside yet
        with pytest.raises(ServerWorkPaused):
            REGISTRY.run(engine, "mail.fetch", {"account": anna})  # a new check waits for the restore
    finally:
        release.set()
        check.join(5)
        restore.join(10)
    assert restored and restored[0].messages == 3
    try:
        REGISTRY.run(engine, "mail.fetch", {"account": anna})  # the restore is done: checks run again
    except ServerWorkPaused:
        pytest.fail("a check is still held back after the restore")
    except ChronoMailError:
        pass  # this account has no server to reach


def _anna(engine: Engine) -> str:
    return next(settings.account.account_id for settings in engine.config().accounts if settings.account.email_address == "anna@example.com")


def _downloaded(engine: Engine, account_id: str, count: int, local: bool = False) -> list[str]:
    """Messages the POP3 account downloaded (into its own folders, or into Local Folders), as its download list names them."""
    state = Pop3State(engine.directories.pop3_state_file(account_id), durable=False)
    store_ids: list[str] = []
    with open_mailbox(engine, LOCAL_ACCOUNT_ID if local else account_id) as mailbox:
        for number in range(count):
            raw = raw_message(subject=f"Fetched {number}", body="x\r\n", message_id=f"<f{number}@example.com>")
            reference = mailbox.deliver(mailbox.folder("Inbox"), raw, frozenset())
            state.record_download(f"uid-{number}", reference.store_id, 1_000_000)
            store_ids.append(reference.store_id)
    return store_ids


def _named(engine: Engine, account_id: str) -> set[str]:
    return {record.store_id for record in Pop3State(engine.directories.pop3_state_file(account_id), durable=False).records.values()}


def _fresh(root: Path) -> Engine:
    engine = make_engine(root)
    REGISTRY.run(engine, KEY_ENSURE.id, {})
    return engine


def test_a_damaged_message_is_left_out_of_what_was_downloaded(tmp_path: Path) -> None:
    """RV-61: the download list named the damaged message that did not come back; with "delete from the server when deleted here" the
    next check deleted it on the server."""
    first = _computer(tmp_path / "first")
    anna = _anna(first)
    fetched = _downloaded(first, anna, 2)
    _backup(first, tmp_path / "all.cmz", protected=False)
    damaged = _damage(tmp_path / "all.cmz", lambda name: f"/{anna}/" in name and ("/cur/" in name or "/new/" in name))
    second = _fresh(tmp_path / "second")
    result = _restore(second, tmp_path / "all.cmz", "")
    assert result.damaged == 1 and _named(second, anna) == set(fetched) - {Path(damaged).name[:32]}


def test_mail_kept_in_local_folders_that_does_not_come_back_is_left_out_of_what_was_downloaded(tmp_path: Path) -> None:
    """RV-61: a POP3 account that keeps its mail in Local Folders, backed up without Local Folders: its download list named every message."""
    first = _computer(tmp_path / "first")
    shop = _add_local_pop3(first)
    fetched = _downloaded(first, shop, 2, local=True)
    BACKUP_MAKE.answer(REGISTRY.run(first, BACKUP_MAKE.id, {"path": str(tmp_path / "shop.cmz"), "accounts": (shop,), "passwords": False}))
    BACKUP_MAKE.answer(REGISTRY.run(first, BACKUP_MAKE.id, {"path": str(tmp_path / "both.cmz"), "accounts": (shop, "local-folders"), "passwords": False}))
    alone = _fresh(tmp_path / "alone")
    assert shop in _restore(alone, tmp_path / "shop.cmz", "").made and _named(alone, shop) == set()
    together = _fresh(tmp_path / "together")
    assert shop in _restore(together, tmp_path / "both.cmz", "").made and _named(together, shop) == set(fetched)


def test_what_is_downloaded_while_the_backup_lists_the_mail_is_not_in_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-61: the download list was read when it was written, after the mail was listed: it named messages the backup lacked."""
    first = _computer(tmp_path / "first")
    anna = _anna(first)
    before = _downloaded(first, anna, 1)
    real_walk = backup._walk  # noqa: SLF001
    later: list[str] = []

    def walk_while_mail_arrives(root: Path) -> Iterator[Path]:
        if not later:
            later.append("arrived")
            Pop3State(first.directories.pop3_state_file(anna), durable=False).record_download("uid-later", "f" * 32, 1_000_001)
        return real_walk(root)

    monkeypatch.setattr(backup, "_walk", walk_while_mail_arrives)
    _backup(first, tmp_path / "all.cmz", protected=False)
    with zipfile.ZipFile(tmp_path / "all.cmz") as archive:
        text = archive.read(f"data/accounts/{anna}/pop3-state.log").decode("utf-8")
    assert later and before[0] in text and "uid-later" not in text


def test_a_message_deleted_while_the_backup_runs_searches_the_mailbox_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-69: every deleted message searched the whole mailbox again; with many deleted in a large mailbox the backup seemed to hang."""
    engine = _computer(tmp_path / "first", messages=5)
    root = engine.directories.store_directory(LOCAL_ACCOUNT_ID)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        gone = [message.ref.store_id for message in mailbox.store.list_messages(mailbox.folder("Inbox"))]
        for store_id in gone:
            mailbox.delete(mailbox.reference(mailbox.folder("Inbox"), store_id), permanent=True)
    real_walk = backup._walk  # noqa: SLF001
    walks: list[Path] = []

    def counted(found: Path) -> Iterator[Path]:
        walks.append(found)
        return real_walk(found)

    monkeypatch.setattr(backup, "_walk", counted)
    messages = backup._Messages()  # noqa: SLF001
    assert [backup._find_again(messages, root, store_id) for store_id in gone] == [None] * len(gone)  # noqa: SLF001
    assert len(walks) == 1


def test_a_file_in_a_backup_larger_than_its_limit_is_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-63: a small crafted file can unpack to far more than memory holds; it is left out as damaged, the rest comes back."""
    first = _computer(tmp_path / "first")
    _backup(first, tmp_path / "all.cmz", protected=False)
    monkeypatch.setattr(restore, "MAXIMUM_MESSAGE_BYTES", 10)
    result = _restore(_fresh(tmp_path / "second"), tmp_path / "all.cmz", "")
    assert result.damaged == 3 and result.messages == 0 and result.items == 1


def test_a_damaged_calendar_description_leaves_only_that_calendar_out(tmp_path: Path) -> None:
    """RV-64: one damaged collection.json made the whole backup unopenable."""
    first = _computer(tmp_path / "first")
    _backup(first, tmp_path / "all.cmz", protected=False)
    _damage(tmp_path / "all.cmz", lambda name: name.endswith("/collection.json") and f"/{LOCAL_ACCOUNT_ID}/" in name and _holds_items(tmp_path / "all.cmz", name))
    second = _fresh(tmp_path / "second")
    contents = REGISTRY.ask(second, BACKUP_CONTENTS, ReadParameters(str(tmp_path / "all.cmz")))
    assert isinstance(contents, ContentsResult)
    result = _restore(second, tmp_path / "all.cmz", "")
    assert result.damaged == 1 and result.items == 0 and result.messages == 3 and _mail(second) == _mail(first)


def _holds_items(backup_file: Path, description: str) -> bool:
    with zipfile.ZipFile(backup_file) as archive:
        return any(name.startswith(description.rsplit("/", 1)[0] + "/items/") for name in archive.namelist())


def _inbox_description(backup_file: Path) -> str:
    with zipfile.ZipFile(backup_file) as archive:
        return next(
            name for name in archive.namelist()
            if name.endswith("/folder.json") and f"/{LOCAL_ACCOUNT_ID}/" in name and json.loads(archive.read(name)).get("path") == ["Inbox"]
        )


def test_a_damaged_folder_description_or_setting_leaves_only_that_out(tmp_path: Path) -> None:
    """RV-65: a damaged folder.json, details file or settings file stopped the whole restore."""
    first = _computer(tmp_path / "first")
    _backup(first, tmp_path / "all.cmz", protected=False)
    inbox = _inbox_description(tmp_path / "all.cmz")
    _damage(tmp_path / "all.cmz", lambda name: name == inbox)
    _damage(tmp_path / "all.cmz", lambda name: name == "config/rules.json")
    second = _fresh(tmp_path / "second")
    result = _restore(second, tmp_path / "all.cmz", "")  # back whole: the account was not here
    assert result.damaged == 4 and result.messages == 0 and result.items == 1 and any("the rules" in note for note in result.notes)
    assert _anna(first) in {settings.account.account_id for settings in second.config().accounts}
    result = _restore(first, tmp_path / "all.cmz", "")  # filled in: the account was here
    assert result.damaged == 4 and len(_mail(first)) == 3


def test_replace_keeps_where_the_mail_is_kept_and_its_encryption(tmp_path: Path) -> None:
    """RV-67: Replace items that already exist put back the account's storage and encryption from the backup."""
    first = _computer(tmp_path / "first")
    anna = _anna(first)
    password = _backup(first, tmp_path / "all.cmz")
    here = next(settings for settings in first.config().accounts if settings.account.account_id == anna)
    first.change_config(lambda fresh: fresh.with_account(dataclasses.replace(here, encrypted=False, signature="Changed")))
    _restore(first, tmp_path / "all.cmz", password, replace_existing=True)
    after = next(settings for settings in first.config().accounts if settings.account.account_id == anna)
    assert after.encrypted is False and after.signature == here.signature


def test_a_failed_restore_leaves_nothing_it_wrote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-68: when there was no data before, what the failed restore wrote stayed while the accounts were put back as none."""
    first = _computer(tmp_path / "first")
    password = _backup(first, tmp_path / "all.cmz")
    second = _fresh(tmp_path / "second")
    accounts = second.directories.data_directory / "accounts"
    before = sorted(path.name for path in accounts.iterdir()) if accounts.exists() else list[str]()

    def fails(*_: object) -> None:
        raise ChronoMailError("The disk is full")

    monkeypatch.setattr(restore, "_restore_rules", fails)
    with pytest.raises(ChronoMailError, match="disk is full"):
        _restore(second, tmp_path / "all.cmz", password, remove_first=True)
    assert (sorted(path.name for path in accounts.iterdir()) if accounts.exists() else list[str]()) == before
    assert not list(second.directories.data_directory.glob("restore-aside-*"))


def test_a_calendars_and_contacts_account_is_found_by_its_server_and_login(tmp_path: Path) -> None:
    """RV-73: such an account was found only by its id: a restore brought the same login in twice."""

    def dav(username: str, url: str) -> AccountSettings:
        account = dataclasses.replace(make_account(), kind=AccountKind.DAV, email_address="")
        return AccountSettings(dataclasses.replace(account, incoming=dataclasses.replace(account.incoming, username=username)), dav_url=url)

    here = dav("ann", "https://dav.example.com/")
    config = AppConfig((here,))
    assert restore._same_here(config, dav("Ann", "https://DAV.example.com")) is here  # noqa: SLF001
    assert restore._same_here(config, dav("bob", "https://dav.example.com/")) is None  # noqa: SLF001
