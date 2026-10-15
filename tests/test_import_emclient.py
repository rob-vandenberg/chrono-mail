from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any  # command results are command-specific dataclasses

import pytest

from chrono_mail.config import ServerRole, load_rules
from chrono_mail.core import LOCAL_ACCOUNT_ID, MessageFlag
from chrono_mail.engine import Engine, UsageError
from chrono_mail.engine.commands_emclient import JOURNAL_FILE
from chrono_mail.engine.mailbox import open_mailbox
from tests.enginefactory import make_engine
from tests.mailfactory import raw_message
from tests.test_emclient_source import FakeAccount, FakeContact, FakeItem, rule_xml, ui_rule, words, write_backup, write_mail_export, write_settings
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_import_emclient.py 0.5.132"

# --- Version history ----------------------------------------------------
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
