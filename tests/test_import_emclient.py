from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import ServerRole, load_rules
from chrono_mail.core import LOCAL_ACCOUNT_ID, MessageFlag
from chrono_mail.engine import Engine, UsageError
from chrono_mail.engine import commands_emclient
from chrono_mail.engine.commands_emclient import JOURNAL_FILE, fingerprint, fingerprint_of_raw
from chrono_mail.engine.mailbox import open_mailbox
from tests.enginefactory import make_engine
from tests.mailfactory import raw_message
from tests.test_emclient_source import FakeAccount, FakeContact, FakeItem, category_xml, rule_xml, ui_rule, words, write_backup, write_mail_export, write_settings
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_import_emclient.py 2.0.287"

# --- Version history ----------------------------------------------------
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
    scan: Any = call(engine, "import.emclient-scan", settings_file=None, mail_folder=str(exports.mail), backup_file=None)
    assert (scan.messages, scan.messages_done) == (7, 3)
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

    def counted(path: Path, work: Path) -> object:
        reads.append(path)
        return original(path, work)

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
    call(engine, "import.emclient", **exports.values(accounts=False, rules=False, contacts=False, batch=3))
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
    assert seen == [(number, 7) for number in range(1, 8)]
