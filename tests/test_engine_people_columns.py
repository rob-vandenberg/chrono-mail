import sqlite3
from collections.abc import Generator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID, MessageRef
from chrono_mail.engine import Engine
from chrono_mail.engine.changes import MessageRow
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.index import Index, entry_from_raw
from chrono_mail.index.model import DateBounds, SortField
from chrono_mail.index.schema import UPGRADE_FROM_13
from chrono_mail.mime import delivery_addresses
from chrono_mail.store import ReceivedBy
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import add_account, call
from tests.test_engine_restore import _backup, _restore

# --- Version ------------------------------------------------------------
__version__ = "test_engine_people_columns.py 4.2.418"

# --- Version history ----------------------------------------------------
# v4.2.418: the account is recorded once and kept through copies, moves, a move to another account and a rebuild; a sent message records its
#           account, a draft nothing; the upgrade from format 14 records every message
# v4.2.417: Initial: the people columns (D-291) and the account that received each message (D-292)

TO_ALL_THREE: bytes = raw_message(
    subject="To all three", sender="Zoe Zimmer <zoe@example.org>", to="Ann <a@example.com>, b@example.com", cc="c@example.com", message_id="<all@x>"
)


def test_an_entry_has_each_persons_name_or_address_and_the_delivery_addresses() -> None:
    raw = raw_message(sender="b@example.org", to="Ann <a@example.com>, plain@example.com", cc="\"Doe, John\" <john@example.com>")
    raw = raw.replace(b"Subject:", b"Delivered-To: Rob@Example.com\r\nX-Original-To: alias@example.com\r\nSubject:", 1)
    entry = entry_from_raw(MessageRef("a" * 32, ("Inbox",), "1" * 32), "new/x", frozenset(), len(raw), raw)
    assert entry.recipient_names == "Ann, plain@example.com, Doe, John"  # no name: the address (the owner's choice)
    assert entry.recipient_addresses == "a@example.com, plain@example.com, john@example.com"
    assert entry.sender_address == "b@example.org" and entry.delivered_to == ("rob@example.com", "alias@example.com")
    assert delivery_addresses(b"\xff\xfe") == () and delivery_addresses(b"Delivered-To: <>\r\n\r\n") == ()  # never raises, nothing empty


def test_the_index_sorts_and_groups_by_what_each_people_column_shows(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.account_names = {"id-b": "Bea", "id-a": "Ann's mail"}
    people = (("Carl <z@example.org>", "z", "id-b"), ("x@example.org", "y", "id-a"), ("Abe <m@example.org>", "x", ""))
    for number, (sender, to, account) in enumerate(people, 1):
        raw = raw_message(sender=sender, to=f"{to}@example.net", message_id=f"<p{number}@x>")
        entry = entry_from_raw(MessageRef("a" * 32, ("Inbox",), f"{number:032d}"), "new/x", frozenset(), len(raw), raw)
        index.add(replace(entry, received_by=account))

    def order(sort: SortField) -> list[str]:
        return [message.subject for message in index.search("", ("Inbox",), sort=sort, descending=False)]

    rows = {message.store_id: message for message in index.search("", ("Inbox",))}
    assert rows[f"{1:032d}"].sender_address == "z@example.org" and rows[f"{1:032d}"].received_by == "Bea" and rows[f"{3:032d}"].received_by == ""
    first, second, third = (rows[f"{number:032d}"].subject for number in (1, 2, 3))
    assert order(SortField.SENDER_FULL) == [third, first, second]  # Abe <, Carl <, x@
    assert order(SortField.SENDER_ADDRESS) == [third, second, first]  # m@, x@, z@
    assert order(SortField.RECIPIENT_ADDRESSES) == order(SortField.RECIPIENT_NAMES) == [third, second, first]  # x@, y@, z@
    assert order(SortField.ACCOUNT) == [third, second, first]  # "", Ann's mail, Bea: by the name shown, not the id
    today = datetime(2026, 10, 29, tzinfo=UTC)  # the date bounds are not used by these sorts
    bounds = DateBounds(today, today, today, today, today, today)
    assert [group.value for group in index.groups(bounds, "", ("Inbox",), sort=SortField.ACCOUNT, descending=False)] == ["", "Ann's mail", "Bea"]
    assert [group.value for group in index.groups(bounds, "", ("Inbox",), sort=SortField.RECIPIENT_NAMES, descending=True)] == ["z@example.net", "y@example.net", "x@example.net"]
    index.close()


@dataclass
class Three:
    engine: Engine
    a: str
    b: str
    c: str
    smtp: FakeSmtpServer


@pytest.fixture
def three(tmp_path: Path) -> Generator[Three]:
    """The owner's example: three accounts of one person in Local Folders, each sent the same message (D-292)."""
    engine = make_engine(tmp_path)
    with FakePop3Server({"m": TO_ALL_THREE}) as pop_a, FakePop3Server({"m": TO_ALL_THREE}) as pop_b, FakePop3Server({"m": TO_ALL_THREE}) as pop_c, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com", storage="local")
        b = add_account(engine, pop_b, smtp, email="b@example.com", storage="local")
        c = add_account(engine, pop_c, smtp, email="c@example.com", storage="local", account_name="Club")
        for account in (a, b, c):
            assert call(engine, "mail.fetch", account=account).downloaded == 1
        yield Three(engine, a, b, c, smtp)


def _accounts(engine: Engine, folder: str = "Inbox", sort: str = "account") -> list[str]:
    rows: list[MessageRow] = list(call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder=folder, sort=sort, oldest_first=True).messages)
    return [row.received_by for row in rows]


def test_each_copy_in_local_folders_shows_the_account_that_downloaded_it(three: Three) -> None:
    assert _accounts(three.engine) == ["a@example.com", "b@example.com", "Club"]  # the account's name when it has one, by the name
    with open_mailbox(three.engine, LOCAL_ACCOUNT_ID) as mailbox:  # mail no account here received (an import): nothing to show
        mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject="Imported", to="old@example.net", message_id="<old@x>"), frozenset())
        mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject="Bcc", to="list@example.net", message_id="<bcc@x>").replace(b"Subject:", b"Delivered-To: c@example.com\r\nSubject:", 1), frozenset())
    assert sorted(_accounts(three.engine)) == ["", "Club", "Club", "a@example.com", "b@example.com"]  # the delivery line names C
    assert call(three.engine, "mail.send", account=three.b, to="you@example.org", subject="From B", body="hi").sent
    assert _accounts(three.engine, "Sent") == ["b@example.com"]  # sent from B: B's


def test_a_copy_keeps_the_account_that_received_the_original(three: Three) -> None:
    rows: list[MessageRow] = list(call(three.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox", sort="account").messages)
    of_b = next(row for row in rows if row.received_by == "b@example.com")
    call(three.engine, "message.copy", account=LOCAL_ACCOUNT_ID, folder="Inbox", message=of_b.message_id, target="Junk")
    assert _accounts(three.engine, "Junk") == ["b@example.com"]  # not A, the first address the message names


def test_an_index_of_format_13_is_filled_in_with_the_people_and_the_accounts(three: Three) -> None:
    path = three.engine.directories.index_file(LOCAL_ACCOUNT_ID)
    connection = sqlite3.connect(path)
    for statement in UPGRADE_FROM_13:
        if statement.startswith("CREATE INDEX"):
            connection.execute(f"DROP INDEX {statement.split()[2]}")
    for statement in UPGRADE_FROM_13:
        if statement.startswith("ALTER TABLE"):
            connection.execute(f"ALTER TABLE messages DROP COLUMN {statement.split()[5]}")
    connection.execute("UPDATE meta SET value = '13' WHERE key = 'schema'")
    connection.commit()
    connection.close()
    assert {state.need for state in call(three.engine, "index.check")} == {"fill"}  # usable at once, filled in afterwards (D-229)
    assert _accounts(three.engine) == ["", "", ""]
    assert call(three.engine, "index.fill-sent", account=LOCAL_ACCOUNT_ID).filled == 3
    assert _accounts(three.engine) == ["a@example.com", "b@example.com", "Club"]  # by the download records, as at the download
    row: MessageRow = call(three.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages[0]
    assert (row.recipient_names, row.recipient_addresses, row.sender_address) == ("Ann, b@example.com, c@example.com", "a@example.com, b@example.com, c@example.com", "zoe@example.org")
    assert [state.need for state in call(three.engine, "index.check")] == [""]


def test_mail_forwarded_into_an_account_is_that_accounts_and_stays_so_after_a_rebuild(tmp_path: Path) -> None:
    """The owner: the account that received the message (D-292): A downloaded it, though it was sent to B; recorded, so a rebuild keeps it."""
    engine = make_engine(tmp_path)
    to_b = raw_message(subject="For B", to="b@example.com", message_id="<forb@x>")
    with FakePop3Server({"m": to_b}) as pop_a, FakePop3Server({}) as pop_b, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com")
        add_account(engine, pop_b, smtp, email="b@example.com")
        call(engine, "mail.fetch", account=a)
        assert call(engine, "message.list", account=a, folder="Inbox").messages[0].received_by == "a@example.com"
        engine.directories.index_file(a).unlink()
        assert call(engine, "message.list", account=a, folder="Inbox").messages[0].received_by == "a@example.com"  # rebuilt: the same


def test_an_account_moved_into_local_folders_keeps_who_downloaded_its_mail(tmp_path: Path) -> None:
    """The reviewer's case: list mail names none of the accounts; once moved into Local Folders it is still the account that downloaded it."""
    engine = make_engine(tmp_path)
    to_list = raw_message(subject="List", to="list@example.net", message_id="<list@x>")
    with FakePop3Server({"m": to_list}) as pop_a, FakePop3Server({}) as pop_b, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com")
        add_account(engine, pop_b, smtp, email="b@example.com", storage="local")
        call(engine, "mail.fetch", account=a)
        call(engine, "account.set-storage", account=a, storage="local")
        assert _accounts(engine) == ["a@example.com"]


def _receiver_recorded(engine: Engine, store_id: str) -> str | None:
    with open_mailbox(engine, LOCAL_ACCOUNT_ID) as mailbox:
        return mailbox.store.received_by.of(store_id)


def test_the_account_is_recorded_once_and_a_copy_keeps_it_through_a_rebuild(three: Three) -> None:
    """The owner: the account that received a message is recorded and never changes, whatever happens to the message (D-292)."""
    rows: list[MessageRow] = list(call(three.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox", sort="account", oldest_first=True).messages)
    assert [_receiver_recorded(three.engine, row.message_id) for row in rows] == [three.a, three.b, three.c]
    call(three.engine, "message.copy", account=LOCAL_ACCOUNT_ID, folder="Inbox", message=rows[2].message_id, target="Junk")
    call(three.engine, "message.move", account=LOCAL_ACCOUNT_ID, folder="Inbox", message=rows[1].message_id, target="Trash")
    three.engine.directories.index_file(LOCAL_ACCOUNT_ID).unlink()  # rebuilt from the store: the records, not the rule
    assert _accounts(three.engine, "Junk") == ["Club"] and _accounts(three.engine, "Trash") == ["b@example.com"]
    assert _accounts(three.engine) == ["a@example.com", "Club"]


def test_a_record_is_never_written_over_and_a_damaged_line_stops_nothing(tmp_path: Path) -> None:
    record = ReceivedBy(tmp_path)
    assert record.record("1" * 32, "a" * 32) == "a" * 32
    assert record.record("1" * 32, "b" * 32) == "a" * 32 and record.of("1" * 32) == "a" * 32
    record.copy("1" * 32, "2" * 32)
    assert record.of("2" * 32) == "a" * 32
    with record.path.open("ab") as file:
        file.write(b"R\t" + b"3" * 32 + b"\tnot an account\nR\t" + b"1" * 32 + b"\t" + b"c" * 32 + b"\nR\t" + b"4" * 16)  # damaged, a second line, cut off
    assert ReceivedBy(tmp_path).all() == {"1" * 32: "a" * 32, "2" * 32: "a" * 32}  # the first line of a message counts
    assert record.record("5" * 32, "b" * 32) == "b" * 32 and record.of("5" * 32) == "b" * 32  # the cut-off line stays a line of its own


def test_a_sent_message_records_the_account_it_was_sent_from_and_a_draft_records_nothing(three: Three) -> None:
    assert call(three.engine, "mail.send", account=three.c, to="you@example.org", subject="From C", body="hi").sent
    (sent,) = call(three.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Sent").messages
    assert _receiver_recorded(three.engine, sent.message_id) == three.c and sent.received_by == "Club"
    draft = call(three.engine, "draft.save", account=three.b, to="you@example.org", subject="Later", body="soon")
    (row,) = call(three.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Drafts").messages
    assert row.received_by == "b@example.com" and _receiver_recorded(three.engine, row.message_id) is None and draft is not None  # its From can still change


def test_a_message_moved_to_another_account_keeps_who_received_it(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({"m": TO_ALL_THREE}) as pop_a, FakePop3Server({}) as pop_b, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com")
        b = add_account(engine, pop_b, smtp, email="b@example.com")
        call(engine, "mail.fetch", account=a)
        (row,) = call(engine, "message.list", account=a, folder="Inbox").messages
        call(engine, "message.move-across", account=a, folder="Inbox", messages=(row.message_id,), target_account=b, target="Inbox")
        assert [moved.received_by for moved in call(engine, "message.list", account=b, folder="Inbox").messages] == ["a@example.com"]
        engine.directories.index_file(b).unlink()
        assert [moved.received_by for moved in call(engine, "message.list", account=b, folder="Inbox").messages] == ["a@example.com"]


def test_the_upgrade_from_format_14_records_the_account_of_every_message_once(three: Three) -> None:
    """Mail kept before 4.2.418 has no record: the filling in after the upgrade writes it, by the download records (D-292)."""
    with open_mailbox(three.engine, LOCAL_ACCOUNT_ID) as mailbox:
        mailbox.store.received_by.path.unlink()  # as 4.2.417 left it: no records
    connection = sqlite3.connect(three.engine.directories.index_file(LOCAL_ACCOUNT_ID))
    connection.execute("UPDATE meta SET value = '14' WHERE key = 'schema'")
    connection.commit()
    connection.close()
    assert {state.need for state in call(three.engine, "index.check")} == {"fill"}  # by itself at the next start, in the background (D-229)
    assert call(three.engine, "index.fill-sent", account=LOCAL_ACCOUNT_ID).filled == 3
    rows: list[MessageRow] = list(call(three.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox", sort="account", oldest_first=True).messages)
    assert [_receiver_recorded(three.engine, row.message_id) for row in rows] == [three.a, three.b, three.c]


def test_a_message_restored_into_an_account_here_keeps_who_received_it(three: Three, tmp_path: Path) -> None:
    password = _backup(three.engine, tmp_path / "all.cmz")
    rows: list[MessageRow] = list(call(three.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox", sort="account", oldest_first=True).messages)
    with open_mailbox(three.engine, LOCAL_ACCOUNT_ID) as mailbox:
        for row in rows:  # the three copies share one Message-ID: one left here would count as the others
            mailbox.delete(mailbox.reference(("Inbox",), row.message_id), permanent=True)
        mailbox.store.received_by.path.unlink()  # nothing here tells it any more: not the record,
    for account in (three.a, three.b, three.c):
        three.engine.directories.pop3_state_file(account).unlink()  # nor what each account downloaded
    assert _restore(three.engine, tmp_path / "all.cmz", password).messages == 3
    assert [_receiver_recorded(three.engine, row.message_id) for row in rows] == [three.a, three.b, three.c]  # as the backup recorded them


def test_a_draft_dragged_out_of_drafts_keeps_the_account_it_showed(three: Three) -> None:
    call(three.engine, "draft.save", account=three.c, to="a@example.com", subject="Never sent", body="later")  # from C, to A
    (draft,) = call(three.engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Drafts").messages
    assert draft.received_by == "Club"
    call(three.engine, "message.move", account=LOCAL_ACCOUNT_ID, folder="Drafts", message=draft.message_id, target="Junk")
    assert _receiver_recorded(three.engine, draft.message_id) == three.c
    three.engine.directories.index_file(LOCAL_ACCOUNT_ID).unlink()
    assert _accounts(three.engine, "Junk") == ["Club"]  # rebuilt: still C, not A whom it was written to


def test_a_restore_on_a_new_computer_names_the_account_by_its_id_there(tmp_path: Path) -> None:
    """The reviewer's case: the account was added again on the new computer (another id) before the backup was restored."""
    old = make_engine(tmp_path / "old")
    with FakePop3Server({"m": TO_ALL_THREE}) as pop, FakeSmtpServer() as smtp:
        before = add_account(old, pop, smtp, email="a@example.com")
        call(old, "mail.fetch", account=before)
        password = _backup(old, tmp_path / "all.cmz")
        new = make_engine(tmp_path / "new")
        after = add_account(new, pop, smtp, email="a@example.com")  # the same login on the same server: the same account (D-081)
    assert after != before
    _restore(new, tmp_path / "all.cmz", password)
    (row,) = call(new, "message.list", account=after, folder="Inbox").messages
    with open_mailbox(new, after) as mailbox:
        assert mailbox.store.received_by.of(row.message_id) == after and row.received_by == "a@example.com"  # never the old id


def test_records_of_an_account_that_did_not_come_back_are_taken_out(tmp_path: Path) -> None:
    record = ReceivedBy(tmp_path)
    record.record_many([("1" * 32, "a" * 32), ("2" * 32, "b" * 32)])
    record.leave_out({"b" * 32})
    assert ReceivedBy(tmp_path).all() == {"1" * 32: "a" * 32}  # never a record naming an account that is not here
    assert record.record("2" * 32, "a" * 32) == "a" * 32  # that message is decided again by the rule
