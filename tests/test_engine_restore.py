import dataclasses
import hashlib
import json
import os
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from chrono_mail.config import AccountSettings, Action, AppConfig, Condition, Pop3DeletePolicy, Rule, RuleSet, ServerRole, load_rules, save_rules
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, ChronoMailError, CollectionKind, FolderRole, MessageFlag, MessageRef
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
from chrono_mail.engine.commands_accounts import KEY_CODE_STORED, NoParameters
from chrono_mail.engine.commands_schedule import MAIL_SCHEDULED, AccountOptional
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import ensure_local_pim, open_pim
from chrono_mail.engine.restore import BackupPasswordNeeded, NotABackup, RestoreCancelled, WrongBackupPassword
from chrono_mail.engine.session import ServerWorkPaused, let_go_kept_indexes
from chrono_mail.sync.imap_state import ImapState
from chrono_mail.sync.pop3_state import Pop3State
from tests.enginefactory import make_engine
from tests.test_config import make_account
from chrono_mail.mime import with_extra_headers
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_engine_restore.py 3.6.353"

# --- Version history ----------------------------------------------------
# v3.6.353: the files are compared as they lie at rest, the kept index connections let go (D-266)
# v3.6.351: a message that moves during the backup's search is found, never left out; this computer's damaged tags file never
#           stops the restore; a damaged record of the server or file of an account leaves out
#           only that account's part
# v3.6.350: an Outbox message the server took before comes back in Sent, never as a draft (D-262); a folder description the store could
#           not use leaves its messages out of what was downloaded (RV-196); this computer's damaged rules file is told as such (RV-198); a
#           restore after the settings were lost adds to the Local Folders kept here, never replaces it (D-251)
# v3.6.349: a set-aside that stops half-way leaves this computer's data where it is (RV-193); a restored IMAP state names only what came
#           back (RV-194, RV-195); an older backup never leads to POP3 mail deleted on the server (RV-197); a damaged setting is not counted as
#           a damaged message (RV-199); Cancel or quitting during the wait is a cancelled restore, the refusal says try again (RV-174, RV-175)
# v3.5.346: the recovery code is shown before every restore until it was confirmed as stored (RV-03)
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
    """The files as they lie at rest: a kept index connection holds the index's last changes in its log until it is let go (D-266)."""
    let_go_kept_indexes()
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


def test_an_outbox_message_the_server_took_comes_back_in_sent(tmp_path: Path) -> None:
    """A message of the Outbox the server took before (its move to Sent had failed, marked by the letter in its file name or by sent_at)
    came back in Drafts, where the person could send it a second time (D-262)."""
    from chrono_mail.store import SENT_LETTER

    first = _computer(tmp_path / "first")
    with open_mailbox(first, LOCAL_ACCOUNT_ID) as mailbox:
        lettered = mailbox.deliver(mailbox.folder("Outbox"), raw_message(subject="Lettered", message_id="<lettered@example.com>"), frozenset({MessageFlag.SEEN}))
        mailbox.store.set_letter(lettered, SENT_LETTER, True)
        noted = mailbox.deliver(mailbox.folder("Outbox"), raw_message(subject="Noted", message_id="<noted@example.com>"), frozenset({MessageFlag.SEEN}))
        mailbox.store.properties.change(noted.store_id, lambda current: dataclasses.replace(current, sent_at=1_700_000_000))
    password = _backup(first, tmp_path / "all.cmz")
    second = make_engine(tmp_path / "second")
    REGISTRY.run(second, KEY_ENSURE.id, {})
    result = _restore(second, tmp_path / "all.cmz", password)
    mail = _mail(second)
    assert result.to_drafts == 0 and mail[lettered.store_id][0] == "Sent" and mail[noted.store_id][0] == "Sent"  # a whole account
    with open_mailbox(first, LOCAL_ACCOUNT_ID) as mailbox:
        for reference in (lettered, noted):
            mailbox.delete(mailbox.reference(("Outbox",), reference.store_id), permanent=True)
    result = _restore(first, tmp_path / "all.cmz", password)
    mail = _mail(first)
    assert result.to_drafts == 0 and mail[lettered.store_id][0] == "Sent" and mail[noted.store_id][0] == "Sent"  # filled into one that is here


def test_an_outbox_message_only_this_run_knows_was_sent_comes_back_in_sent(tmp_path: Path) -> None:
    """The review of 3.6.350 (D-262): on a full disk neither mark of a message the server took reached it (only this run's memory knew);
    a backup and a restore in the same run brought it back in Drafts, to be sent a second time."""
    first = _computer(tmp_path / "first")
    with open_mailbox(first, LOCAL_ACCOUNT_ID) as mailbox:
        taken = mailbox.deliver(mailbox.folder("Outbox"), raw_message(subject="Taken", message_id="<taken@example.com>"), frozenset({MessageFlag.SEEN}))
    first.sent_unfiled.add(taken.store_id)  # as a send whose marks could not be written leaves it
    password = _backup(first, tmp_path / "all.cmz")
    with open_mailbox(first, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.delete(mailbox.reference(("Outbox",), taken.store_id), permanent=True)
    result = _restore(first, tmp_path / "all.cmz", password)
    assert result.to_drafts == 0 and _mail(first)[taken.store_id][0] == "Sent"  # filled into one that is here
    second = make_engine(tmp_path / "second")
    REGISTRY.run(second, KEY_ENSURE.id, {})
    second.sent_unfiled.add(taken.store_id)  # the same run, a whole account
    result = _restore(second, tmp_path / "all.cmz", password)
    assert result.to_drafts == 0 and _mail(second)[taken.store_id][0] == "Sent"


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
    assert len(walks) == len(set(walks))  # each folder searched once (since 3.6.351 per folder), never again for each deleted message


def test_a_message_that_moves_during_the_search_is_found_never_left_out_as_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The three reviewers of 2026-10-26 (RV-69): a message that moved during the one search, from a folder not searched yet into one
    already searched, was taken as deleted and left out of the backup, though it still existed."""
    engine = _computer(tmp_path / "first", messages=2)
    root = engine.directories.store_directory(LOCAL_ACCOUNT_ID)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        first, second = [message.ref for message in mailbox.store.list_messages(mailbox.folder("Inbox"))]
        archive = mailbox.ensure_folder(("Archive",), FolderRole.ARCHIVE)[0].path
        inbox_directory = mailbox.store._folders[("Inbox",)].directory  # noqa: SLF001
        archive_directory = mailbox.store._folders[archive].directory  # noqa: SLF001
    real_stamp, real_walk = backup._stamp, backup._walk  # noqa: SLF001
    moved: list[bool] = []

    def inbox_last(store_root: Path) -> dict[str, tuple[int, int]]:
        stamps = real_stamp(store_root)
        return {name: stamps[name] for name in sorted(stamps, key=lambda name: name == inbox_directory)}

    def moving_during(found: Path) -> Iterator[Path]:
        if found.name == archive_directory and not moved:  # Archive is searched; then the message moves there from the Inbox
            listed = list(real_walk(found))
            with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
                mailbox.move(second, archive)
            moved.append(True)
            return iter(listed)
        return real_walk(found)

    monkeypatch.setattr(backup, "_stamp", inbox_last)
    monkeypatch.setattr(backup, "_walk", moving_during)
    messages = backup._Messages()  # noqa: SLF001
    assert backup._find_again(messages, root, first.store_id) is not None  # noqa: SLF001 - the search that missed the moving one
    assert moved and second.store_id not in messages.places[root]  # it moved from a folder not searched into one searched
    found = backup._find_again(messages, root, second.store_id)  # noqa: SLF001
    assert found is not None and found.exists()


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
    assert result.damaged == 3 and result.messages == 0 and result.items == 1 and any("the rules" in note for note in result.notes)  # the 3 messages
    # of the Inbox; the rules file is named in the notes, never counted as a message that did not come back (RV-199)
    assert _anna(first) in {settings.account.account_id for settings in second.config().accounts}
    result = _restore(first, tmp_path / "all.cmz", "")  # filled in: the account was here
    assert result.damaged == 3 and len(_mail(first)) == 3


def _rewrite(backup: Path, name: str, data: bytes) -> None:
    """A backup (not protected) with this entry's contents replaced: a readable entry whose contents are wrong."""
    with zipfile.ZipFile(backup) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, contents in entries:
            archive.writestr(info, data if info.filename == name else contents)


def test_a_folder_the_store_cannot_use_is_left_out_of_what_was_downloaded(tmp_path: Path) -> None:
    """RV-196: a folder description that could be read but not used (not a folder's) hid its messages after the restore, while the download
    list still named them; with "delete from the server when deleted here" the next check deleted them on the server."""
    first = _computer(tmp_path / "first")
    anna = _anna(first)
    fetched = _downloaded(first, anna, 2)
    _backup(first, tmp_path / "all.cmz", protected=False)
    with zipfile.ZipFile(tmp_path / "all.cmz") as archive:
        inbox = next(
            name for name in archive.namelist() if name.endswith("/folder.json") and f"/{anna}/" in name and json.loads(archive.read(name)).get("path") == ["Inbox"]
        )
    _rewrite(tmp_path / "all.cmz", inbox, b'{"path": ["Inbox"], "role": "no such role"}')
    second = _fresh(tmp_path / "second")
    result = _restore(second, tmp_path / "all.cmz", "")
    assert result.damaged == 2 and _named(second, anna) == set()  # downloaded again, never deleted on the server
    assert set(fetched).isdisjoint(_mail(second, anna))


def test_a_folder_given_twice_comes_back_together_and_odd_descriptions_never_stop_a_restore(tmp_path: Path) -> None:
    """The review of RV-196: a folder made twice (its messages in two folders of one path) was left out as damaged, though the store puts
    them together; a description with a BOM counted as readable although the store cannot use it; a description or a message's details
    nested without end stopped the whole restore."""
    first = _computer(tmp_path / "first")
    anna = _anna(first)
    fetched = _downloaded(first, anna, 2)
    _backup(first, tmp_path / "all.cmz", protected=False)
    with zipfile.ZipFile(tmp_path / "all.cmz") as archive:
        names = archive.namelist()
        descriptions = {name: json.loads(archive.read(name)) for name in names if name.endswith("/folder.json") and f"/{anna}/" in name}
    inbox = next(name for name, description in descriptions.items() if description.get("path") == ["Inbox"])
    sent = next(name for name, description in descriptions.items() if description.get("path") == ["Sent"])
    junk = next(name for name, description in descriptions.items() if description.get("path") == ["Junk"])
    _rewrite(tmp_path / "all.cmz", sent, json.dumps(descriptions[inbox]).encode())  # Sent's folder now says it is the Inbox: a path twice
    _rewrite(tmp_path / "all.cmz", junk, b"\xef\xbb\xbf" + json.dumps(descriptions[junk]).encode())  # a BOM: the store cannot read it
    properties = next((name for name in names if f"/{LOCAL_ACCOUNT_ID}/store/properties/" in name), None)
    if properties is not None:
        _rewrite(tmp_path / "all.cmz", properties, b"[" * 200_000)
    drafts = next(name for name, description in descriptions.items() if description.get("path") == ["Drafts"])
    _rewrite(tmp_path / "all.cmz", drafts, b"[" * 200_000)  # nested without end
    second = _fresh(tmp_path / "second")
    result = _restore(second, tmp_path / "all.cmz", "")
    assert set(fetched) <= set(_mail(second, anna)) and _named(second, anna) == set(fetched)  # both Inbox folders came back, together
    assert all(folder == "Inbox" for folder, _, _ in (_mail(second, anna)[store_id] for store_id in fetched))
    assert result.messages >= 2
    result = _restore(first, tmp_path / "all.cmz", "")  # filled in: the account is here; nothing stops it either
    assert result.messages == 0


def test_an_empty_folder_path_and_deep_details_in_a_backup_never_stop_a_restore(tmp_path: Path) -> None:
    """The review of 3.6.350: a folder description with an empty path was taken as usable (the store hides it; the fill path crashed); the
    details of an Outbox message nested without end, or a rules file nested without end, stopped a whole restore."""
    from chrono_mail.store import SENT_LETTER

    first = _computer(tmp_path / "first")
    anna = _anna(first)
    fetched = _downloaded(first, anna, 2)
    with open_mailbox(first, LOCAL_ACCOUNT_ID) as mailbox:
        waiting = mailbox.deliver(mailbox.folder("Outbox"), raw_message(subject="Waiting", message_id="<waiting@example.com>"), frozenset({MessageFlag.SEEN}))
        mailbox.store.properties.change(waiting.store_id, lambda current: dataclasses.replace(current, sent_at=1_700_000_000))
    _backup(first, tmp_path / "all.cmz", protected=False)
    with zipfile.ZipFile(tmp_path / "all.cmz") as archive:
        names = archive.namelist()
        inbox = next(
            name for name in names if name.endswith("/folder.json") and f"/{anna}/" in name and json.loads(archive.read(name)).get("path") == ["Inbox"]
        )
    details = next(name for name in names if name.endswith(f"{waiting.store_id}.json"))
    _rewrite(tmp_path / "all.cmz", inbox, b'{"path": [], "role": "inbox"}')
    _rewrite(tmp_path / "all.cmz", details, b"[" * 60_000)
    _rewrite(tmp_path / "all.cmz", "config/rules.json", b"[" * 60_000)
    second = _fresh(tmp_path / "second")
    result = _restore(second, tmp_path / "all.cmz", "")  # whole
    assert _named(second, anna) == set() and result.damaged >= 2 and any("the rules" in note for note in result.notes)
    assert SENT_LETTER is not None and set(fetched).isdisjoint(_mail(second, anna))
    result = _restore(first, tmp_path / "all.cmz", "")  # filled in: nothing stops it
    assert result.damaged >= 2


def test_this_computers_damaged_rules_file_is_not_told_as_the_backups(tmp_path: Path) -> None:
    """RV-198: this computer's own rules file could not be read, and the restore said the backup's rules were damaged."""
    first = _computer(tmp_path / "first")
    _backup(first, tmp_path / "all.cmz", protected=False)
    second = _fresh(tmp_path / "second")
    second.directories.rules_file.parent.mkdir(parents=True, exist_ok=True)
    second.directories.rules_file.write_text("not rules", "utf-8")
    result = _restore(second, tmp_path / "all.cmz", "")
    assert not any("Part of the backup could not be read" in note for note in result.notes)
    assert any("This computer's rules could not be read" in note for note in result.notes)
    assert load_rules(second.directories.rules_file) == RULES and (second.directories.rules_file.with_name("rules.json.bad")).read_text("utf-8") == "not rules"


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


def test_the_recovery_code_is_shown_before_every_restore_until_it_was_stored(tmp_path: Path) -> None:
    """RV-03: stopping the first restore at the recovery code made the key; the second restore went on without ever showing the code."""
    engine = make_engine(tmp_path)
    first = REGISTRY.ask(engine, KEY_ENSURE, NoParameters())
    assert first.made and first.show_code
    second = REGISTRY.ask(engine, KEY_ENSURE, NoParameters())  # the person closed the code window and stopped the restore; Restore… again
    assert not second.made and second.show_code
    REGISTRY.ask(engine, KEY_CODE_STORED, NoParameters())  # Done, with the tick
    assert not REGISTRY.ask(engine, KEY_ENSURE, NoParameters()).show_code
    assert not REGISTRY.ask(make_engine(tmp_path), KEY_ENSURE, NoParameters()).show_code  # also after a restart


@pytest.mark.parametrize("held", ["accounts", "tags.json"])
def test_a_set_aside_that_stops_half_way_leaves_this_computers_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: str) -> None:
    """RV-193: Remove all data first could not move one thing aside (a file another program held open); putting back then moved the
    mail that was still in its place away and deleted it with the aside folder, and removed the settings files not moved yet."""
    engine = _computer(tmp_path / "first")
    password = _backup(engine, tmp_path / "all.cmz")
    engine.directories.tags_file.write_text("{}", "utf-8")  # a settings file that is moved aside after rules and sidebar
    before = _tree(engine.directories.data_directory), _tree(engine.directories.config_directory)
    real_replace = os.replace

    def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        if Path(source).name == held and "restore-aside-" in str(target):
            raise PermissionError(13, "The file is in use by another program")
        real_replace(source, target)

    monkeypatch.setattr(restore.os, "replace", replace)
    with pytest.raises(OSError, match="in use"):
        _restore(engine, tmp_path / "all.cmz", password, remove_first=True)
    assert (_tree(engine.directories.data_directory), _tree(engine.directories.config_directory)) == before
    assert not list(engine.directories.data_directory.glob("restore-aside-*"))


def test_a_restored_imap_state_names_only_what_came_back(tmp_path: Path) -> None:
    """RV-194, RV-195: the IMAP state came back as it was: a message it named that did not come back (damaged, too large, downloaded while
    the backup was made) counted as deleted here and was deleted on the server; a folder left out was taken as deleted here and never
    synchronised again. What the person did here and the server had not got yet stays."""
    from chrono_mail.store import Store
    from chrono_mail.sync.imap_state import Checkpoint, ImapState

    store = Store.create(tmp_path / "store", "0" * 31 + "2", None, durable=False)
    store.create_folder(("Inbox",), FolderRole.INBOX)
    store.create_folder(("Renamed",))
    here = store.deliver(("Inbox",), raw_message(subject="Came back")).store_id
    path = tmp_path / "imap-state.log"
    state = ImapState(path, durable=False)
    state.set_validity(("Inbox",), 1)
    state.map(("Inbox",), 1, here, frozenset())
    state.map(("Inbox",), 2, "a" * 32, frozenset())  # not in the backup
    state.map(("Inbox",), 3, "b" * 32, frozenset())  # deleted for good here, offline: the server still has to delete it
    state.note_deleted("b" * 32)
    state.set_checkpoint(("Inbox",), Checkpoint(4, None))  # with CONDSTORE only, a check would ask only what changed since
    for folder, name, validity in ((("Lost",), "Lost", 5), (("Unticked",), "Unticked", 7), (("Old",), "Old", 9)):
        state.set_validity(folder, validity)
        state.set_server_name(folder, name)
        state.map(folder, 1, "c" * 31 + str(validity), frozenset())
    state.hide_folder("Unticked", ("Unticked",))
    state.rename_later(("Old",), ("Renamed",))  # renamed here offline, not on the server yet
    restore._mirror_that_came_back(path, store, durable=False)  # noqa: SLF001
    after = ImapState(path, durable=False)
    assert sorted(after.folder_entries(("Inbox",))) == [1, 3]
    assert ("Lost",) not in after.validity and after.server_names.get(("Lost",)) == "Lost"  # brought from the server again at the next check
    assert ("Inbox",) not in after.checkpoints  # listed in full at the next check: never only what changed since
    assert "Unticked" in after.hidden and ("Renamed",) in after.validity and after.pending  # the person's own choices stay


def _as_older_backup(backup: Path, account_id: str, extra_record: str) -> None:
    """The backup as one made before 3.3.343: its version older, and its download list naming a message it lacks."""
    with zipfile.ZipFile(backup) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(backup, "w") as archive:
        for info, data in entries:
            if info.filename == "chrono-mail-backup.json":
                manifest = json.loads(data)
                manifest["version"] = "3.3.342"
                data = json.dumps(manifest).encode("utf-8")
            elif info.filename == f"data/accounts/{account_id}/pop3-state.log":
                data += extra_record.encode("utf-8")
            archive.writestr(info, data)


def test_an_older_backup_of_an_account_kept_in_local_folders_never_names_a_message_it_lacks(tmp_path: Path) -> None:
    """The review of RV-197: an account that keeps its mail in Local Folders, restored together with Local Folders, kept the older backup's
    list as it was."""
    first = _computer(tmp_path / "first")
    shop = _add_local_pop3(first)
    settings = first.config().find(shop)
    first.change_config(lambda fresh: fresh.with_account(dataclasses.replace(settings, pop3=dataclasses.replace(settings.pop3, policy=Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY))))
    fetched = _downloaded(first, shop, 1, local=True)
    BACKUP_MAKE.answer(REGISTRY.run(first, BACKUP_MAKE.id, {"path": str(tmp_path / "both.cmz"), "accounts": (shop, "local-folders"), "passwords": False}))
    _as_older_backup(tmp_path / "both.cmz", shop, f"D\tuid-later\t{'f' * 32}\t1000001\n")
    second = _fresh(tmp_path / "second")
    _restore(second, tmp_path / "both.cmz", "")
    assert _named(second, shop) == set(fetched)


@pytest.mark.parametrize("version_older", [True, False])
def test_an_older_backup_never_names_a_message_it_lacks(tmp_path: Path, version_older: bool) -> None:
    """RV-197: a backup made before 3.3.343 read the download list after the mail: it named messages downloaded meanwhile, which it lacks;
    with "delete from the server when deleted here" the next check deleted them on the server."""
    first = _computer(tmp_path / "first")
    anna = _anna(first)
    settings = first.config().find(anna)
    first.change_config(lambda fresh: fresh.with_account(dataclasses.replace(settings, pop3=dataclasses.replace(settings.pop3, policy=Pop3DeletePolicy.DELETE_WHEN_DELETED_LOCALLY))))
    fetched = _downloaded(first, anna, 1)
    _backup(first, tmp_path / "all.cmz", protected=False)
    if version_older:
        _as_older_backup(tmp_path / "all.cmz", anna, f"D\tuid-later\t{'f' * 32}\t1000001\n")
    second = _fresh(tmp_path / "second")
    _restore(second, tmp_path / "all.cmz", "")
    assert _named(second, anna) == set(fetched)


@pytest.mark.parametrize("stopped_by", ["cancel", "quit"])
def test_cancel_or_quit_while_the_restore_waits_is_a_cancelled_restore(tmp_path: Path, stopped_by: str) -> None:
    """RV-175, RV-174: Cancel while the restore waited for a running check said "The restore did not finish"; quitting did not end the
    wait; a send refused meanwhile promised to be sent later, which nothing did."""
    engine = _computer(tmp_path / "first")
    password = _backup(engine, tmp_path / "all.cmz")
    checking, release = threading.Event(), threading.Event()

    def a_check_that_runs() -> None:
        with engine.server_work.running():
            checking.set()
            release.wait(10)

    check = threading.Thread(target=a_check_that_runs)
    check.start()
    assert checking.wait(5)
    errors: list[BaseException] = []

    def restoring() -> None:
        try:
            _restore(engine, tmp_path / "all.cmz", password)
        except BaseException as error:  # noqa: BLE001 - kept for the assert below
            errors.append(error)

    thread = threading.Thread(target=restoring)
    thread.start()
    time.sleep(0.3)
    try:
        with pytest.raises(ServerWorkPaused, match="try again when it is done"):
            with engine.server_work.running():
                pass
        if stopped_by == "cancel":
            cancel_backup()
        else:
            engine.stop_event.set()
        thread.join(5)
    finally:
        release.set()
        check.join(5)
    assert len(errors) == 1 and isinstance(errors[0], RestoreCancelled)


def test_settle_waits_for_a_restore_like_a_check(tmp_path: Path) -> None:
    """RV-171: answering "messages out of step" ran a full IMAP sync while a restore was filling or renaming that account's store."""
    engine = _computer(tmp_path / "first")
    with engine.server_work.paused(lambda: False):
        with pytest.raises(ServerWorkPaused):
            REGISTRY.run(engine, "mail.settle", {"account": _anna(engine), "folder": "Inbox", "choice": "keep_here"})



def test_a_restore_after_the_settings_were_lost_adds_to_the_local_folders_kept_here(tmp_path: Path) -> None:
    """The final review of 3.6.350 (D-251): Local Folders still on this computer but missing from the lost settings counted as "not here";
    the backup's older copy took its place whole and the newer mail was moved aside where the program never looks."""
    engine = _computer(tmp_path / "a")
    password = _backup(engine, tmp_path / "b.cmz")
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject="After the backup", body="new\r\n", message_id="<new@example.com>"), frozenset({MessageFlag.SEEN}))
    before = _mail(engine)
    engine.change_config(lambda fresh: AppConfig((), None))  # the settings lost or reset; the mail is still here
    _restore(engine, tmp_path / "b.cmz", password)
    assert _mail(engine) == before
    assert not any(path.name.startswith(f"{LOCAL_ACCOUNT_ID}-kept-") for path in (engine.directories.data_directory / "accounts").iterdir())


def test_this_computers_damaged_tags_file_never_stops_the_restore(tmp_path: Path) -> None:
    """The three reviewers of 2026-10-26: this computer's own tags file could not be read; the restore stopped at its end, after the
    accounts, without the passwords and without a report. It is handled as its rules file: kept as .bad, told, the backup's tags used."""
    first = _computer(tmp_path / "first")
    REGISTRY.run(first, "tag.create", {"name": "Later", "colour": "#00FF00"})
    _backup(first, tmp_path / "all.cmz", protected=False)
    second = _fresh(tmp_path / "second")
    second.directories.tags_file.parent.mkdir(parents=True, exist_ok=True)
    second.directories.tags_file.write_text("not tags", "utf-8")
    result = _restore(second, tmp_path / "all.cmz", "")
    assert any("This computer's tags could not be read" in note for note in result.notes)
    assert second.directories.tags_file.with_name("tags.json.bad").read_text("utf-8") == "not tags"
    assert "Later" in second.directories.tags_file.read_text("utf-8")


def test_a_damaged_record_of_the_server_leaves_out_only_that_accounts_mail(tmp_path: Path) -> None:
    """The three reviewers of 2026-10-26 (RV-65): a damaged imap-state.log in the backup stopped the whole restore part-way. Now that
    account's messages are left out (the server keeps them, never uploaded as new) and told, and the rest comes back."""
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
        on_server = mailbox.restore_here(("Inbox",), raw_message(subject="On the server", message_id="<s@example.com>", body="x" * 4000), frozenset(), "a" * 32)
    state = ImapState(first.directories.imap_state_file(account), durable=False)
    for uid in range(1, 400):  # a state long enough to be stored compressed, so damage is seen
        state.map(("Inbox",), uid, on_server.store_id if uid == 1 else f"{uid:032x}", frozenset())
    _backup(first, tmp_path / "all.cmz", protected=False)
    _damage(tmp_path / "all.cmz", lambda name: name.endswith("imap-state.log"))
    second, _ = imap_computer(tmp_path / "second")
    result = _restore(second, tmp_path / "all.cmz", "")
    assert any("record of the server's messages could not be read" in note for note in result.notes)
    assert [folder for folder, _, _ in _mail(second, second.config().accounts[0].account.account_id).values()] == ["Recovered/Inbox"]  # kept, never uploaded


def test_an_account_that_cannot_come_back_whole_is_left_out_and_the_rest_comes_back(tmp_path: Path) -> None:
    """The three reviewers of 2026-10-26 (RV-65): one damaged file an account needs (its store's description) stopped every account."""
    first = _computer(tmp_path / "first")
    anna = next(settings.account.account_id for settings in first.config().accounts if settings.account.email_address == "anna@example.com")
    with open_mailbox(first, anna) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject="Anna's", message_id="<anna@example.com>"), frozenset())
    _backup(first, tmp_path / "all.cmz", protected=False)
    _damage(tmp_path / "all.cmz", lambda name: name == f"data/accounts/{anna}/store/store.json")
    second = make_engine(tmp_path / "second")
    REGISTRY.run(second, KEY_ENSURE.id, {})
    result = _restore(second, tmp_path / "all.cmz", "")
    assert any("could not come back" in note for note in result.notes) and anna not in result.made
    assert len(_mail(second)) == 3  # Local Folders came back


def test_local_folders_that_cannot_come_back_whole_still_stops_the_restore(tmp_path: Path) -> None:
    """The review of 3.6.351: with only Local Folders left out, a POP3 account keeping its mail there kept its whole download list, which
    named messages that never came back ("delete from the server when deleted here" would delete them). Local Folders still stops it."""
    first = _computer(tmp_path / "first")
    _backup(first, tmp_path / "all.cmz", protected=False)
    _damage(tmp_path / "all.cmz", lambda name: name == f"data/accounts/{LOCAL_ACCOUNT_ID}/store/store.json")
    second = make_engine(tmp_path / "second")
    REGISTRY.run(second, KEY_ENSURE.id, {})
    with pytest.raises(ChronoMailError):
        _restore(second, tmp_path / "all.cmz", "")
    assert second.config().accounts == ()


def test_a_damaged_record_of_the_server_still_brings_back_the_outbox_and_scheduled(tmp_path: Path) -> None:
    """The review of 3.6.351: a damaged imap-state.log left out the account's scheduled messages too, which exist only here, and a message
    written offline in a server folder (never on the server) was lost: those come back in Recovered, on this computer only."""
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
        on_server = mailbox.restore_here(("Inbox",), raw_message(subject="On the server", message_id="<s@example.com>", body="x" * 4000), frozenset(), "a" * 32)
        raw = with_extra_headers(raw_message(subject="Friday", message_id="<friday@example.com>"), {
            "X-Chrono-Mail-Kind": "scheduled", "X-Chrono-Mail-Send-At": str(int(first.clock()) + 3 * 86_400),
        })
        mailbox.ensure_folder(("Scheduled",))
        mailbox.restore_here(("Scheduled",), raw, frozenset(), "b" * 32)
        mailbox.ensure_folder(("Recovered", "Old"))
        mailbox.restore_here(("Recovered", "Old"), raw_message(subject="Recovered before", message_id="<old@example.com>"), frozenset(), "c" * 32)
    state = ImapState(first.directories.imap_state_file(account), durable=False)
    for uid in range(1, 400):
        state.map(("Inbox",), uid, on_server.store_id if uid == 1 else f"{uid:032x}", frozenset())
    _backup(first, tmp_path / "all.cmz", protected=False)
    _damage(tmp_path / "all.cmz", lambda name: name.endswith("imap-state.log"))
    second, _ = imap_computer(tmp_path / "second")
    result = _restore(second, tmp_path / "all.cmz", "")
    kept = sorted(folder for folder, _, _ in _mail(second, second.config().accounts[0].account.account_id).values())
    assert result.messages == 3 and kept == ["Recovered/Inbox", "Recovered/Old", "Scheduled"]  # never lost: kept in Recovered, never nested



def test_a_damaged_record_of_the_servers_items_still_brings_back_the_calendars_on_no_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.351: a damaged dav-state.json left out every calendar, contact and task of the account, also those on no server,
    which exist only in the backup and here."""
    engine = _computer(tmp_path / "first")
    _backup(engine, tmp_path / "all.cmz", protected=False)
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:  # the appointment of this computer is gone; the restore fills it in again
        collection = store.collections(CollectionKind.EVENTS)[0].collection_id
        for item_id in store.item_ids(collection):
            store.delete(collection, item_id)
    assert _items(engine) == []

    def damaged(*_: object) -> dict[str, set[str]]:
        raise restore.DamagedEntry("pim/dav-state.json")

    monkeypatch.setattr(restore, "_linked_items", damaged)
    _restore(engine, tmp_path / "all.cmz", "")
    assert len(_items(engine)) == 1


def test_a_deleted_message_searches_only_a_folder_that_changed_and_once_per_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reviews of 3.6.351: with mail arriving during a large backup, every deleted message searched the whole mailbox again (RV-69).
    Only a folder that changed since its last search is searched again, once per change, never once per deleted message."""
    engine = _computer(tmp_path / "first", messages=4)
    root = engine.directories.store_directory(LOCAL_ACCOUNT_ID)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        inbox = mailbox.folder("Inbox")
        gone = [message.ref.store_id for message in mailbox.store.list_messages(inbox)]
        for store_id in gone:
            mailbox.delete(mailbox.reference(inbox, store_id), permanent=True)
    real_walk = backup._walk  # noqa: SLF001
    walks: list[Path] = []

    def counted(found: Path) -> Iterator[Path]:
        walks.append(found)
        return real_walk(found)

    monkeypatch.setattr(backup, "_walk", counted)
    messages = backup._Messages()  # noqa: SLF001
    assert backup._find_again(messages, root, gone[0]) is None  # noqa: SLF001 - the first search, of every folder
    first_round = len(walks)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:  # one change: mail arrives in the Inbox
        mailbox.deliver(inbox, raw_message(subject="New", message_id="<new-once@example.com>"), frozenset())
    assert [backup._find_again(messages, root, store_id) for store_id in gone[1:]] == [None] * 3  # noqa: SLF001
    again = walks[first_round:]
    assert len(again) == 1 and len(set(walks[:first_round])) == first_round  # only the Inbox, once



def test_messages_deleted_after_the_search_never_search_the_mailbox_each(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.351: a folder searched again kept the entries of its messages that were gone, so each later deleted message found
    a stale entry with nothing changed and searched every folder again (RV-69)."""
    engine = _computer(tmp_path / "first", messages=5)
    root = engine.directories.store_directory(LOCAL_ACCOUNT_ID)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        inbox = mailbox.folder("Inbox")
        listed = [message.ref.store_id for message in mailbox.store.list_messages(inbox)]
    real_walk = backup._walk  # noqa: SLF001
    walks: list[Path] = []

    def counted(found: Path) -> Iterator[Path]:
        walks.append(found)
        return real_walk(found)

    monkeypatch.setattr(backup, "_walk", counted)
    messages = backup._Messages()  # noqa: SLF001
    assert backup._find_again(messages, root, "f" * 32) is None  # noqa: SLF001 - a first miss: every folder searched, all entries known
    first_round = len(walks)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:  # then the person empties part of the Inbox (deleted for good)
        for store_id in listed:
            mailbox.delete(mailbox.reference(inbox, store_id), permanent=True)
    assert [backup._find_again(messages, root, store_id) for store_id in listed] == [None] * len(listed)  # noqa: SLF001
    assert len(walks) - first_round == 1  # the Inbox once, never every folder for each


def test_a_message_renamed_again_right_after_it_was_found_is_still_backed_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.351: a mark set during or right after the search renamed the file again before it was read; it was taken as
    deleted and left out of the backup."""
    engine = _computer(tmp_path / "first", messages=1)
    root = engine.directories.store_directory(LOCAL_ACCOUNT_ID)
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        (message,) = mailbox.store.list_messages(mailbox.folder("Inbox"))
    (now,) = [path for path in (root / "folders").rglob("*") if path.is_file() and path.parent.name in ("cur", "new") and message.ref.store_id in path.name]
    listed_at = now.with_name(f"{message.ref.store_id}!2,")  # where the listing saw it, before a mark renamed it
    found_at = now.with_name(f"{message.ref.store_id}!2,F")  # where the search found it a moment before a mark renamed it again
    real_find = backup._find_again  # noqa: SLF001
    answers: list[Path] = [found_at]

    def found_then_renamed(messages: backup._Messages, store_root: Path, store_id: str) -> Path | None:  # noqa: SLF001
        return answers.pop(0) if answers else real_find(messages, store_root, store_id)

    monkeypatch.setattr(backup, "_find_again", found_then_renamed)
    written: dict[str, bytes] = {}

    class Archive:
        def writestr(self, zinfo_or_arcname: str, data: bytes) -> None:
            written[zinfo_or_arcname] = data

        def setpassword(self, pwd: bytes) -> None:
            pass

        def close(self) -> None:
            pass

    item = backup._File(listed_at, backup.DOING_MESSAGES, True, root)  # noqa: SLF001
    assert backup._add_file(engine, Archive(), item, None, backup._Messages(), [], [])  # noqa: SLF001
    assert len(written) == 1
