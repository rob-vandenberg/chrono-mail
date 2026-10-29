import sys
from pathlib import Path

import pytest

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine import receiving
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.store import ReceivedBy
from chrono_mail.store.receivedby import read_records, records_text
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message
from tests.test_engine_commands import add_account, call
from tests.test_engine_people_columns import _receiver_recorded

# --- Version ------------------------------------------------------------
__version__ = "test_engine_received_records.py 4.2.428"

# --- Version history ----------------------------------------------------
# v4.2.428: Initial: a draft has no account until it is sent (the owner); one write per download; each account's text kept once; the record
#           tidied when more than half is gone, never a line named again meanwhile; written at the latest every batch; old POP3 mail in its own
#           folders fixed by its download record; a draft moved into the Outbox sent without the program's notes (D-292)

A_ID: str = "a" * 32
B_ID: str = "b" * 32


def _ids(number: int) -> list[str]:
    return [f"{position:032x}" for position in range(1, number + 1)]


def test_each_accounts_text_is_kept_once_in_memory() -> None:
    first, second = _ids(2)
    found = read_records(records_text({first: A_ID, second: A_ID}))
    assert found[first] is found[second] is sys.intern(A_ID)


def test_the_record_is_tidied_only_when_more_than_half_is_gone(tmp_path: Path) -> None:
    record = ReceivedBy(tmp_path, durable=False)
    ids = _ids(10)
    record.record_many((store_id, A_ID) for store_id in ids)
    assert record.tidy(lambda: set(ids[:5])) == 0  # half gone: kept as it is
    assert record.tidy(lambda: set(ids[:4])) == 6 and set(record.all()) == set(ids[:4])  # more than half gone: taken out
    assert set(read_records(record.path.read_bytes())) == set(ids[:4])  # on disk too
    record.record(ids[9], B_ID)
    assert record.of(ids[9]) == B_ID  # a later line is written as before


def test_a_message_seen_by_only_one_of_the_two_looks_is_never_taken_out(tmp_path: Path) -> None:
    record = ReceivedBy(tmp_path, durable=False)
    ids = _ids(4)
    record.record_many((store_id, A_ID) for store_id in ids)
    looks = iter([{ids[0]}, {ids[0], ids[1]}])  # ids[1] moved between folders while the first look passed
    assert record.tidy(lambda: next(looks)) == 2 and set(record.all()) == {ids[0], ids[1]}


def test_a_draft_moved_to_another_account_or_copied_has_no_account_until_it_is_sent(tmp_path: Path) -> None:
    """The owner: a draft has no account wherever it goes, also copied or moved to another account from outside Drafts; sent, it gets the
    sender's account."""
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop_a, FakePop3Server({}) as pop_b, FakeSmtpServer() as smtp:
        a = add_account(engine, pop_a, smtp, email="a@example.com")
        b = add_account(engine, pop_b, smtp, email="b@example.com")
        call(engine, "draft.save", account=a, to="b@example.com", subject="Not sent", body="later")
        (draft,) = call(engine, "message.list", account=a, folder="Drafts").messages
        call(engine, "message.copy", account=a, folder="Drafts", message=draft.message_id, target="Junk")
        call(engine, "message.move", account=a, folder="Drafts", message=draft.message_id, target="Inbox")
        call(engine, "message.move-across", account=a, folder="Inbox", messages=(draft.message_id,), target_account=b, target="Inbox")
        for account in (a, b):
            with open_mailbox(engine, account) as mailbox:
                assert mailbox.store.received_by.all() == {}  # nothing fixed in either account
        assert call(engine, "mail.send", account=b, to="you@example.org", subject="Sent from B", body="hi").sent
        (sent,) = call(engine, "message.list", account=b, folder="Sent").messages
        with open_mailbox(engine, b) as mailbox:
            assert mailbox.store.received_by.all() == {sent.message_id: b}  # sent: the sender's account, fixed


def test_a_download_records_its_messages_in_one_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path)
    messages = {f"m{number}": raw_message(subject=f"Mail {number}", to="a@example.com", message_id=f"<m{number}@x>") for number in range(5)}
    writes: list[int] = []
    original = ReceivedBy._append  # noqa: SLF001 - counted, then done as always

    def counted(self: ReceivedBy, text: str) -> None:
        writes.append(text.count("\n"))
        original(self, text)

    monkeypatch.setattr(ReceivedBy, "_append", counted)
    with FakePop3Server(messages) as pop, FakeSmtpServer() as smtp:
        a = add_account(engine, pop, smtp, email="a@example.com", storage="local")
        assert call(engine, "mail.fetch", account=a).downloaded == 5
    assert writes == [5]
    assert len(set(_receiver_recorded(engine, row.message_id) for row in call(engine, "message.list", account=LOCAL_ACCOUNT_ID, folder="Inbox").messages)) == 1


def test_a_message_named_again_while_the_tidy_looks_keeps_its_line(tmp_path: Path) -> None:
    record = ReceivedBy(tmp_path, durable=False)
    ids = _ids(4)
    record.record_many((store_id, A_ID) for store_id in ids)

    def present() -> set[str]:
        record.record(ids[1], A_ID)  # moved back meanwhile: its record is asked again (it exists: nothing new is written)
        return {ids[0]}

    assert record.tidy(present) == 2 and set(record.all()) == {ids[0], ids[1]}


def test_a_held_download_writes_at_the_latest_every_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = make_engine(tmp_path)
    messages = {f"m{number}": raw_message(subject=f"Mail {number}", to="a@example.com", message_id=f"<m{number}@x>") for number in range(5)}
    writes: list[int] = []
    original = ReceivedBy._append  # noqa: SLF001 - counted, then done as always

    def counted(self: ReceivedBy, text: str) -> None:
        writes.append(text.count("\n"))
        original(self, text)

    monkeypatch.setattr(ReceivedBy, "_append", counted)
    monkeypatch.setattr(receiving, "HELD_BATCH", 2)
    with FakePop3Server(messages) as pop, FakeSmtpServer() as smtp:
        a = add_account(engine, pop, smtp, email="a@example.com", storage="local")
        assert call(engine, "mail.fetch", account=a).downloaded == 5
    assert writes == [2, 2, 1]  # a crash halfway loses at most one batch


def test_old_pop3_mail_in_its_own_folders_is_fixed_by_its_download_record(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({"m": raw_message(subject="Old", to="b@example.com", message_id="<old@x>")}) as pop, FakeSmtpServer() as smtp:
        a = add_account(engine, pop, smtp, email="a@example.com")
        call(engine, "mail.fetch", account=a)
        with open_mailbox(engine, a) as mailbox:
            mailbox.store.received_by.path.unlink()  # as before records existed
        engine.directories.index_file(a).unlink()
        (row,) = call(engine, "message.list", account=a, folder="Inbox").messages  # rebuilt
        with open_mailbox(engine, a) as mailbox:
            assert mailbox.store.received_by.all() == {row.message_id: a}  # its download record says A retrieved it


def test_a_draft_moved_into_the_outbox_is_sent_without_the_programs_notes_and_gets_the_senders_account(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    with FakePop3Server({}) as pop, FakeSmtpServer() as smtp:
        a = add_account(engine, pop, smtp, email="a@example.com")
        call(engine, "draft.save", account=a, to="you@example.org", subject="Moved", body="later")
        (draft,) = call(engine, "message.list", account=a, folder="Drafts").messages
        call(engine, "message.move", account=a, folder="Drafts", message=draft.message_id, target="Outbox")
        call(engine, "mail.send-outbox", account=a)
        (sent,) = smtp.received
        assert b"x-chrono-mail" not in sent.data.lower()
        (filed,) = call(engine, "message.list", account=a, folder="Sent").messages
        with open_mailbox(engine, a) as mailbox:
            assert mailbox.store.received_by.all() == {filed.message_id: a}


def test_an_account_a_download_holds_back_is_known_at_once_and_written_once(tmp_path: Path) -> None:
    record = ReceivedBy(tmp_path, durable=False)
    (first,) = _ids(1)
    record.hold(first, A_ID)
    assert record.of(first) == A_ID and record.all() == {}  # known at once (a move or copy meanwhile takes it along), not written yet
    record.record_many([(first, A_ID)])
    assert record.all() == {first: A_ID} and record.of(first) == A_ID


def test_nothing_is_tidied_while_a_folder_cannot_be_read(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    messages = {f"m{number}": raw_message(subject=f"Mail {number}", to="a@example.com", message_id=f"<m{number}@x>") for number in range(3)}
    with FakePop3Server(messages) as pop, FakeSmtpServer() as smtp:
        a = add_account(engine, pop, smtp, email="a@example.com")
        call(engine, "mail.fetch", account=a)
    with open_mailbox(engine, a) as mailbox:
        record = mailbox.store.received_by
        kept = dict(record.all())
        gone = {store_id: a for store_id in _ids(10)}
        record.path.write_bytes(records_text({**kept, **gone}))
        inbox = next(folder for folder in mailbox.store.list_folders() if folder.path == ("Inbox",))
    folder_files = list((engine.directories.store_directory(a)).rglob("folder.json"))
    damaged = next(path for path in folder_files if b"Inbox" in path.read_bytes())
    damaged.write_bytes(b"{not json")
    assert inbox is not None
    assert call(engine, "index.tidy-records", account=a).tidied == 0  # its mail would look gone: nothing is taken out
    with open_mailbox(engine, a) as mailbox:
        assert set(mailbox.store.received_by.all()) >= set(kept)


def test_an_account_held_back_is_forgotten_when_a_restore_leaves_its_account_out(tmp_path: Path) -> None:
    record = ReceivedBy(tmp_path, durable=False)
    first, second = _ids(2)
    record.hold(first, A_ID)
    record.hold(second, B_ID)
    record.leave_out({A_ID})
    assert record.of(first) is None and record.of(second) == B_ID
