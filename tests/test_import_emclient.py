import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import ServerRole, load_rules
from chrono_mail.core import LOCAL_ACCOUNT_ID, AccountKind, CollectionKind, MessageFlag, MessageRef
from chrono_mail.engine import Engine, UsageError
from chrono_mail.engine import commands_emclient
from chrono_mail.engine.commands_emclient import (
    JOURNAL_FILE, ImportParameters, ImportResult, ScanParameters, StoreFound, fingerprint, fingerprint_of_raw, run_import, scan as scan_exports,
)
from chrono_mail.engine.emclient_source import export_layout, pop3_stores, read_backup, read_settings
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.pimaccess import open_pim
from tests.enginefactory import make_engine
from tests.mailfactory import raw_message
from tests.test_emclient_source import (
    NAMESPACE, FakeAccount, FakeContact, FakeItem, category_xml, encode_password, rule_xml, settings_xml, ui_rule, words, write_backup, write_mail_export, write_settings,
)
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_import_emclient.py 4.2.461"

# --- Version history ----------------------------------------------------
# v4.2.461: a POP3 account's store in the backup gives its mail its marks and tags, in its own folders and in Local Folders > its name; an
#           IMAP or calendar account's store is never read; a damaged store leaves out only its marks; tags given later reach its mail (UR-09)
# v4.2.458: Local Folders found by its place in the mail export (the chosen folder holds it, is it, lies inside it, or holds it further
#           down); IMAP stores next to it left out, a POP3 store into its own account, any other store into Local Folders by its name
# v4.2.452: IMAP accounts and calendar servers are made without asking any server, "include in global operations" is the Send and Receive
#           tick, Local Folders' appointments and tasks come from the backup, notes are counted (UR-01); the batch test leaves calendars out
# v4.2.395: the count comes every 100 messages and at the last of each batch
# v4.2.388: a newer export in the same place is a new import; the scan says what was finished; again starts over without doubles
# v4.2.385: the journal is written only when the batch was made safe on disk
# v2.0.287: os.sync replaced also where Windows has none (the Windows computer's report)
# v0.5.143: The progress of the mail counts over the whole import, not per batch
# v0.5.141: import.emclient-passwords
# v0.5.137: tags: categories, tagged mail, mail imported before
# v0.5.133: an account the password store refused is reported and made by the next run; speed: header-only fingerprint, one read of the export per import, verified duplicates, one sync per batch
# v0.5.132: Initial tests: importing accounts, rules, contacts and mail from eM Client's exports (all made-up data)

MOMENT = datetime(2024, 3, 4, 5, 6, 7, tzinfo=UTC)


def mail(number: int, message_id: str | None = None) -> bytes:
    return raw_message(subject=f"Message {number}", body=f"body {number}\r\n", date=MOMENT, message_id=message_id if message_id is not None else f"<m{number}@x>")


@dataclass
class Exports:
    settings: Path
    mail: Path
    backup: Path

    def values(self, **extra: object) -> dict[str, object]:
        values: dict[str, object] = {"settings_file": str(self.settings), "mail_folder": str(self.mail), "backup_file": str(self.backup)}
        values.update(extra)
        return values


def make_exports(folder: Path) -> Exports:
    accounts = [FakeAccount("a1", "a1@example.com", "a1login"), FakeAccount("a2", "a2@example.com", "a2login", days=7)]
    rules = [
        rule_xml("To clients", ui_rule({"usingAccount": "a1@example.com", "moveToFolder": "/Local Folders/Clients", "terminateProcessing": ""}), "SpecifiedAccount", "a1"),
        rule_xml("Blocked", ui_rule({"fromPeople": words("spam@example.com"), "moveToFolder": "special://spam", "terminateProcessing": ""}), kind="Blacklist"),
        rule_xml("Empty", ui_rule({})),
    ]
    messages = {
        "Inbox/001.eml": mail(1),
        "Inbox/002.eml": mail(2),
        "Inbox/Sub/003.eml": mail(3),
        "Junk E-mail/004.eml": mail(4),
        "Outbox/005.eml": mail(5),
        "Clients/006.eml": mail(6, message_id=""),
        "Clients/007.eml": mail(7),
    }
    items = [
        FakeItem("/Inbox", "m1@x", MOMENT, flags=0),
        FakeItem("/Inbox", "m2@x", MOMENT, flags=2 | 4),
        FakeItem("/Inbox/Sub", "m3@x", MOMENT, flags=2, replied=True),
        FakeItem("/Clients", "", MOMENT, flags=0),
    ]
    contacts = [FakeContact("Ann Example", ("ann@example.com",), organization="Example Ltd"), FakeContact("Bob", ("bob@example.com",))]
    return Exports(write_settings(folder, accounts, rules), write_mail_export(folder, messages), write_backup(folder, items, contacts))


def local_messages(engine: Engine) -> dict[str, tuple[tuple[str, ...], frozenset[MessageFlag]]]:
    """subject -> (folder, marks) of everything in Local Folders."""
    found: dict[str, tuple[tuple[str, ...], frozenset[MessageFlag]]] = {}
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        for folder in mailbox.store.list_folders():
            for message in mailbox.index.page(folder.path, limit=1000):
                assert message.subject not in found, f"{message.subject} is there twice"
                found[message.subject] = (message.folder, message.flags)
    return found


def test_everything_is_imported_in_one_run(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    result: Any = call(engine, "import.emclient", **exports.values())
    assert (result.accounts_added, result.accounts_existing, result.accounts_without_password) == (2, 0, ())
    assert (result.rules_added, result.rules_not_imported) == (2, ("Empty: no action that Chrono Mail has",))
    assert (result.contacts_added, result.messages_imported, result.messages_duplicate, result.messages_failed, result.messages_left) == (2, 7, 0, (), 0)
    accounts = {item.email_address: item for item in call(engine, "account.list") if item.kind == "pop3"}
    first = engine.config().find(accounts["a1@example.com"].account_id)
    assert (first.storage, first.skip_known_mail, first.account.incoming.port, first.account.incoming.security.value, first.account.display_name) == ("local", True, 110, "starttls", "Person a1")
    assert engine.passwords.get(first.account.account_id, ServerRole.INCOMING) == "Secret12"
    assert engine.config().find(accounts["a2@example.com"].account_id).pop3.days == 7
    rule, blocked = load_rules(engine.directories.rules_file).rules
    assert rule.accounts == (first.account.account_id,) and rule.name == "To clients"
    assert (blocked.name, blocked.accounts, [action.type for action in blocked.actions]) == ("Blocked", (), ["mark_junk", "stop"])
    found = local_messages(engine)
    seen = MessageFlag.SEEN
    assert found["Message 1"] == (("Inbox",), frozenset())
    assert found["Message 2"] == (("Inbox",), frozenset({seen, MessageFlag.FLAGGED}))
    assert found["Message 3"] == (("Inbox", "Sub"), frozenset({seen, MessageFlag.REPLIED}))
    assert found["Message 4"] == (("Junk",), frozenset({seen}))  # not in the backup: read
    assert found["Message 5"] == (("Drafts",), frozenset({seen}))  # never into the Outbox, so nothing is sent
    assert found["Message 6"] == (("Clients",), frozenset())  # without a Message-ID: its marks found by its date
    names = sorted(item.name for item in call(engine, "contact.list").contacts)
    assert names == ["Ann Example", "Bob"]


def test_an_account_that_exists_is_not_made_again_but_its_rules_follow_it(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    existing: Any = call(
        engine, "account.add", display_name="Mine", email_address="a1@example.com", username="A1LOGIN", password="x", incoming_host="POP.example.com",
        outgoing_host="smtp.example.com", storage="local",
    )
    result: Any = call(engine, "import.emclient", **exports.values(mail=False, contacts=False))
    assert (result.accounts_added, result.accounts_existing) == (1, 1)
    assert engine.passwords.get(existing.account_id, ServerRole.INCOMING) == "x"  # its own password is kept
    assert load_rules(engine.directories.rules_file).rules[0].accounts == (existing.account_id,)


def test_mail_that_was_already_here_is_not_stored_twice(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    call(engine, "local.enable")
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.deliver(("Inbox",), mail(1), frozenset())
        mailbox.ensure_folder(("Elsewhere",))
        mailbox.deliver(("Elsewhere",), mail(6, message_id=""), frozenset())  # no Message-ID: the same by date, sender and subject
    result: Any = call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False))
    assert (result.messages_imported, result.messages_duplicate) == (5, 2)
    local_messages(engine)  # asserts that nothing is there twice


def test_the_mail_comes_in_batches_and_a_stopped_import_continues(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    values = exports.values(accounts=False, rules=False, contacts=False, batch=3)
    first: Any = call(engine, "import.emclient", **values)
    assert (first.messages_imported, first.messages_left) == (3, 4)
    scan: Any = call(engine, "import.emclient-scan", settings_file=str(exports.settings), mail_folder=str(exports.mail), backup_file=str(exports.backup))
    assert (scan.messages, scan.messages_done, scan.finished) == (7, 3, ())  # stopped: not finished, it continues
    second: Any = call(engine, "import.emclient", **values)
    third: Any = call(engine, "import.emclient", **values)
    assert (second.messages_imported, second.messages_left, third.messages_imported, third.messages_left) == (3, 1, 1, 0)
    assert len(local_messages(engine)) == 7
    assert call(engine, "import.emclient", **values).messages_imported == 0


def test_a_message_stored_but_not_yet_written_in_the_journal_is_not_imported_twice(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    values = exports.values(accounts=False, rules=False, contacts=False, batch=2)
    call(engine, "import.emclient", **values)
    journal = engine.directories.data_directory / JOURNAL_FILE
    lines = journal.read_text("utf-8").splitlines()
    journal.write_text("\n".join(lines[:-1]) + "\n" + lines[-1][:5], "utf-8")  # the crash: the last line cut off
    commands_emclient._finished_run()  # and the program starts again: nothing of the import is in memory any more
    result: Any = call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False))
    assert (result.messages_imported, result.messages_duplicate) == (5, 1)
    assert len(local_messages(engine)) == 7


def test_the_accounts_rules_and_contacts_are_done_once(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    call(engine, "import.emclient", **exports.values(batch=1))
    again: Any = call(engine, "import.emclient", **exports.values(batch=1))
    assert (again.accounts_added, again.rules_added, again.contacts_added, again.messages_imported) == (0, 0, 0, 1)
    assert len([item for item in call(engine, "account.list") if item.kind == "pop3"]) == 2
    assert len(load_rules(engine.directories.rules_file).rules) == 2


def test_a_scan_counts_and_changes_nothing(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    scan: Any = call(engine, "import.emclient-scan", settings_file=str(exports.settings), mail_folder=str(exports.mail), backup_file=str(exports.backup))
    assert (scan.accounts, scan.accounts_existing, scan.passwords_missing, scan.rules, scan.rules_not_imported) == (2, 0, (), 2, ("Empty: no action that Chrono Mail has",))
    assert (scan.folders, scan.messages, scan.messages_done, scan.marks, scan.contacts) == (5, 7, 0, 4, 2)
    assert call(engine, "account.list") == ()
    assert not (engine.directories.data_directory / JOURNAL_FILE).exists()


def test_a_choice_without_its_export_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    with pytest.raises(UsageError, match="mail export folder"):
        call(engine, "import.emclient", settings_file=str(exports.settings), mail_folder=None, backup_file=None, contacts=False)
    with pytest.raises(UsageError, match="backup"):
        call(engine, "import.emclient", settings_file=str(exports.settings), mail_folder=str(exports.mail), backup_file=None)
    with pytest.raises(UsageError, match="does not exist"):
        call(engine, "import.emclient", **exports.values(mail_folder=str(tmp_path / "nowhere")))


def test_the_fingerprint_from_the_header_is_the_one_of_the_whole_message() -> None:
    from chrono_mail.mime import parse_message

    samples = [
        mail(1),
        mail(2, message_id=""),
        raw_message(subject="=?utf-8?q?Caf=C3=A9_=E2=82=AC?=", sender='"Ann, Example" <ANN@Example.com>', date=MOMENT, attachment=b"x" * 5000),
        b"From: a@x\r\nSubject: no date and no id\r\n\r\nbody",
        b"garbage without a header",
    ]
    for raw in samples:
        parsed = parse_message(raw)
        whole = fingerprint(parsed.message_id, parsed.date, parsed.from_addresses[0].address if parsed.from_addresses else "", parsed.subject)
        assert fingerprint_of_raw(raw) == whole


def test_the_export_and_the_backup_are_read_once_per_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    reads: list[Path] = []
    original = commands_emclient.read_backup

    def counted(path: Path, work: Path, pop3: dict[str, str] | None = None) -> object:
        reads.append(path)
        return original(path, work, pop3)

    monkeypatch.setattr(commands_emclient, "read_backup", counted)
    values = exports.values(accounts=False, rules=False, contacts=False, batch=2)
    while call(engine, "import.emclient", **values).messages_left:
        pass
    assert len(reads) == 1
    assert local_messages(engine)["Message 3"][1] == frozenset({MessageFlag.SEEN, MessageFlag.REPLIED})  # the marks were found across batches


def test_a_duplicate_whose_copy_is_gone_is_imported_again(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    call(engine, "local.enable")
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        reference = mailbox.deliver(("Inbox",), mail(1), frozenset())
    for found in (tmp_path / "profile").rglob(reference.store_id + "*"):
        found.unlink()  # the index still names the copy, but its file was lost (a crash before it was safe on disk)
    result: Any = call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False))
    assert (result.messages_imported, result.messages_duplicate) == (7, 0)


def test_the_batch_is_made_safe_on_disk_before_it_is_written_as_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    engine.durable = True
    exports = make_exports(tmp_path)
    events: list[str] = []
    journal = engine.directories.data_directory / JOURNAL_FILE
    monkeypatch.setattr("os.sync", lambda: events.append(f"sync, journal lines {len(journal.read_text('utf-8').splitlines())}"), raising=False)  # Windows has no os.sync
    call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False, calendars=False, batch=3))
    assert events == ["sync, journal lines 1"]  # one sync for the batch, while only the journal's first line was written
    assert len(journal.read_text("utf-8").splitlines()) == 4


def test_an_account_the_password_store_refuses_is_reported_and_made_by_the_next_run(tmp_path: Path) -> None:
    from chrono_mail.config import KeyringPasswordStore
    from tests.doubles import MemoryBackend, SessionLostBackend

    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    broken = SessionLostBackend()
    broken.failures_left = 1  # the first password the import stores is refused, the rest work
    working = MemoryBackend()

    class Flaky:
        def get_password(self, service: str, username: str) -> str | None:
            return working.get_password(service, username)

        def set_password(self, service: str, username: str, password: str) -> None:
            broken.set_password(service, username, password)
            working.set_password(service, username, password)

        def delete_password(self, service: str, username: str) -> None:
            working.delete_password(service, username)

    engine.passwords = KeyringPasswordStore("chrono-mail-test", Flaky())
    first: Any = call(engine, "import.emclient", **exports.values(rules=False, contacts=False))
    assert first.accounts_added == 1 and first.messages_imported == 7  # the other account and all mail still came
    assert any("the account was not made (Import again makes it)" in note and "Can't find session" in note for note in first.notes)
    again: Any = call(engine, "import.emclient", **exports.values(rules=False, contacts=False))
    assert (again.accounts_added, again.accounts_existing, again.messages_imported) == (1, 1, 0)


def tagged_exports(folder: Path) -> Exports:
    categories = [category_xml("Work", "#ff0000", "Mail"), category_xml("People", "#00ff00", "Contact")]
    messages = {"Inbox/001.eml": mail(1), "Inbox/002.eml": mail(2)}
    items = [FakeItem("/Inbox", "m1@x", MOMENT, tags=("Work", "Old name")), FakeItem("/Inbox", "m2@x", MOMENT)]
    return Exports(write_settings(folder, [], [], categories), write_mail_export(folder, messages), write_backup(folder, items))


def test_categories_become_tags_and_imported_mail_gets_its_tags(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = tagged_exports(tmp_path)
    scan: Any = call(engine, "import.emclient-scan", settings_file=str(exports.settings), mail_folder=str(exports.mail), backup_file=str(exports.backup))
    assert (scan.tags, scan.tagged) == (2, 1)
    result: Any = call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False))
    assert result.tags_added == 3  # Work, People, and the name a message carries that is no category
    listed = {item.name: item for item in call(engine, "tag.list")}
    assert (listed["Work"].colour, listed["Work"].kinds, listed["Work"].mails) == ("#ff0000", ("mail",), 1)
    assert listed["People"].kinds == ("contacts",) and listed["Old name"].colour == "#808080"
    rows = {row.subject: row.tags for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages}
    assert rows == {"Message 1": ("Work", "Old name"), "Message 2": ()}


def test_mail_imported_before_gets_its_tags_in_a_later_run(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = tagged_exports(tmp_path)
    call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False, tags=False))
    assert {row.subject: row.tags for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages}["Message 1"] == ()
    again: Any = call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False))
    assert (again.messages_imported, again.messages_tagged) == (0, 1)
    assert {row.subject: row.tags for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages}["Message 1"] == ("Work", "Old name")
    third: Any = call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False))
    assert third.messages_tagged == 0  # done once


def test_existing_tags_keep_what_they_have(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    call(engine, "tag.create", name="work", colour="#0000ff")
    exports = tagged_exports(tmp_path)
    call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False, mail=False))
    assert {item.name: item.colour for item in call(engine, "tag.list")}["work"] == "#0000ff"


def test_the_passwords_of_accounts_that_are_here_are_stored_again_from_the_export(tmp_path: Path) -> None:
    from chrono_mail.engine.emclient_source import decode_password

    engine = make_engine(tmp_path / "profile")
    accounts = [
        FakeAccount("a1", "a1@example.com", "a1login", password="Right_pw12!"),
        FakeAccount("a2", "a2@example.com", "a2login", password="bad\x01"),  # comes out as no text: not decodable
        FakeAccount("a3", "a3@example.com", "a3login"),  # not here
    ]
    settings = write_settings(tmp_path, accounts, [])
    assert decode_password("") is None
    here: dict[str, str] = {}
    for login, email in (("a1login", "a1@example.com"), ("a2login", "a2@example.com")):
        made: Any = call(
            engine, "account.add", display_name="Me", email_address=email, username=login, password="wrong one", incoming_host="pop.example.com",
            outgoing_host="smtp.example.com", storage="local",
        )
        here[email] = str(made.account_id)
    other: Any = call(
        engine, "account.add", display_name="Other", email_address="b@example.com", username="b", password="keep me", incoming_host="pop.other.example",
        outgoing_host="smtp.other.example", storage="local",
    )
    result: Any = call(engine, "import.emclient-passwords", settings_file=str(settings))
    assert (result.updated, result.not_decodable, result.not_here) == (("a1@example.com",), ("a2@example.com",), 1)
    assert engine.passwords.get(here["a1@example.com"], ServerRole.INCOMING) == "Right_pw12!"
    assert engine.passwords.get(here["a1@example.com"], ServerRole.OUTGOING) == "Right_pw12!"
    assert engine.passwords.get(here["a2@example.com"], ServerRole.INCOMING) == "wrong one"  # left as it was
    assert engine.passwords.get(str(other.account_id), ServerRole.INCOMING) == "keep me"


def test_the_progress_counts_over_the_whole_import_not_per_batch(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    values = exports.values(accounts=False, rules=False, contacts=False, batch=3)
    seen: list[tuple[int, object]] = []
    engine.progress = lambda what, done, total: seen.append((done, total)) if what == "Importing mail" else None
    for _ in range(3):
        call(engine, "import.emclient", **values)
    assert seen == [(3, 7), (6, 7), (7, 7)]  # the last of each batch (a step of 100 is never reached here)


def test_the_journal_is_written_only_when_the_batch_is_safe_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from collections.abc import Generator
    from contextlib import contextmanager

    from chrono_mail.core import StorageError
    from chrono_mail.store.store import Store

    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)

    @contextmanager
    def failing(self: Store) -> Generator[None]:
        yield
        raise StorageError("The disk is full")

    monkeypatch.setattr(Store, "written_together", failing)
    with pytest.raises(StorageError):
        call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False))
    lines = (engine.directories.data_directory / JOURNAL_FILE).read_text("utf-8").splitlines()
    assert not any(line.startswith('["mail"') for line in lines)  # nothing recorded as done that was not made safe
    commands_emclient._finished_run()


def test_a_retry_after_a_batch_that_could_not_be_made_safe_stores_nothing_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from collections.abc import Generator
    from contextlib import contextmanager

    from chrono_mail.core import StorageError
    from chrono_mail.store.store import Store

    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    real = Store.written_together
    failures = [1]

    @contextmanager
    def failing_once(self: Store) -> Generator[None]:
        with real(self):
            yield
        if failures:
            failures.pop()
            raise StorageError("The disk is full")

    monkeypatch.setattr(Store, "written_together", failing_once)
    values = exports.values(accounts=False, rules=False, contacts=False)
    with pytest.raises(StorageError):
        call(engine, "import.emclient", **values)
    again = call(engine, "import.emclient", **values)  # Import again in the same session: the run is not forgotten by hand
    assert (again.messages_imported, again.messages_duplicate) == (0, 7)
    local_messages(engine)  # asserts that nothing is there twice
    commands_emclient._finished_run()


def test_a_message_whose_tags_could_not_be_given_stays_imported_and_gets_them_later(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import MessageRef, StorageError
    from chrono_mail.engine.mailbox import Mailbox

    engine = make_engine(tmp_path / "profile")
    exports = tagged_exports(tmp_path)
    real = Mailbox.set_tags
    failing = [1]

    def set_tags(self: Mailbox, reference: MessageRef, tags: tuple[str, ...]) -> tuple[str, ...]:
        if failing:
            failing.pop()
            raise StorageError("The properties could not be written")
        return real(self, reference, tags)

    monkeypatch.setattr(Mailbox, "set_tags", set_tags)
    values = exports.values(accounts=False, rules=False, contacts=False)
    first: Any = call(engine, "import.emclient", **values)
    assert (first.messages_imported, first.messages_failed) == (2, ())
    assert any("their tags come with the next import" in note for note in first.notes)
    second: Any = call(engine, "import.emclient", **values)
    assert second.messages_tagged == 1
    assert {row.subject: row.tags for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages}["Message 1"] == ("Work", "Old name")
    assert call(engine, "import.emclient", **values).messages_tagged == 0  # once


def test_tags_waiting_are_tried_once_per_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.core import MessageRef, StorageError
    from chrono_mail.engine.mailbox import Mailbox

    engine = make_engine(tmp_path / "profile")
    exports = tagged_exports(tmp_path)
    real = Mailbox.set_tags

    def failing(self: Mailbox, reference: MessageRef, tags: tuple[str, ...]) -> tuple[str, ...]:
        raise StorageError("The properties could not be written")

    monkeypatch.setattr(Mailbox, "set_tags", failing)
    values = exports.values(accounts=False, rules=False, contacts=False, batch=1)
    first: Any = call(engine, "import.emclient", **values)  # Message 1 is imported; its tags wait
    second: Any = call(engine, "import.emclient", **values)  # the next batch of the same import does not try them again
    assert first.messages_left == 1 and second.messages_left == 0
    assert not any("could not get their tags yet" in note for note in second.notes)
    monkeypatch.setattr(Mailbox, "set_tags", real)
    assert call(engine, "import.emclient", **values).messages_tagged == 1  # a later import


def test_a_newer_export_in_the_same_place_is_a_new_import(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    call(engine, "import.emclient", **exports.values())
    sources = {"settings_file": str(exports.settings), "mail_folder": str(exports.mail), "backup_file": str(exports.backup)}
    assert call(engine, "import.emclient-scan", **sources).finished == commands_emclient.PARTS
    (exports.mail / "Inbox" / "008.eml").write_bytes(mail(8))  # the newer export, written into the same folder
    scan: Any = call(engine, "import.emclient-scan", **sources)
    assert (scan.messages, scan.messages_done, scan.finished) == (8, 0, ())
    result: Any = call(engine, "import.emclient", **exports.values())
    assert (result.messages_imported, result.messages_duplicate, result.accounts_existing, result.contacts_existing) == (1, 7, 2, 2)
    assert len(local_messages(engine)) == 8


def test_import_again_starts_over_and_brings_back_what_was_deleted(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    exports = make_exports(tmp_path)
    call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False))
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        row = next(message for message in mailbox.index.page(("Inbox",), limit=100) if message.subject == "Message 1")
        mailbox.delete(MessageRef(mailbox.store.account_id, row.folder, row.store_id), permanent=True)
    assert call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False)).messages_imported == 0
    again: Any = call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False, again=True))
    assert (again.messages_imported, again.messages_duplicate) == (1, 6)
    assert len(local_messages(engine)) == 7


# --- every kind of account, and Local Folders' calendars and tasks (UR-01) --------------------------------

NOWHERE: str = "https://127.0.0.1:1/dav/"  # nothing answers there: an import that asked the server would fail


def dav_xml(uid: str, email: str, login: str, url: str = NOWHERE, included: str = "true", avatar: int = 17) -> str:
    return f"""<account><AccountName>{email}</AccountName>
  <Account xmlns="{NAMESPACE}" uid="{uid}">
    <PersonName /><LoginName>{login}</LoginName><Password>{encode_password('Cal12345')}</Password>
    <IncludeInGlobalOperations>{included}</IncludeInGlobalOperations><Addresses><Address>{email}</Address></Addresses><AvatarType>{avatar}</AvatarType>
    <Protocol uid="d{uid}"><Type>Dav</Type><Server>{url}</Server><CredentialsMode>UseAccountDefaults</CredentialsMode>
      <LoginName>{login}</LoginName><Password>{encode_password('Cal12345')}</Password><Enabled>true</Enabled></Protocol>
  </Account></account>"""


def left_out(xml: str) -> str:
    """An account's xml with "include in global operations" switched off."""
    return xml.replace("<Addresses>", "<IncludeInGlobalOperations>false</IncludeInGlobalOperations><Addresses>", 1)


def write_export(folder: Path) -> Path:
    imap = FakeAccount("i1", "ann@example.com", "annlogin", incoming_host="imap.example.com", kind="IMAP", port=143)
    pop3 = FakeAccount("p1", "bob@example.com", "boblogin")
    accounts = [imap.xml(), left_out(pop3.xml()), dav_xml("c1", "ann@example.com", "annlogin"), dav_xml("c2", "ann@example.com", "annlogin", avatar=8),
                dav_xml("c3", "club@example.org", "clubber", url="https://127.0.0.1:1/club/", included="false")]
    text = settings_xml([], []).replace("</crypted></accounts>", "</crypted>" + "".join(accounts) + "</accounts>")
    path = folder / "settings.xml"
    path.write_text(text, "utf-8")
    return path


EVENT: str = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:e1@example.com\r\nDTSTART:20260105T090000Z\r\nDTEND:20260105T100000Z\r\nRRULE:FREQ=WEEKLY;COUNT=4\r\nSUMMARY:Team meeting\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
MOVED: str = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:e1@example.com\r\nRECURRENCE-ID:20260112T090000Z\r\nDTSTART:20260112T140000Z\r\nDTEND:20260112T150000Z\r\nSUMMARY:Team meeting (moved)\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
TASK: str = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:t1@example.com\r\nSUMMARY:Water the plants\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"


def add_local_items(backup: Path, work: Path) -> None:
    """Local Folders' appointments (a weekly series with one moved occurrence, kept as an item of its own), a task and two notes."""
    work.mkdir(parents=True, exist_ok=True)
    for kind, rows in (("Event", [(1, 50, None, EVENT), (2, 50, 1, MOVED)]), ("Task", [(1, 60, None, TASK)])):
        index = sqlite3.connect(work / f"{kind.lower()}_index.dat")
        index.execute(f"CREATE TABLE {kind}Items (id INTEGER PRIMARY KEY, folder INTEGER, parent INTEGER)")
        index.executemany(f"INSERT INTO {kind}Items VALUES (?, ?, ?)", [(number, folder, parent) for number, folder, parent, _ in rows])
        index.commit()
        index.close()
        data = sqlite3.connect(work / f"{kind.lower()}_data.dat")
        data.execute(f"CREATE TABLE {kind}ItemContents (id INTEGER, propertyName TEXT, properties, PRIMARY KEY (id, propertyName))")
        data.executemany(f"INSERT INTO {kind}ItemContents VALUES (?, 'calendar', ?)", [(number, text.encode()) for number, _, _, text in rows])
        data.commit()
        data.close()
    notes = sqlite3.connect(work / "note_data.dat")
    notes.execute("CREATE TABLE NoteContent (id INTEGER PRIMARY KEY, body TEXT)")
    notes.executemany("INSERT INTO NoteContent (body) VALUES (?)", [("one",), ("two",)])
    notes.commit()
    notes.close()
    folders = sqlite3.connect(work / "folders.dat")
    folders.execute("CREATE TABLE Folders (id INTEGER PRIMARY KEY, name TEXT, path TEXT)")
    folders.executemany("INSERT INTO Folders VALUES (?, ?, ?)", [(1, "Inbox", "/Inbox"), (50, "Holidays", "/Holidays"), (60, "Tasks", "/Tasks")])
    folders.commit()
    folders.close()
    with zipfile.ZipFile(backup) as archive:
        kept = {name: archive.read(name) for name in archive.namelist() if name != "Local Folders/folders.dat"}
    with zipfile.ZipFile(backup, "w") as archive:
        for name, content in kept.items():
            archive.writestr(name, content)
        for name in ("folders.dat", "event_index.dat", "event_data.dat", "task_index.dat", "task_data.dat", "note_data.dat"):
            archive.write(work / name, f"Local Folders/{name}")


def imported(engine: Engine, settings: Path, backup: Path | None = None) -> object:
    values: dict[str, object] = {"settings_file": str(settings), "mail": False, "contacts": backup is not None, "calendars": backup is not None, "tags": False}
    if backup is not None:
        values["backup_file"] = str(backup)
    return call(engine, "import.emclient", **values)


def test_calendar_accounts_are_read_with_their_server_and_the_include_tick(tmp_path: Path) -> None:
    export = read_settings(write_export(tmp_path))
    kinds = {account.uid: account for account in export.accounts}
    assert [kinds[uid].kind for uid in ("i1", "p1", "c1", "c2", "c3")] == ["imap", "pop3", "dav", "dav", "dav"]
    assert (kinds["c1"].dav_url, kinds["c1"].incoming.username, kinds["c1"].incoming.password) == (NOWHERE, "annlogin", "Cal12345")
    assert (kinds["i1"].included, kinds["p1"].included, kinds["c3"].included) == (True, False, False)


def test_imap_accounts_and_calendar_servers_are_made_without_asking_any_server(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    result = imported(engine, write_export(tmp_path))
    assert (getattr(result, "accounts_added"), getattr(result, "calendars_given")) == (3, 1)  # IMAP, POP3, the club's calendars; Ann's given to her IMAP account
    config = engine.config()
    by_address = {settings.account.email_address: settings for settings in config.accounts if settings.account.kind is not AccountKind.LOCAL}
    ann = by_address["ann@example.com"]
    assert (ann.account.kind, ann.storage, ann.skip_known_mail, ann.account.incoming.port, ann.in_send_receive) == (AccountKind.IMAP, "own", False, 143, True)
    assert (ann.dav_url, ann.dav_username) == (NOWHERE, "annlogin")  # its calendars and contacts: given to the mail account of that address
    assert engine.passwords.get(ann.account.account_id, ServerRole.DAV) == "Cal12345"
    assert by_address["bob@example.com"].in_send_receive is False  # eM Client's "include in global operations" was off
    club = next(settings for settings in config.accounts if settings.account.kind is AccountKind.DAV)
    assert (club.dav_url, club.account.incoming.username, club.in_send_receive) == ("https://127.0.0.1:1/club/", "clubber", False)
    assert engine.passwords.get(club.account.account_id, ServerRole.INCOMING) == "Cal12345"
    assert len([settings for settings in config.accounts if settings.account.kind is AccountKind.DAV]) == 1


def test_an_import_again_makes_no_account_or_calendar_server_twice(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    settings = write_export(tmp_path)
    imported(engine, settings)
    again = call(engine, "import.emclient", settings_file=str(settings), mail=False, contacts=False, calendars=False, tags=False, again=True)
    assert (getattr(again, "accounts_added"), getattr(again, "accounts_existing"), getattr(again, "calendars_given")) == (0, 4, 0)
    assert len(engine.config().accounts) == 4  # Local Folders, IMAP, POP3, the club's calendars


def test_local_appointments_and_tasks_come_in_with_their_moved_occurrence_and_notes_are_counted(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    backup = write_backup(tmp_path, [])
    add_local_items(backup, tmp_path / "items")
    data = read_backup(backup, tmp_path / "work")
    assert (len(data.events), len(data.tasks), data.notes) == (1, 1, 2)
    assert data.events[0].folder == "Holidays" and "RECURRENCE-ID" in data.events[0].ical  # the moved occurrence is part of its series
    result = imported(engine, write_export(tmp_path), backup)
    assert (getattr(result, "events_added"), getattr(result, "tasks_added")) == (1, 1)
    assert any("2 notes not imported" in note for note in getattr(result, "notes"))
    with open_pim(engine, LOCAL_ACCOUNT_ID) as store:
        holidays = next(info for info in store.collections(CollectionKind.EVENTS) if info.name == "Holidays")
        assert store.index.find_uid(holidays.collection_id, "e1@example.com") is not None
        text = store.get(holidays.collection_id, store.index.find_uid(holidays.collection_id, "e1@example.com").item_id)  # type: ignore[union-attr]
        assert "Team meeting (moved)" in text and "RRULE" in text
        tasks = next(info for info in store.collections(CollectionKind.TASKS) if info.name == "Tasks")
        assert store.index.find_uid(tasks.collection_id, "t1@example.com") is not None
    again = call(engine, "import.emclient", settings_file=str(write_export(tmp_path)), backup_file=str(backup), mail=False, tags=False, again=True)
    assert (getattr(again, "events_added"), getattr(again, "events_existing"), getattr(again, "tasks_existing")) == (0, 1, 1)


def test_a_scan_counts_imap_accounts_calendar_servers_appointments_tasks_and_notes(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    backup = write_backup(tmp_path, [])
    add_local_items(backup, tmp_path / "items")
    scan = call(engine, "import.emclient-scan", settings_file=str(write_export(tmp_path)), backup_file=str(backup))
    assert (scan.accounts, scan.imap, scan.calendar_servers, scan.accounts_not_imported) == (2, 1, 2, 0)
    assert (scan.events, scan.tasks, scan.notes) == (1, 1, 2)
    assert len(engine.config().accounts) <= 1  # a scan changes nothing


def test_calendars_and_tasks_alone_can_be_imported_and_a_part_that_cannot_be_read_leaves_the_rest(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    backup = write_backup(tmp_path, [], [FakeContact("Ann Example", ("ann@example.com",))])
    add_local_items(backup, tmp_path / "items")
    alone = call(engine, "import.emclient", backup_file=str(backup), accounts=False, rules=False, mail=False, contacts=False, tags=False)
    assert getattr(alone, "events_added") == 1  # only Calendars and tasks ticked
    broken = tmp_path / "broken"
    broken.mkdir()
    backup = write_backup(broken, [], [FakeContact("Bob Example", ("bob@example.com",))])
    with zipfile.ZipFile(backup, "a") as archive:  # an appointment list of another shape
        work = broken / "odd.dat"
        odd = sqlite3.connect(work)
        odd.execute("CREATE TABLE Something (id INTEGER)")
        odd.commit()
        odd.close()
        archive.write(work, "Local Folders/event_index.dat")
        archive.write(work, "Local Folders/event_data.dat")
    result = call(engine, "import.emclient", backup_file=str(backup), accounts=False, rules=False, mail=False, tags=False)
    assert getattr(result, "contacts_added") == 1  # the contacts still come
    assert any("appointments could not be read" in note for note in getattr(result, "notes"))


def test_a_calendar_server_that_could_not_be_added_comes_with_import_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    settings = write_export(tmp_path)
    real = commands_emclient.account_add_dav

    def refused(engine: Engine, parameters: object) -> object:
        raise UsageError("the password store did not answer")

    monkeypatch.setattr(commands_emclient, "account_add_dav", refused)
    first = imported(engine, settings)
    assert any("were not added" in note for note in getattr(first, "notes"))
    monkeypatch.setattr(commands_emclient, "account_add_dav", real)
    second = call(engine, "import.emclient", settings_file=str(settings), mail=False, contacts=False, calendars=False, tags=False, again=True)
    assert getattr(second, "accounts_added") == 1  # the club's calendars now, as the note said


def test_a_calendar_protocol_is_never_taken_as_an_accounts_incoming_server(tmp_path: Path) -> None:
    imap = FakeAccount("m1", "mixed@example.com", "mixed", incoming_host="imap.example.com", kind="IMAP", port=143)
    xml = imap.xml().replace('<Protocol uid="im1">', f'<Protocol uid="dm1"><Type>Dav</Type><Server>{NOWHERE}</Server></Protocol><Protocol uid="im1">', 1)
    path = tmp_path / "settings.xml"
    path.write_text(settings_xml([], []).replace("</crypted></accounts>", "</crypted>" + xml + "</accounts>"), "utf-8")
    (account,) = read_settings(path).accounts
    assert (account.kind, account.incoming.host) == ("imap", "imap.example.com")


def test_calendars_and_tasks_without_the_backup_are_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    with pytest.raises(UsageError, match="calendars and tasks come from the backup"):
        call(engine, "import.emclient", settings_file=str(write_export(tmp_path)), mail=False, contacts=False, tags=False)


# --- where the mail export's mail goes: Local Folders found by its place, the other accounts' stores next to it (4.2.458) ---------------

STORE_IMAP = FakeAccount("i1", "ann@example.com", "annlogin", incoming_host="imap.example.com", kind="IMAP", port=143)
STORE_POP3 = FakeAccount("p1", "bob@example.com", "boblogin")


def write_eml_files(root: Path, messages: dict[str, int]) -> Path:
    """relative path of a .eml file -> the number of its made-up message; empty folders end in /."""
    for relative, number in messages.items():
        path = root / relative
        if relative.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(mail(number))
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_store_settings(folder: Path, accounts: list[FakeAccount]) -> Path:
    path = folder / "settings.xml"
    path.write_text(settings_xml(accounts, []), "utf-8")
    return path


def where_mail(engine: Engine, account: str = LOCAL_ACCOUNT_ID) -> dict[str, tuple[str, ...]]:
    """subject -> folder of every message in an account's mailbox."""
    found: dict[str, tuple[str, ...]] = {}
    with open_mailbox(engine, account) as mailbox:
        for folder in mailbox.store.list_folders():
            for message in mailbox.index.page(folder.path, limit=1000):
                assert message.subject not in found, f"{message.subject} is there twice"
                found[message.subject] = message.folder
    return found


def import_mail(engine: Engine, chosen: Path, settings: Path | None = None, backup: Path | None = None, accounts: bool = False) -> ImportResult:
    return run_import(engine, ImportParameters(
        str(settings) if settings is not None else None, str(chosen), str(backup) if backup is not None else None, accounts=accounts, rules=False,
        contacts=False, tags=False, calendars=False,
    ))


# The export as eM Client writes it: one folder per store, Local Folders and an IMAP account's, next to each other
STORES: dict[str, int] = {
    "Local Folders/Inbox/001.eml": 1,
    "Local Folders/Inbox/Sub/002.eml": 2,
    "Local Folders/Junk E-mail/003.eml": 3,
    "Local Folders/Clients/004.eml": 4,
    "ann@example.com/Inbox/": 0,
    "ann@example.com/Trash/005.eml": 5,
    "ann@example.com/Public Folders/Tasks/006.eml": 6,
}


def test_the_folder_that_holds_local_folders_puts_its_mail_on_the_standard_folders_and_leaves_the_imap_store_out(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", STORES)
    settings = write_store_settings(tmp_path, [STORE_IMAP])
    backup = write_backup(tmp_path, [FakeItem("/Inbox", "m1@x", MOMENT, flags=0), FakeItem("/Inbox/Sub", "m2@x", MOMENT, flags=2 | 4)])
    found = scan_exports(engine, ScanParameters(str(settings), str(chosen), str(backup)))
    assert (found.layout, found.local_folders, found.messages, found.eml_files) == ("stores", "Local Folders", 4, 6)
    assert found.stores == (StoreFound("ann@example.com", "imap", 2),)
    result = import_mail(engine, chosen, settings, backup)
    assert (result.messages_imported, result.messages_failed, result.messages_left) == (4, (), 0)
    assert where_mail(engine) == {"Message 1": ("Inbox",), "Message 2": ("Inbox", "Sub"), "Message 3": ("Junk",), "Message 4": ("Clients",)}
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        flags = {row.subject: row.flags for folder in mailbox.store.list_folders() for row in mailbox.index.page(folder.path, limit=100)}
    assert flags["Message 1"] == frozenset() and flags["Message 2"] == frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED})  # the backup's marks still found


def test_local_folders_is_found_further_down_and_folders_next_to_it_are_stores(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", {f"backup/{relative}": number for relative, number in STORES.items()} | {"backup/Archive/Old/007.eml": 7})
    result = import_mail(engine, chosen, write_store_settings(tmp_path, [STORE_IMAP]))
    assert result.messages_imported == 5
    assert where_mail(engine) == {
        "Message 1": ("Inbox",), "Message 2": ("Inbox", "Sub"), "Message 3": ("Junk",), "Message 4": ("Clients",), "Message 7": ("Archive", "Old"),
    }  # Archive: a store of no account, into Local Folders by its name


def test_local_folders_chosen_itself_finds_the_accounts_stores_next_to_it(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    write_eml_files(tmp_path / "export", STORES | {"Unrelated/008.eml": 8})
    chosen = tmp_path / "export" / "Local Folders"
    assert [store.path for store in export_layout(chosen, read_settings(write_store_settings(tmp_path, [STORE_IMAP])).accounts).stores] == [("ann@example.com",)]  # only an account's
    result = import_mail(engine, chosen, write_store_settings(tmp_path, [STORE_IMAP]))
    assert result.messages_imported == 4 and "Message 8" not in where_mail(engine)
    alone = make_engine(tmp_path / "alone")
    import_mail(alone, chosen)  # without the settings export: Local Folders alone, as before
    assert where_mail(alone)["Message 3"] == ("Junk",)


def test_a_folder_chosen_inside_local_folders_lands_at_its_place_there(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    write_eml_files(tmp_path / "export", STORES)
    import_mail(engine, tmp_path / "export" / "Local Folders" / "Clients")
    import_mail(engine, tmp_path / "export" / "Local Folders" / "Junk E-mail")
    assert where_mail(engine) == {"Message 4": ("Clients",), "Message 3": ("Junk",)}


def test_the_standard_folders_are_known_in_the_language_of_the_export(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", {"Local Folders/Postvak IN/001.eml": 1, "Local Folders/Ongewenste e-mail/002.eml": 2, "Local Folders/Klanten/003.eml": 3})
    import_mail(engine, chosen)
    assert where_mail(engine) == {"Message 1": ("Inbox",), "Message 2": ("Junk",), "Message 3": ("Klanten",)}


def test_a_pop3_accounts_own_store_goes_into_its_own_folders(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", {"Local Folders/Inbox/001.eml": 1, "bob@example.com/Inbox/002.eml": 2, "bob@example.com/Sent/003.eml": 3})
    settings = write_store_settings(tmp_path, [STORE_POP3])
    assert scan_exports(engine, ScanParameters(str(settings), str(chosen), None)).stores == (StoreFound("bob@example.com", "pop3-new", 2),)
    import_mail(engine, chosen, settings, accounts=True)
    bob = next(settings.account for settings in engine.config().accounts if settings.account.kind is AccountKind.POP3)
    assert engine.config().find(bob.account_id).storage == "own"
    assert where_mail(engine, bob.account_id) == {"Message 2": ("Inbox",), "Message 3": ("Sent",)}
    assert where_mail(engine) == {"Message 1": ("Inbox",)}


def test_without_local_folders_every_folder_goes_into_local_folders_as_before(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", {"Inbox/001.eml": 1, "Projects/002.eml": 2})
    found = scan_exports(engine, ScanParameters(None, str(chosen), None))
    assert (found.layout, found.top_folders, found.stores) == ("none", ("Inbox", "Projects"), ())
    import_mail(engine, chosen)
    assert where_mail(engine) == {"Message 1": ("Inbox",), "Message 2": ("Projects",)}


def test_a_folder_without_messages_or_only_an_imap_store_has_nothing_to_import(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    empty = write_eml_files(tmp_path / "empty", {"Something/": 0})
    found = scan_exports(engine, ScanParameters(None, str(empty), None))
    assert (found.messages, found.eml_files) == (0, 0)
    imap_only = write_eml_files(tmp_path / "imap", {"ann@example.com/Inbox/001.eml": 1})
    settings = write_store_settings(tmp_path, [STORE_IMAP])
    found = scan_exports(engine, ScanParameters(str(settings), str(imap_only / "ann@example.com"), None))
    assert (found.messages, found.eml_files, found.stores) == (0, 1, (StoreFound("ann@example.com", "imap", 1),))
    assert import_mail(engine, imap_only / "ann@example.com", settings).messages_imported == 0


def test_a_calendar_account_of_the_same_address_never_makes_an_imap_store_local_mail(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", STORES)
    settings = tmp_path / "settings.xml"
    settings.write_text(settings_xml([], []).replace("</crypted></accounts>", "</crypted>" + dav_xml("c1", "ann@example.com", "annlogin") + STORE_IMAP.xml() + "</accounts>"), "utf-8")
    assert scan_exports(engine, ScanParameters(str(settings), str(chosen), None)).stores == (StoreFound("ann@example.com", "imap", 2),)
    assert import_mail(engine, chosen, settings).messages_imported == 4


def test_folders_around_a_chosen_local_folders_count_only_by_an_accounts_address(tmp_path: Path) -> None:
    work = FakeAccount("w1", "work@example.com", "worklogin")
    xml = work.xml().replace("<AccountName>work@example.com</AccountName>", "<AccountName>Work</AccountName>", 1)
    settings = tmp_path / "settings.xml"
    settings.write_text(settings_xml([], []).replace("</crypted></accounts>", "</crypted>" + xml + "</accounts>"), "utf-8")
    write_eml_files(tmp_path / "home", {"Local Folders/Inbox/001.eml": 1, "Work/notes.eml": 2})
    engine = make_engine(tmp_path / "profile")
    assert import_mail(engine, tmp_path / "home" / "Local Folders", settings).messages_imported == 1  # ~/Work is no store of the account named Work


def test_without_local_folders_a_folder_named_like_an_account_is_imported_as_before(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "bob@example.com", {"Inbox/001.eml": 1})
    import_mail(engine, chosen, write_store_settings(tmp_path, [STORE_POP3]), accounts=True)
    assert where_mail(engine) == {"Message 1": ("Inbox",)}  # into Local Folders, as before; only an IMAP account's address leaves it out


def test_an_imap_accounts_folder_anywhere_outside_local_folders_is_left_out(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "exports", {f"2024/{relative}": number for relative, number in STORES.items()} | {
        "2023/Local Folders/Inbox/007.eml": 7, "2023/ann@example.com/Inbox/008.eml": 8,
    })
    result = import_mail(engine, chosen, write_store_settings(tmp_path, [STORE_IMAP]))
    assert "Message 8" not in where_mail(engine) and result.messages_imported == 5  # the 2023 Local Folders by its path, Ann's mail never


def test_a_store_name_two_accounts_share_is_no_store_of_either(tmp_path: Path) -> None:
    accounts = [FakeAccount("w1", "one@example.com", "one"), FakeAccount("w2", "two@example.com", "two", incoming_host="imap.example.com", kind="IMAP", port=143)]
    xml = "".join(account.xml().replace(f"<AccountName>{account.email}</AccountName>", "<AccountName>Work</AccountName>", 1) for account in accounts)
    settings = tmp_path / "settings.xml"
    settings.write_text(settings_xml([], []).replace("</crypted></accounts>", "</crypted>" + xml + "</accounts>"), "utf-8")
    chosen = write_eml_files(tmp_path / "export", {"Local Folders/Inbox/001.eml": 1, "Work/Inbox/002.eml": 2})
    engine = make_engine(tmp_path / "profile")
    assert scan_exports(engine, ScanParameters(str(settings), str(chosen), None)).stores == (StoreFound("Work", "other", 1),)


def test_an_imap_store_whose_address_two_accounts_share_is_still_left_out(tmp_path: Path) -> None:
    twin = FakeAccount("i2", "ann@example.com", "annother", incoming_host="imap2.example.com", kind="IMAP", port=143)
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", STORES)
    assert import_mail(engine, chosen, write_store_settings(tmp_path, [STORE_IMAP, twin])).messages_imported == 4  # never Ann's 2


def test_own_folders_named_after_an_address_in_a_second_export_are_imported(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "exports", {"A/Local Folders/Inbox/001.eml": 1, "B/Local Folders/ann@example.com/002.eml": 2, "B/ann@example.com/Inbox/003.eml": 3})
    import_mail(engine, chosen, write_store_settings(tmp_path, [STORE_IMAP]))
    found = where_mail(engine)
    assert "Message 2" in found and "Message 3" not in found


def test_a_folder_chosen_inside_an_imap_store_is_left_out(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    write_eml_files(tmp_path / "export", STORES)
    assert import_mail(engine, tmp_path / "export" / "ann@example.com" / "Trash", write_store_settings(tmp_path, [STORE_IMAP])).messages_imported == 0


def test_where_a_pop3_store_goes_is_decided_once_and_kept_through_a_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", {"Local Folders/Inbox/001.eml": 1, "bob@example.com/Inbox/002.eml": 2, "bob@example.com/Inbox/003.eml": 3})
    settings = write_store_settings(tmp_path, [STORE_POP3])
    values = ImportParameters(str(settings), str(chosen), None, accounts=False, rules=False, contacts=False, tags=False, calendars=False, batch=2)
    run_import(engine, values)  # no account for Bob yet: his store goes into Local Folders > bob@example.com
    commands_emclient._finished_run()  # as after a restart: the run in memory is gone
    call(engine, "account.add", display_name="Bob", email_address="bob@example.com", username="boblogin", password="x", incoming_host="pop.example.com",
         outgoing_host="smtp.example.com", storage="own")
    run_import(engine, values)
    assert where_mail(engine) == {"Message 1": ("Inbox",), "Message 2": ("bob@example.com", "Inbox"), "Message 3": ("bob@example.com", "Inbox")}


def test_a_folder_deep_inside_local_folders_named_after_an_imap_address_is_the_persons(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    write_eml_files(tmp_path / "export", {"Local Folders/Archive/ann@example.com/001.eml": 1})
    assert import_mail(engine, tmp_path / "export" / "Local Folders" / "Archive" / "ann@example.com", write_store_settings(tmp_path, [STORE_IMAP])).messages_imported == 1


def test_a_store_whose_address_a_pop3_and_an_imap_account_share_is_imported_never_skipped(tmp_path: Path) -> None:
    pop3 = FakeAccount("p2", "ann@example.com", "annpop")
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", STORES)
    settings = write_store_settings(tmp_path, [STORE_IMAP, pop3])
    assert scan_exports(engine, ScanParameters(str(settings), str(chosen), None)).stores == (StoreFound("ann@example.com", "other", 2),)
    assert import_mail(engine, chosen, settings).messages_imported == 6  # unclear: into Local Folders > ann@example.com, the only copy is never lost


def test_an_account_made_after_its_store_went_into_local_folders_keeps_its_mail_there(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", {"Local Folders/Inbox/001.eml": 1, "bob@example.com/Inbox/002.eml": 2, "bob@example.com/Inbox/003.eml": 3})
    settings = write_store_settings(tmp_path, [STORE_POP3])
    run_import(engine, ImportParameters(str(settings), str(chosen), None, accounts=False, rules=False, contacts=False, tags=False, calendars=False, batch=2))
    commands_emclient._finished_run()
    found = scan_exports(engine, ScanParameters(str(settings), str(chosen), None))
    assert found.stores == (StoreFound("bob@example.com", "other", 2),)  # the scan says what the import will do: as already decided
    run_import(engine, ImportParameters(str(settings), str(chosen), None, accounts=True, rules=False, contacts=False, tags=False, calendars=False))
    bob = next(settings.account for settings in engine.config().accounts if settings.account.kind is AccountKind.POP3)
    assert engine.config().find(bob.account_id).storage == "local"  # with its mail, not in empty folders of its own
    assert where_mail(engine)["Message 3"] == ("bob@example.com", "Inbox")


def test_a_folder_two_levels_inside_local_folders_below_an_address_folder_is_imported(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    write_eml_files(tmp_path / "ann@example.com", {"Local Folders/Archive/2020/001.eml": 1})
    chosen = tmp_path / "ann@example.com" / "Local Folders" / "Archive" / "2020"
    assert import_mail(engine, chosen, write_store_settings(tmp_path, [STORE_IMAP])).messages_imported == 1


def test_without_local_folders_an_imap_accounts_folder_below_the_chosen_one_is_left_out(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", {"Clients/001.eml": 1, "ann@example.com/Inbox/002.eml": 2})
    import_mail(engine, chosen, write_store_settings(tmp_path, [STORE_IMAP]))
    assert where_mail(engine) == {"Message 1": ("Clients",)}


def test_an_address_counts_as_an_imap_store_only_where_a_store_sits(tmp_path: Path) -> None:
    settings = write_store_settings(tmp_path, [STORE_IMAP])
    kept = make_engine(tmp_path / "kept")
    under = write_eml_files(tmp_path / "ann@example.com" / "backup" / "export", {"bob@example.com/Inbox/001.eml": 1})
    assert import_mail(kept, under, settings).messages_imported == 1  # an export kept below a folder named after the address
    own = make_engine(tmp_path / "own")
    chosen = write_eml_files(tmp_path / "export", {"bob@example.com/Inbox/ann@example.com/002.eml": 2})
    assert import_mail(own, chosen, settings).messages_imported == 1  # the person's own folder inside another store


# --- a POP3 account's own store in the backup gives its mail its marks and tags (UR-09) --------------------------------------------------

POP3_STORE: dict[str, int] = {
    "Local Folders/Inbox/001.eml": 1,
    "bob@example.com/Inbox/002.eml": 2,
    "bob@example.com/Inbox/003.eml": 3,
    "bob@example.com/Sent/004.eml": 4,
}
POP3_MARKS: list[FakeItem] = [
    FakeItem("/Inbox", "m2@x", MOMENT, flags=0),  # unread
    FakeItem("/Inbox", "m3@x", MOMENT, flags=2 | 4, tags=("Work",)),  # read and flagged, with a category
    FakeItem("/Sent", "m4@x", MOMENT, replied=True),  # read and replied
]


def add_store(backup: Path, folder: Path, account_folder: str, items: list[FakeItem], damaged: bool = False) -> None:
    """An account's store in the backup as the reference client writes it: <account id>/<store id>/folders.dat and mail_index.dat."""
    made = zipfile.ZipFile(write_backup(folder, items))
    with made, zipfile.ZipFile(backup, "a") as archive:
        for name in ("folders.dat", "mail_index.dat"):
            content = b"not a database" if damaged and name == "mail_index.dat" else made.read(f"Local Folders/{name}")
            archive.writestr(f"{account_folder}/7f3a9c2e-0b1d-4e5f-8a6b-1c2d3e4f5a6b/{name}", content)


def flags_and_tags(engine: Engine, account: str) -> dict[str, tuple[frozenset[MessageFlag], tuple[str, ...]]]:
    """subject -> marks and tags of every message in an account's mailbox."""
    with open_mailbox(engine, account) as mailbox:
        return {
            row.subject: (row.flags, tuple(mailbox.tags_of(row.store_id)))
            for folder in mailbox.store.list_folders() for row in mailbox.index.page(folder.path, limit=100)
        }


def pop3_exports(folder: Path, damaged: bool = False) -> tuple[Path, Path, Path]:
    """The mail export (Local Folders and Bob's own store), the settings export and the backup with Local Folders' marks and Bob's store."""
    chosen = write_eml_files(folder / "export", POP3_STORE)
    settings = write_store_settings(folder, [STORE_POP3])
    backup = write_backup(folder, [FakeItem("/Inbox", "m1@x", MOMENT, flags=0)])
    add_store(backup, folder / "store", "P1", POP3_MARKS, damaged)  # the id compared ignoring case
    return chosen, settings, backup


def import_all(engine: Engine, chosen: Path, settings: Path, backup: Path, accounts: bool = True, tags: bool = True, mail: bool = True) -> ImportResult:
    return run_import(engine, ImportParameters(
        str(settings), str(chosen), str(backup), accounts=accounts, rules=False, mail=mail, contacts=False, tags=tags, calendars=False,
    ))


def bob_id(engine: Engine) -> str:
    return next(settings.account.account_id for settings in engine.config().accounts if settings.account.kind is AccountKind.POP3)


def test_a_pop3_accounts_own_store_gives_its_mail_its_marks_and_tags(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path)
    found = scan_exports(engine, ScanParameters(str(settings), str(chosen), str(backup)))
    assert (found.marks, found.tagged) == (4, 1)  # Local Folders' one and Bob's store's three
    result = import_all(engine, chosen, settings, backup)
    assert (result.messages_imported, result.messages_failed) == (4, ())
    assert flags_and_tags(engine, bob_id(engine)) == {
        "Message 2": (frozenset(), ()),
        "Message 3": (frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}), ("Work",)),
        "Message 4": (frozenset({MessageFlag.SEEN, MessageFlag.REPLIED}), ()),
    }
    assert flags_and_tags(engine, LOCAL_ACCOUNT_ID) == {"Message 1": (frozenset(), ())}  # Local Folders' marks as before


def test_a_pop3_store_that_goes_into_local_folders_by_its_name_keeps_its_marks(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path)
    import_all(engine, chosen, settings, backup, accounts=False)  # no account for Bob here: his store into Local Folders > his address
    assert where_mail(engine)["Message 2"] == ("bob@example.com", "Inbox")
    marks = flags_and_tags(engine, LOCAL_ACCOUNT_ID)
    assert (marks["Message 2"], marks["Message 3"][0], marks["Message 4"][0]) == (
        (frozenset(), ()), frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}), frozenset({MessageFlag.SEEN, MessageFlag.REPLIED}),
    )


def test_the_store_of_an_imap_or_calendar_account_is_never_read(tmp_path: Path) -> None:
    accounts = read_settings(write_store_settings(tmp_path, [STORE_IMAP, STORE_POP3])).accounts
    backup = write_backup(tmp_path, [])
    add_store(backup, tmp_path / "imap", "i1", [FakeItem("/Inbox", "m5@x", MOMENT)])  # the IMAP account's: its marks are the server's
    add_store(backup, tmp_path / "calendar", "c9", [FakeItem("/Inbox", "m6@x", MOMENT)])  # a calendar account's (no mail account of the export)
    add_store(backup, tmp_path / "pop3", "p1", POP3_MARKS)
    data = read_backup(backup, tmp_path / "work", pop3_stores(accounts))
    assert set(data.store_marks) == {"p1"}
    assert data.counted_marks() == 3


def test_a_store_that_cannot_be_read_leaves_out_only_its_marks_and_says_so(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path, damaged=True)
    result = import_all(engine, chosen, settings, backup)
    assert (result.messages_imported, result.messages_failed) == (4, ())  # the mail still comes in
    assert any(note.startswith("bob@example.com: the read, flagged and replied marks and the tags of its mail could not be read") for note in result.notes)
    assert flags_and_tags(engine, bob_id(engine))["Message 2"][0] == frozenset({MessageFlag.SEEN})  # without the backup's marks: read, as before
    assert flags_and_tags(engine, LOCAL_ACCOUNT_ID) == {"Message 1": (frozenset(), ())}  # Local Folders' marks still come


def test_tags_chosen_later_reach_the_mail_of_a_pop3_store_that_is_here(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path)
    import_all(engine, chosen, settings, backup, tags=False)
    assert flags_and_tags(engine, bob_id(engine))["Message 3"][1] == ()
    later = import_all(engine, chosen, settings, backup, accounts=False, mail=False)
    assert later.messages_tagged == 1
    assert flags_and_tags(engine, bob_id(engine))["Message 3"][1] == ("Work",)


def test_a_damaged_store_entry_in_the_zip_leaves_out_only_that_accounts_marks(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path)
    name = "P1/7f3a9c2e-0b1d-4e5f-8a6b-1c2d3e4f5a6b/mail_index.dat"
    with zipfile.ZipFile(backup) as archive:
        entry = archive.getinfo(name)
    damaged = bytearray(backup.read_bytes())
    start = entry.header_offset + 30 + len(name.encode()) + int.from_bytes(damaged[entry.header_offset + 28:entry.header_offset + 30], "little")
    damaged[start + 100] ^= 0xFF  # its contents no longer match its CRC-32
    backup.write_bytes(bytes(damaged))
    result = import_all(engine, chosen, settings, backup)
    assert (result.messages_imported, result.messages_failed) == (4, ())
    assert any(note.startswith("bob@example.com: the read, flagged and replied marks and the tags of its mail could not be read") for note in result.notes)
    assert flags_and_tags(engine, LOCAL_ACCOUNT_ID) == {"Message 1": (frozenset(), ())}  # the rest of the backup still comes


def test_an_account_folder_written_in_two_spellings_never_mixes_their_files(tmp_path: Path) -> None:
    accounts = read_settings(write_store_settings(tmp_path, [STORE_POP3])).accounts
    backup = write_backup(tmp_path, [])
    add_store(backup, tmp_path / "lower", "p1", [FakeItem("/Inbox", "m2@x", MOMENT, flags=0)])
    with zipfile.ZipFile(write_backup(tmp_path / "upper", [FakeItem("/Sent", "m9@x", MOMENT)])) as other, zipfile.ZipFile(backup, "a") as archive:
        archive.writestr("P1/7f3a9c2e-0b1d-4e5f-8a6b-1c2d3e4f5a6b/folders.dat", other.read("Local Folders/folders.dat"))  # only half a store
    data = read_backup(backup, tmp_path / "work", pop3_stores(accounts))
    assert data.store_marks["p1"] == {("inbox", "id:m2@x"): [(frozenset(), ())]}  # its own folders file, never the other spelling's


def test_import_again_gives_the_tags_to_a_pop3_stores_mail_that_is_here(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path)
    import_all(engine, chosen, settings, backup, tags=False)
    again = run_import(engine, ImportParameters(
        str(settings), str(chosen), str(backup), accounts=False, rules=False, contacts=False, tags=True, calendars=False, again=True,
    ))
    assert (again.messages_imported, again.messages_duplicate, again.messages_tagged) == (0, 4, 1)
    assert flags_and_tags(engine, bob_id(engine))["Message 3"][1] == ("Work",)


def test_a_pop3_store_not_in_the_mail_export_is_not_read(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen = write_eml_files(tmp_path / "export", {"Local Folders/Inbox/001.eml": 1})
    settings = write_store_settings(tmp_path, [STORE_POP3])
    backup = write_backup(tmp_path, [FakeItem("/Inbox", "m1@x", MOMENT, flags=0)])
    add_store(backup, tmp_path / "store", "p1", POP3_MARKS)
    found = scan_exports(engine, ScanParameters(str(settings), str(chosen), str(backup)))
    assert (found.marks, found.tagged) == (1, 0)  # only what this import can give


def test_import_again_with_tags_only_gives_the_tags_to_a_pop3_stores_mail_here_and_says_nothing_false(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path)
    import_all(engine, chosen, settings, backup, tags=False)
    for mail_too in (False, True):
        again = run_import(engine, ImportParameters(
            str(settings), str(chosen), str(backup), accounts=False, rules=False, mail=mail_too, contacts=False, tags=True, calendars=False, again=True,
        ))
        assert not any("not here yet" in note for note in again.notes)
        assert flags_and_tags(engine, bob_id(engine))["Message 3"][1] == ("Work",)
    assert again.messages_tagged == 1  # found where its store went, also on the second pass (its tags then unchanged)


def test_a_store_that_cannot_be_read_is_said_once_per_import_also_with_tags_only(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path, damaged=True)
    told = run_import(engine, ImportParameters(str(settings), str(chosen), str(backup), accounts=False, rules=False, mail=False, contacts=False, tags=True, calendars=False))
    assert sum("could not be read from the backup" in note for note in told.notes) == 1
    commands_emclient._finished_run()  # as after a failed batch or a restart: the same import goes on
    later = run_import(engine, ImportParameters(str(settings), str(chosen), str(backup), accounts=False, rules=False, contacts=False, tags=True, calendars=False, batch=2))
    assert not any("could not be read from the backup" in note for note in later.notes)


def test_a_pop3_store_found_by_its_name_and_its_address_has_its_tags_counted_once(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "profile")
    work = FakeAccount("p1", "bob@example.com", "boblogin")
    chosen = write_eml_files(tmp_path / "export", {"Local Folders/Inbox/001.eml": 1, "bob@example.com/Inbox/003.eml": 3, "bob@example.com/Inbox/": 0})
    (chosen / "Bob").mkdir()  # the same account by its name: eM Client names the folder after it
    settings = tmp_path / "settings.xml"
    settings.write_text(settings_xml([work], []).replace("<AccountName>bob@example.com</AccountName>", "<AccountName>Bob</AccountName>"), "utf-8")
    backup = write_backup(tmp_path, [])
    add_store(backup, tmp_path / "store", "p1", POP3_MARKS)
    assert len(export_layout(chosen, read_settings(settings).accounts).stores) == 2
    import_all(engine, chosen, settings, backup, tags=False)
    again = import_all(engine, chosen, settings, backup, accounts=False, mail=False)
    assert again.messages_tagged == 1


def test_tags_that_could_not_be_given_reach_a_pop3_accounts_own_mailbox_later(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path)
    real = commands_emclient.Mailbox.set_tags

    def refused(self: object, reference: object, tags: object) -> None:
        raise UsageError("the tags file could not be written")

    monkeypatch.setattr(commands_emclient.Mailbox, "set_tags", refused)
    first = import_all(engine, chosen, settings, backup)
    assert (first.messages_imported, first.messages_failed) == (4, ())
    monkeypatch.setattr(commands_emclient.Mailbox, "set_tags", real)
    later = import_all(engine, chosen, settings, backup, accounts=False, mail=False)
    assert later.messages_tagged >= 1
    assert flags_and_tags(engine, bob_id(engine))["Message 3"][1] == ("Work",)


def test_a_tag_that_fails_on_mail_already_here_is_no_failed_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path)
    import_all(engine, chosen, settings, backup, tags=False)

    def refused(self: object, reference: object, tags: object) -> None:
        raise UsageError("the tags file could not be written")

    monkeypatch.setattr(commands_emclient.Mailbox, "set_tags", refused)
    def nothing_present(*arguments: object) -> tuple[int, int]:
        return 0, 0

    monkeypatch.setattr(commands_emclient, "_tag_present_mail", nothing_present)  # only the mail step's own tagging
    again = run_import(engine, ImportParameters(str(settings), str(chosen), str(backup), accounts=False, rules=False, contacts=False, tags=True, calendars=False, again=True))
    assert (again.messages_duplicate, again.messages_failed) == (4, ())


def test_the_bad_store_line_comes_again_when_the_run_that_said_it_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path / "profile")
    chosen, settings, backup = pop3_exports(tmp_path, damaged=True)
    real = commands_emclient._import_mail

    def broken(*arguments: object) -> object:
        raise UsageError("the disk is full")

    monkeypatch.setattr(commands_emclient, "_import_mail", broken)
    with pytest.raises(UsageError):
        import_all(engine, chosen, settings, backup)
    monkeypatch.setattr(commands_emclient, "_import_mail", real)
    result = import_all(engine, chosen, settings, backup)
    assert sum("could not be read from the backup" in note for note in result.notes) == 1
