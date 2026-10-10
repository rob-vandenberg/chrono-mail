import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chrono_mail.core import MessageFlag, MessageRef, StorageError
from chrono_mail.index import Index, IndexedMessage, IndexEntry, SearchFilter, SortField, entry_from_raw, match_expression
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_index_index.py 0.0.8"

# --- Version history ----------------------------------------------------
# v0.0.8: Initial index tests

INBOX = ("Inbox",)
ARCHIVE = ("Archive",)
BASE = datetime(2026, 1, 1, tzinfo=UTC)
SEEN = frozenset({MessageFlag.SEEN})


def make_entry(number: int, folder: tuple[str, ...] = INBOX, flags: frozenset[MessageFlag] = frozenset(), **fields: str) -> IndexEntry:
    store_id = f"{number:032x}"
    raw = raw_message(
        subject=fields.get("subject", f"Subject number {number}"),
        sender=fields.get("sender", f"Sender {number % 7} <s{number % 7}@example.com>"),
        to=fields.get("to", "bob@example.com"),
        body=fields.get("body", f"Body of message {number}\n"),
        date=BASE + timedelta(days=number),
        message_id=f"<m{number}@example.com>",
        attachment=b"x" if fields.get("attachment") else None,
    )
    return entry_from_raw(MessageRef("account1", folder, store_id), f"new/{store_id}", flags, len(raw), raw)


def subjects(messages: list[IndexedMessage]) -> list[str]:
    return [message.subject for message in messages]


def filled(tmp_path: Path, count: int = 30) -> Index:
    index = Index(tmp_path / "index.sqlite")
    index.add_many(make_entry(number, flags=SEEN if number % 2 else frozenset()) for number in range(count))
    return index


def test_new_index_is_empty(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    assert index.message_count() == 0 and index.count(INBOX) == 0 and index.page(INBOX) == []
    assert index.check()


def test_add_and_get(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add(make_entry(1))
    message = index.get(f"{1:032x}")
    assert message is not None
    assert message.subject == "Subject number 1" and message.folder == INBOX
    assert message.date == BASE + timedelta(days=1)
    assert index.get("f" * 32) is None


def test_adding_the_same_message_again_replaces_it(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add(make_entry(1, subject="old words"))
    index.add(make_entry(1, subject="new words"))
    assert index.message_count() == 1
    assert subjects(index.search("old")) == [] and subjects(index.search("new")) == ["new words"]


def test_page_orders_and_paginates(tmp_path: Path) -> None:
    index = filled(tmp_path)
    newest = index.page(INBOX, limit=5)
    assert subjects(newest) == [f"Subject number {number}" for number in (29, 28, 27, 26, 25)]
    oldest = index.page(INBOX, descending=False, limit=3)
    assert subjects(oldest) == [f"Subject number {number}" for number in (0, 1, 2)]
    assert subjects(index.page(INBOX, offset=5, limit=2)) == ["Subject number 24", "Subject number 23"]
    everything = [message for start in range(0, 30, 7) for message in index.page(INBOX, offset=start, limit=7)]
    assert len(everything) == 30 == len({message.store_id for message in everything})
    assert index.page(INBOX, offset=100) == []


@pytest.mark.parametrize("sort", list(SortField))
def test_every_sort_order_works_both_ways(tmp_path: Path, sort: SortField) -> None:
    index = filled(tmp_path)
    ascending = index.page(INBOX, sort=sort, descending=False, limit=100)
    descending = index.page(INBOX, sort=sort, descending=True, limit=100)
    assert len(ascending) == len(descending) == 30
    assert {m.store_id for m in ascending} == {m.store_id for m in descending}


def test_sort_by_subject_ignores_case(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    for number, subject in enumerate(["banana", "Apple", "cherry", "apple pie"]):
        index.add(make_entry(number, subject=subject))
    assert subjects(index.page(INBOX, sort=SortField.SUBJECT, descending=False)) == ["Apple", "apple pie", "banana", "cherry"]


def test_counts_and_unread(tmp_path: Path) -> None:
    index = filled(tmp_path)
    assert index.count(INBOX) == 30 and index.count(INBOX, unread_only=True) == 15
    assert all(MessageFlag.SEEN not in message.flags for message in index.page(INBOX, unread_only=True, limit=100))
    assert index.count(ARCHIVE) == 0


def test_update_flags_move_remove(tmp_path: Path) -> None:
    index = filled(tmp_path, 5)
    index.update_flags(f"{0:032x}", frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED}), f"cur/{0:032x}!2,FS")
    message = index.get(f"{0:032x}")
    assert message is not None and message.flags == {MessageFlag.SEEN, MessageFlag.FLAGGED} and message.path.endswith("!2,FS")
    index.move(f"{0:032x}", ARCHIVE, f"cur/{0:032x}!2,FS")
    assert index.count(INBOX) == 4 and index.count(ARCHIVE) == 1
    index.remove(f"{0:032x}")
    index.remove(f"{0:032x}")
    assert index.count(ARCHIVE) == 0 and index.get(f"{0:032x}") is None and index.search("number") != []


def test_rename_folder_renames_everything_below(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add(make_entry(1, folder=("A",)))
    index.add(make_entry(2, folder=("A", "B")))
    index.add(make_entry(3, folder=("AB",)))
    index.rename_folder(("A",), ("X", "Y"))
    assert index.count(("X", "Y")) == 1 and index.count(("X", "Y", "B")) == 1 and index.count(("AB",)) == 1 and index.count(("A",)) == 0


def test_remove_folder_and_snapshot(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add(make_entry(1))
    index.add(make_entry(2, folder=ARCHIVE, subject="archived words"))
    index.add(make_entry(3, folder=ARCHIVE))
    assert index.snapshot() == {f"{n:032x}": (folder, f"new/{n:032x}") for n, folder in ((1, INBOX), (2, ARCHIVE), (3, ARCHIVE))}
    assert index.remove_folder(ARCHIVE) == 2 and index.remove_folder(ARCHIVE) == 0
    assert index.message_count() == 1 and index.search("archived") == []  # gone from the search too
    assert index.snapshot() == {f"{1:032x}": (INBOX, f"new/{1:032x}")}


def test_search_words_prefixes_and_diacritics(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add(make_entry(1, subject="Café crème", body="Héllo wörld\n"))
    index.add(make_entry(2, subject="Invoice 2026", body="payment overdue\n"))
    index.add(make_entry(3, subject="Other", body="nothing to see\n"))
    assert subjects(index.search("cafe")) == ["Café crème"]
    assert subjects(index.search("CRÈME")) == ["Café crème"]
    assert subjects(index.search("hello world")) == ["Café crème"]
    assert subjects(index.search("inv")) == ["Invoice 2026"]  # prefix
    assert subjects(index.search("payment other")) == []  # all words must match
    assert subjects(index.search("overdue")) == ["Invoice 2026"]  # body text


def test_search_phrases_and_fields(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add(make_entry(1, subject="Plan", sender="Alice <alice@example.com>", to="bob@example.com", body="the big plan\n"))
    index.add(make_entry(2, subject="Big", sender="Bob <bob@example.com>", to="alice@example.com", body="plan big\n"))
    assert subjects(index.search('"big plan"')) == ["Plan"]
    assert subjects(index.search("from:alice")) == ["Plan"]
    assert subjects(index.search("to:alice")) == ["Big"]
    assert subjects(index.search("subject:big")) == ["Big"]
    assert subjects(index.search("body:plan from:bob")) == ["Big"]
    assert subjects(index.search("re:something")) == []  # unknown prefix is plain text


def test_search_in_one_folder_and_with_filters(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add(make_entry(1, subject="alpha report", flags=SEEN))
    index.add(make_entry(2, subject="alpha memo", folder=ARCHIVE, attachment="yes"))
    index.add(make_entry(3, subject="alpha note", flags=frozenset({MessageFlag.FLAGGED})))
    assert len(index.search("alpha")) == 3
    assert subjects(index.search("alpha", folder=ARCHIVE)) == ["alpha memo"]
    assert subjects(index.search("alpha", filters=SearchFilter(unread=False))) == ["alpha report"]
    assert subjects(index.search("alpha", filters=SearchFilter(flagged=True))) == ["alpha note"]
    assert subjects(index.search("alpha", filters=SearchFilter(has_attachments=True))) == ["alpha memo"]
    assert subjects(index.search("", filters=SearchFilter(date_from=BASE + timedelta(days=2)))) == ["alpha note", "alpha memo"]
    assert subjects(index.search("", filters=SearchFilter(date_to=BASE + timedelta(days=1)))) == ["alpha report"]
    assert len(index.search("", filters=SearchFilter(minimum_size=1))) == 3
    assert index.search("", filters=SearchFilter(maximum_size=1)) == []
    assert len(index.search("")) == 3


def test_text_search_with_a_folder_looks_up_the_words_first(tmp_path: Path) -> None:
    # Regression: with 300,000 hits, "m.folder = ?" let SQLite walk the folder's date index and probe the
    # full-text table once per row: 4 seconds instead of 0.1 second. The plan must start from the words.
    index = filled(tmp_path, 200)
    statements: list[str] = []
    index._connection.set_trace_callback(statements.append)  # noqa: SLF001
    for filters in (SearchFilter(), SearchFilter(date_from=BASE), SearchFilter(date_to=BASE + timedelta(days=500))):
        statements.clear()
        index.search("number", folder=INBOX, filters=filters)
        (select,) = [statement for statement in statements if statement.startswith("SELECT")]
        plan = " | ".join(str(row[3]) for row in index._connection.execute("EXPLAIN QUERY PLAN " + select))  # noqa: SLF001
        assert "VIRTUAL TABLE INDEX" in plan and "SCAN t" in plan, plan
        assert "messages_by_date" not in plan and "messages_by_subject" not in plan, plan


def test_search_results_are_newest_first_and_paginate(tmp_path: Path) -> None:
    index = filled(tmp_path)
    first = index.search("number", limit=10)
    second = index.search("number", limit=10, offset=10)
    assert [m.store_id for m in first] == [f"{n:032x}" for n in range(29, 19, -1)]
    assert not {m.store_id for m in first} & {m.store_id for m in second}


@pytest.mark.parametrize(
    "query",
    ['"', '""', '"""', "AND", "OR", "NOT", "a AND", "(", ")", "((a)", "*", "a*", "NEAR(a b)", "subject:", ":", "::", "from:from:from:", "-a", "a -b", "^a", "a^", "{a}", "\\", "'", "';DROP TABLE messages;--", "\x00", "a\x00b", "é☕", "x" * 10000, " ".join(["a"] * 2000), "title:body:subject:x", "col : a", "rank", "text:a", "a OR b NOT c"],
)
def test_hostile_queries_never_crash_or_change_anything(tmp_path: Path, query: str) -> None:
    index = filled(tmp_path, 5)
    try:
        result = index.search(query)
    except StorageError:
        result: list[IndexedMessage] = []
    assert isinstance(result, list)
    assert index.message_count() == 5


def test_match_expression_quotes_everything() -> None:
    assert match_expression("") == ""
    assert match_expression("hello") == '"hello"*'
    assert match_expression('say "hi there"') == '"say"* "hi there"'
    assert match_expression('a"b') == '"a""b"*'
    assert match_expression("from:alice") == 'sender : "alice"*'


def test_thread_members(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    for number, references in enumerate([None, "<m0@example.com>", "<m0@example.com> <m1@example.com>"]):
        raw = raw_message(subject=f"Re {number}", message_id=f"<m{number}@example.com>", references=references, date=BASE + timedelta(days=number))
        reference = MessageRef("account1", INBOX, f"{number:032x}")
        index.add(entry_from_raw(reference, "new/x", frozenset(), len(raw), raw))
    members = index.thread_members("<m0@example.com>")
    assert subjects(members) == ["Re 0", "Re 1", "Re 2"]


def test_index_survives_reopening(tmp_path: Path) -> None:
    filled(tmp_path, 10).close()
    reopened = Index(tmp_path / "index.sqlite")
    assert reopened.message_count() == 10 and len(reopened.search("body")) == 10


def test_damaged_index_is_reported_not_trusted(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    filled(tmp_path, 5).close()
    path.write_bytes(b"this is not a database" * 100)
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    with pytest.raises(StorageError, match="rebuild"):
        Index(path)


def test_unknown_schema_version_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    filled(tmp_path, 1).close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE meta SET value = '99' WHERE key = 'schema'")
    connection.commit()
    connection.close()
    with pytest.raises(StorageError, match="rebuild"):
        Index(path)


def test_failed_batch_leaves_nothing_behind(tmp_path: Path) -> None:
    index = filled(tmp_path, 3)

    def failing() -> Iterator[IndexEntry]:
        yield make_entry(100)
        yield make_entry(101)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        index.add_many(failing())
    assert index.message_count() == 3  # the two entries added before the failure were rolled back
    assert index.get(f"{100:032x}") is None
    assert index.search("number") != []  # and the index is still usable
