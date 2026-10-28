import dataclasses
import sqlite3
import threading
from collections.abc import Iterator
from email.utils import format_datetime
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, override

import pytest

from chrono_mail.core import MessageFlag, MessageRef, StorageError, shown_name
from chrono_mail.index import Index, IndexedMessage, IndexEntry, ParsedQuery, SearchFilter, SortField, entry_from_raw, match_expression, parse_query
from chrono_mail.index.index import IndexBuilder, _order
from chrono_mail.index.schema import FOLDER_NUMBER_STATEMENTS, SEND_AT_INDEX, STATEMENTS
from chrono_mail.index.model import YES_NO_SORTS, DateBounds
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_index_index.py 4.2.406"

# --- Version history ----------------------------------------------------
# v4.2.406: a search with subfolders finds every folder inside, at any depth, and no sibling whose name starts the same (D-287)
# v2.0.280: sent, the moment the message carries itself, kept beside Received, sorted and grouped by (the Sent column, B-16)
# v1.9.276: Every older format (6 to 10 too) is rebuilt: format 11's date is when the server received the message (D-226); the in-place upgrade tests are gone
# v1.1.250: Format 10: the numbers of every folder, kept by the database with every change, also through a rebuild and the upgrade from 9 (R-10)
# v1.1.196: Format 9: the send time of scheduled messages; format 8 is upgraded in place and waits for its send times (R-01)
# v0.5.166: sorting and grouping by sender follow the name the From column shows; format 7 is upgraded in place
# v0.5.162: messages named in stay are found although they no longer match unread, flagged or the tag
# v0.5.160: positions: where several messages stand, in one pass, as page() lists them
# v0.5.130: A failed open closes its connection (old format, unreadable format, damaged), so the rebuild can replace the files on Windows
# v0.4.117: Unread counts use the index of the Unread sort (the partial index is gone)
# v0.3.110: Changes made at the same time from several connections all succeed
# v0.3.109: Sorting by unread, flagged, replied, attachment, folder, conversation and preview
# v0.2.94: Sorting by recipients, search in any order, groups
# v0.0.23: Address completion
# v0.0.19: Advanced search: OR, NOT, dates, sizes, dialog
# v0.0.14: Changes after the first real use (see DEVLOG.md)
# v0.0.13: Changes for the user interface (see DEVLOG.md)
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


@pytest.mark.parametrize("sort", list(SortField))
def test_the_positions_of_several_messages_are_where_page_lists_them(tmp_path: Path, sort: SortField) -> None:
    index = filled(tmp_path)
    for descending in (True, False):
        listed = [message.store_id for message in index.page(INBOX, sort=sort, descending=descending, limit=100)]
        asked = listed[::3] + ["not there"]
        assert index.positions(INBOX, asked, sort, descending) == {store_id: listed.index(store_id) for store_id in listed[::3]}
    assert index.positions(INBOX, []) == {}
    assert index.positions(("Archive",), listed) == {}  # not in that folder


def test_messages_named_to_stay_are_found_although_they_no_longer_match_the_marks(tmp_path: Path) -> None:
    index = filled(tmp_path)
    unread = index.search("", INBOX, SearchFilter(unread=True), 0, 100)
    read = index.search("", INBOX, SearchFilter(unread=False), 0, 100)
    stay = read[0].store_id
    found = index.search("", INBOX, SearchFilter(unread=True, stay=frozenset({stay})), 0, 100)
    assert {message.store_id for message in found} == {message.store_id for message in unread} | {stay}
    assert len(index.search("", INBOX, SearchFilter(stay=frozenset({stay})), 0, 100)) == 30  # without a filter of marks nothing changes
    assert len(index.search("", INBOX, SearchFilter(unread=True, has_attachments=True, stay=frozenset({stay})), 0, 100)) <= len(found)  # other filters still hold


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


def test_a_search_with_subfolders_finds_every_folder_inside_and_no_sibling(tmp_path: Path) -> None:
    """D-287: This folder + subfolders looks in the folder and every folder inside it, at any depth; never in a folder whose
    name only starts the same (Archive2, "Archive 1")."""
    index = Index(tmp_path / "index.sqlite")
    folders: tuple[tuple[str, ...], ...] = (ARCHIVE, ("Archive", "2026"), ("Archive", "2026", "Q1"), ("Archive2",), ("Archive 1",), INBOX)
    for number, folder in enumerate(folders, start=1):
        index.add(make_entry(number, folder=folder, subject=f"alpha {number}"))
    inside = SearchFilter(subfolders=True)
    assert sorted(subjects(index.search("alpha", folder=ARCHIVE, filters=inside))) == ["alpha 1", "alpha 2", "alpha 3"]
    assert sorted(subjects(index.search("", folder=ARCHIVE, filters=inside))) == ["alpha 1", "alpha 2", "alpha 3"]
    assert sorted(subjects(index.search("alpha", folder=("Archive", "2026"), filters=inside))) == ["alpha 2", "alpha 3"]
    assert subjects(index.search("alpha", folder=ARCHIVE)) == ["alpha 1"]  # This folder only
    ordered = index.ordered("alpha", ARCHIVE, inside)
    assert sorted(folder for _, folder, _ in ordered) == [ARCHIVE, ("Archive", "2026"), ("Archive", "2026", "Q1")]  # each message its own folder
    assert index.tally("alpha", ARCHIVE, inside) == (3, 3)
    assert sum(group.count for group in index.groups(bounds_at(0), "alpha", ARCHIVE, inside)) == 3


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


@pytest.mark.parametrize("old", ["1", "2", "3", "6", "7", "8", "9", "10"])  # format 11 changed what date holds: every older index is rebuilt (D-226)
def test_an_older_schema_is_reported_so_that_it_gets_rebuilt(tmp_path: Path, old: str) -> None:
    path = tmp_path / "index.sqlite"
    filled(tmp_path, 1).close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE meta SET value = ? WHERE key = 'schema'", (old,))
    connection.commit()
    connection.close()
    with pytest.raises(StorageError, match="rebuild"):
        Index(path)


def test_rows_carry_the_preview(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add(make_entry(1, body="the quick brown fox\n"))
    assert index.page(INBOX)[0].preview == "the quick brown fox"
    assert index.search("quick")[0].preview == "the quick brown fox"
    assert index.get(f"{1:032x}") is not None


def test_unread_counts_seek_the_index_of_the_unread_sort(tmp_path: Path) -> None:
    # Measured with 300,000 messages in one folder: 454 ms when every message of the folder is read, 0.5 ms with a partial index (D-031); schema 6 seeks the Unread sort index instead.
    index = filled(tmp_path, 50)
    plan = " | ".join(str(row[3]) for row in index._connection.execute(  # noqa: SLF001
        "EXPLAIN QUERY PLAN SELECT count(*) FROM messages m WHERE m.folder = ? AND (m.flags & 1) = 0", ("Inbox",)))
    assert "messages_by_unread (folder=? AND <expr>=?)" in plan, plan  # only the unread entries of the folder are visited
    assert index.count(INBOX, unread_only=True) == 25


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


# --- OR and NOT in search words (0.0.19) -----------------------------------------------------------------------------------------------


def test_parse_query_understands_or_not_and_minus() -> None:
    assert parse_query("a b OR c") == ParsedQuery('"a"* "b"* OR "c"*', "")
    assert parse_query("a | b") == ParsedQuery('"a"* OR "b"*', "")
    assert parse_query("cat NOT dog") == ParsedQuery('"cat"*', '"dog"*')
    assert parse_query("cat -dog -from:bob") == ParsedQuery('"cat"*', '"dog"* OR sender : "bob"*')
    assert parse_query('-from:bob "a phrase"') == ParsedQuery('"a phrase"', 'sender : "bob"*')
    assert parse_query('x NOT "not this"') == ParsedQuery('"x"*', '"not this"')
    assert parse_query("only -negative") == ParsedQuery('"only"*', '"negative"*')
    assert parse_query("-negative") == ParsedQuery("", '"negative"*')


def test_parse_query_ignores_stray_operators_and_never_breaks() -> None:
    assert parse_query("OR") == ParsedQuery() and parse_query("NOT") == ParsedQuery() and parse_query("-") == ParsedQuery('"-"*', "")
    assert parse_query("OR a OR OR b OR") == ParsedQuery('"a"* OR "b"*', "")
    assert parse_query("a NOT OR b") == ParsedQuery('"a"* OR "b"*', "")
    assert parse_query('a OR "unfinished') == ParsedQuery('"a"* OR "unfinished"', "")
    assert parse_query("lowercase or word") == ParsedQuery('"lowercase"* "or"* "word"*', "")  # only capitals are an operator
    for hostile in ('"', '""', "'", "a ) (", "NEAR(a b)", "x:", ":y", "-from:", "*", "a AND b", "\x00", '- - -"'):
        parse_query(hostile)


def mixed(tmp_path: Path) -> Index:
    index = Index(tmp_path / "index.sqlite")
    for number, (subject, body, sender) in enumerate(
        [("Cats", "all about cats", "Ann <ann@x.org>"), ("Dogs", "all about dogs", "Bob <bob@x.org>"), ("Both", "cats and dogs together", "Ann <ann@x.org>"), ("Birds", "owls only", "Cy <cy@x.org>")]
    ):
        raw = raw_message(subject=subject, body=body + "\n", sender=sender, message_id=f"<q{number}@x>", date=BASE + timedelta(days=number))
        index.add(entry_from_raw(MessageRef("account1", INBOX, f"{number:032x}"), "new/x", frozenset(), len(raw), raw))
    return index


def test_searching_with_or_not_and_minus(tmp_path: Path) -> None:
    index = mixed(tmp_path)
    assert sorted(subjects(index.search("cats OR dogs"))) == ["Both", "Cats", "Dogs"]
    assert sorted(subjects(index.search("cats dogs"))) == ["Both"]
    assert sorted(subjects(index.search("cats NOT dogs"))) == ["Cats"]
    assert sorted(subjects(index.search("cats -dogs"))) == ["Cats"]
    assert sorted(subjects(index.search("-dogs"))) == ["Birds", "Cats"]  # only an exclusion: everything else
    assert sorted(subjects(index.search("about -from:ann"))) == ["Dogs"]
    assert sorted(subjects(index.search("from:ann OR from:cy"))) == ["Birds", "Both", "Cats"]
    assert sorted(subjects(index.search("from:ann OR from:cy -cats"))) == ["Birds"]
    assert index.search("cats NOT cats") == []
    assert sorted(subjects(index.search("-dogs", INBOX, SearchFilter(unread=True)))) == ["Birds", "Cats"]


def test_a_negative_search_inside_a_folder_and_with_a_date_range(tmp_path: Path) -> None:
    index = mixed(tmp_path)
    window = SearchFilter(date_from=BASE + timedelta(days=1), date_to=BASE + timedelta(days=3))
    assert sorted(subjects(index.search("-cats", INBOX, window))) == ["Birds", "Dogs"]
    assert index.search("-cats", ("Nowhere",)) == []


# --- the addresses seen in the mail (0.0.23) ---------------------------------------------------------------------------------------


def with_people(index: Index, number: int, sender: str, to: str, cc: str | None = None, days: int = 0) -> None:
    lines = [f"From: {sender}", f"To: {to}", f"Subject: Person {number}", f"Message-ID: <p{number}@x>", f"Date: {format_datetime(BASE + timedelta(days=days))}"]
    if cc:
        lines.append(f"Cc: {cc}")
    raw = ("\n".join(lines) + "\n\nbody\n").encode()
    index.add(entry_from_raw(MessageRef("account1", INBOX, f"{number:032x}"), "new/x", frozenset(), len(raw), raw))


def test_addresses_are_collected_and_found_by_name_or_address(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    with_people(index, 1, "Ann Archer <ann@x.org>", "me@example.com", "Bob Brown <bob@x.org>")
    with_people(index, 2, "me@example.com", "Bob Brown <bob@x.org>, carol@y.net", days=1)
    with_people(index, 3, "me@example.com", "Bob Brown <bob@x.org>", days=2)
    assert [item.address for item in index.suggest("bob")] == ["bob@x.org"]
    assert [item.address for item in index.suggest("BROWN")] == ["bob@x.org"]
    found = index.suggest("o", 10)
    assert found[0].address == "bob@x.org" and found[0].as_recipient == 3 and found[0].name == "Bob Brown"  # the one written to most comes first
    assert {item.address for item in index.suggest("x.org")} == {"ann@x.org", "bob@x.org"}
    assert index.suggest("") == [] and index.suggest("  ") == [] and index.suggest("zzz") == []
    assert [item.address for item in index.suggest("carol", 1)] == ["carol@y.net"] and index.suggest("carol")[0].name == ""


def test_word_starts_rank_before_the_middle_of_a_word(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    with_people(index, 1, "me@example.com", "Marta Lopez <marta@a.org>, Dr Tamara <t@b.org>, Smart Home <s@c.org>")
    mar = [item.address for item in index.suggest("mar")]
    assert mar[0] == "marta@a.org" and set(mar) == {"marta@a.org", "t@b.org", "s@c.org"}  # Tamara and Smart contain "mar" in the middle: they follow
    assert [item.address for item in index.suggest("tam")][0] == "t@b.org"


def test_wildcards_in_what_is_typed_match_literally(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    with_people(index, 1, "me@example.com", "a_b@x.org, axb@x.org, 100%@x.org")
    assert [item.address for item in index.suggest("a_b")] == ["a_b@x.org"]
    assert [item.address for item in index.suggest("%")] == ["100%@x.org"]
    assert index.suggest("\\") == []


def test_adding_a_message_again_does_not_count_it_twice(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    for _ in range(3):
        with_people(index, 1, "me@example.com", "Bob <bob@x.org>")
    assert index.suggest("bob")[0].as_recipient == 1


def test_the_newest_non_empty_name_is_kept_and_odd_addresses_are_skipped(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    with_people(index, 1, "me@example.com", "Old Name <bob@x.org>", days=1)
    with_people(index, 2, "me@example.com", "New Name <bob@x.org>", days=5)
    with_people(index, 3, "me@example.com", "bob@x.org", days=9)  # no name: the known one stays
    with_people(index, 4, "me@example.com", "Older Name <bob@x.org>", days=0)  # older mail does not rename
    assert index.suggest("bob")[0].name == "New Name"
    with_people(index, 5, "me@example.com", "Weird <a@b@c>, <@nodomain>, Evil <\"x y\"@z.org>")
    assert index.suggest("nodomain") == [] and index.suggest("weird") == []


def test_a_rebuild_recounts_the_addresses(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    with_people(index, 1, "me@example.com", "Bob <bob@x.org>")
    index.remove(f"{1:032x}")
    assert index.suggest("bob")[0].as_recipient == 1  # counts only grow until a rebuild
    index.close()


def bounds_at(day: int) -> DateBounds:
    """Group boundaries as if "today" were BASE + day days (Thursday 2026-01-01 + day); weeks start on Monday."""
    today = BASE + timedelta(days=day)
    this_week = today - timedelta(days=today.weekday())
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    return DateBounds(today, today - timedelta(days=1), this_week, this_week - timedelta(days=7), this_month, last_month)


def test_sort_by_recipients_and_a_search_in_any_order(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add_many(make_entry(number, to=name) for number, name in enumerate(["carol@x.org", "Alice@x.org", "bob@x.org"]))
    assert [message.recipients for message in index.page(INBOX, SortField.RECIPIENTS, descending=False)] == ["Alice@x.org", "bob@x.org", "carol@x.org"]
    found = index.search("Body", INBOX, sort=SortField.RECIPIENTS, descending=True)
    assert [message.recipients for message in found] == ["carol@x.org", "bob@x.org", "Alice@x.org"]
    assert subjects(index.search("Body", INBOX)) == ["Subject number 2", "Subject number 1", "Subject number 0"]  # newest first unless asked
    index.close()


def test_date_groups_follow_the_list_both_ways(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add_many(make_entry(number) for number in (0, 20, 25, 31, 38, 42, 43, 44))
    bounds = bounds_at(44)  # Saturday 2026-02-14: this week from Monday 9 Feb, last week from 2 Feb, this month from 1 Feb, last month January
    groups = index.groups(bounds, folder=INBOX)
    assert [(group.value, group.count) for group in groups] == [
        ("Today", 1), ("Yesterday", 1), ("Earlier this week", 1), ("Last week", 1), ("Earlier this month", 1), ("Last month", 3),
    ]
    assert [group.key for group in index.groups(bounds, folder=INBOX, descending=False)] == ["last_month", "this_month", "last_week", "this_week", "yesterday", "today"]
    old = Index(tmp_path / "old.sqlite")
    old.add_many([make_entry(1), make_entry(2)])
    assert [(group.value, group.count) for group in old.groups(bounds_at(100), folder=INBOX)] == [("Older", 2)]
    index.close()
    old.close()


def test_groups_count_what_the_list_shows_in_the_same_order(tmp_path: Path) -> None:
    index = filled(tmp_path, 30)
    for sort in SortField:
        for descending in (True, False):
            listed = index.search("", INBOX, SearchFilter(unread=True), 0, 100, sort, descending)
            groups = index.groups(bounds_at(29), "", INBOX, SearchFilter(unread=True), sort, descending)
            if sort in YES_NO_SORTS:
                assert groups == []  # the icon columns have no group headers
                continue
            assert sum(group.count for group in groups) == len(listed) == 15
            position = 0
            for group in groups:  # every group is one unbroken run of the list
                run = listed[position:position + group.count]
                if sort in (SortField.SENDER, SortField.SUBJECT, SortField.RECIPIENTS):
                    attribute = {SortField.SENDER: "sender", SortField.SUBJECT: "subject", SortField.RECIPIENTS: "recipients"}[sort]
                    values = [shown_name(message.sender) if sort is SortField.SENDER else str(getattr(message, attribute)) for message in run]  # the sender as the From column shows it
                    assert {value.lower() for value in values} == {group.key}
                position += group.count
    index.close()


def test_size_groups(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    sizes = (500, 20_000, 200_000, 2_000_000, 20_000_000, 600)
    index.add_many(dataclasses.replace(make_entry(number), size=size) for number, size in enumerate(sizes))
    groups = index.groups(bounds_at(0), folder=INBOX, sort=SortField.SIZE)
    assert [(group.value, group.count) for group in groups] == [("Huge", 1), ("Large", 1), ("Medium", 1), ("Small", 1), ("Tiny", 2)]
    assert [group.key for group in index.groups(bounds_at(0), folder=INBOX, sort=SortField.SIZE, descending=False)] == ["tiny", "small", "medium", "large", "huge"]
    index.close()


def test_text_groups_ignore_case_and_a_search_narrows_them(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add_many(make_entry(number, subject=subject) for number, subject in enumerate(["Hello", "hello", "Apples", "Zebra"]))
    groups = index.groups(bounds_at(0), folder=INBOX, sort=SortField.SUBJECT, descending=False)
    assert [(group.key, group.count) for group in groups] == [("apples", 1), ("hello", 2), ("zebra", 1)]
    assert [group.key for group in index.groups(bounds_at(0), "hello", INBOX, sort=SortField.SUBJECT)] == ["hello"]
    index.close()



def numbers(messages: list[IndexedMessage]) -> list[int]:
    return [int(message.subject.rsplit(" ", 1)[1]) for message in messages]


def test_yes_no_sorts_put_yes_first_and_the_newest_first_either_way(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    flags = {0: SEEN, 1: frozenset[MessageFlag](), 2: frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED, MessageFlag.REPLIED}), 3: frozenset({MessageFlag.FLAGGED}), 4: SEEN}
    index.add_many(make_entry(number, flags=flags[number], attachment="yes" if number in (1, 4) else "") for number in range(5))
    expected = {
        SortField.UNREAD: ([3, 1, 4, 2, 0], [4, 2, 0, 3, 1]),
        SortField.FLAGGED: ([3, 2, 4, 1, 0], [4, 1, 0, 3, 2]),
        SortField.REPLIED: ([2, 4, 3, 1, 0], [4, 3, 1, 0, 2]),
        SortField.ATTACHMENT: ([4, 1, 3, 2, 0], [3, 2, 0, 4, 1]),
    }
    for sort, (yes_first, yes_last) in expected.items():
        assert numbers(index.page(INBOX, sort, descending=True)) == yes_first
        assert numbers(index.page(INBOX, sort, descending=False)) == yes_last
        assert numbers(index.search("", INBOX, sort=sort, descending=True)) == yes_first
        assert index.groups(bounds_at(4), folder=INBOX, sort=sort) == []
    index.close()


def test_folder_conversation_and_preview_sorts_and_their_groups(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add_many([make_entry(0), make_entry(1, folder=ARCHIVE), make_entry(2), make_entry(3, folder=ARCHIVE)])
    assert numbers(index.search("", None, sort=SortField.FOLDER, descending=False)) == [3, 1, 2, 0]  # Archive first, newest first within
    assert numbers(index.search("", None, sort=SortField.FOLDER, descending=True)) == [2, 0, 3, 1]
    folders = index.groups(bounds_at(3), folder=None, sort=SortField.FOLDER, descending=False)
    assert [(group.key, group.value, group.count) for group in folders] == [("archive", "Archive", 2), ("inbox", "Inbox", 2)]
    index.close()
    talk = Index(tmp_path / "talk.sqlite")
    for number, references in ((0, None), (1, "<m0@example.com>"), (2, None)):
        raw = raw_message(subject=f"Subject number {number}", date=BASE + timedelta(days=number), message_id=f"<m{number}@example.com>", references=references, body=("banana", "Apple", "cherry")[number])
        store_id = f"{number:032x}"
        talk.add(entry_from_raw(MessageRef("account1", INBOX, store_id), f"new/{store_id}", frozenset(), len(raw), raw))
    assert numbers(talk.page(INBOX, SortField.CONVERSATION, descending=True)) == [1, 0, 2]
    assert numbers(talk.page(INBOX, SortField.CONVERSATION, descending=False)) == [2, 1, 0]
    sizes = talk.groups(bounds_at(2), folder=INBOX, sort=SortField.CONVERSATION)
    assert [(group.value, group.count) for group in sizes] == [("2", 2), ("1", 1)]
    assert numbers(talk.page(INBOX, SortField.PREVIEW, descending=False)) == [1, 0, 2]  # Apple, banana, cherry: case is ignored
    previews = talk.groups(bounds_at(2), folder=INBOX, sort=SortField.PREVIEW, descending=True)
    assert [group.value for group in previews] == ["cherry", "banana", "Apple"]
    talk.close()


@pytest.mark.parametrize("sort", [*YES_NO_SORTS, SortField.PREVIEW])
def test_the_new_sorts_of_a_folder_use_their_sqlite_index(tmp_path: Path, sort: SortField) -> None:
    index = filled(tmp_path, 5)
    for descending in (True, False):
        plan = str(index._connection.execute(f"EXPLAIN QUERY PLAN SELECT m.id FROM messages m WHERE m.folder = ? ORDER BY {_order(sort, descending)} LIMIT 5", ("Inbox",)).fetchall())  # noqa: SLF001
        assert f"messages_by_{sort.value}" in plan
    plan = str(index._connection.execute(f"EXPLAIN QUERY PLAN SELECT m.id FROM messages m WHERE m.folder = ? ORDER BY {_order(sort, True)} LIMIT 5", ("Inbox",)).fetchall())  # noqa: SLF001
    assert "TEMP B-TREE" not in plan  # yes first, newest first: read straight from the index
    index.close()


def test_changes_made_at_the_same_time_all_succeed(tmp_path: Path) -> None:
    # With a deferred BEGIN, most of 60 removals from separate connections at once failed with "database is locked".
    filled(tmp_path, 60).close()
    failures: list[str] = []

    def remove(number: int) -> None:
        index = Index(tmp_path / "index.sqlite")
        index.count(INBOX)  # the connection has read before, like a worker's
        try:
            index.remove(f"{number:032x}")
        except StorageError as error:
            failures.append(str(error))
        index.close()

    workers = [threading.Thread(target=remove, args=(number,)) for number in range(60)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    index = Index(tmp_path / "index.sqlite")
    assert failures == [] and index.message_count() == 0
    index.close()


class RecordedConnection(sqlite3.Connection):
    """A connection that records that it was closed (Linux cannot show the Windows file lock itself)."""

    closed: list[bool] = []

    @override
    def close(self) -> None:
        RecordedConnection.closed.append(True)
        super().close()


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    real = sqlite3.connect
    RecordedConnection.closed = []

    def connect(database: str | Path, isolation_level: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None = "DEFERRED", timeout: float = 5.0) -> sqlite3.Connection:
        return real(database, isolation_level=isolation_level, timeout=timeout, factory=RecordedConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)
    return RecordedConnection.closed


def _schema(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("UPDATE meta SET value = ? WHERE key = 'schema'", (value,))
    connection.commit()
    connection.close()


@pytest.mark.parametrize("value", ["4", "x"])
def test_an_old_or_unreadable_format_closes_its_connection_before_it_is_reported(tmp_path: Path, value: str, recorded: list[bool]) -> None:
    path = tmp_path / "index.sqlite"
    filled(tmp_path, 1).close()
    _schema(path, value)
    recorded.clear()
    with pytest.raises(StorageError, match="unknown format; rebuild"):
        Index(path)
    assert recorded == [True]  # else Windows cannot delete the -wal file for the rebuild (WinError 32)


def test_a_damaged_index_closes_its_connection_before_it_is_reported(tmp_path: Path, recorded: list[bool]) -> None:
    path = tmp_path / "index.sqlite"
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(StorageError, match="damaged; rebuild"):
        Index(path)
    assert recorded == [True]


def test_after_a_failed_open_the_rebuild_replaces_the_files_and_the_index_opens(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    filled(tmp_path, 3).close()
    _schema(path, "4")
    with pytest.raises(StorageError):
        Index(path)
    builder = IndexBuilder(path)
    builder.finish().close()
    index = Index(path)
    assert index.count(("Inbox",)) == 0  # the new, empty index of the current format
    index.close()


def test_sorting_and_grouping_by_sender_follow_the_name_the_from_column_shows(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add_many([
        make_entry(1, sender="Corner Shop Team <one@spam.example>"),
        make_entry(2, sender="Corner Shop Team <two@other.example>"),
        make_entry(3, sender='"Corner Shop" <three@x.example>'),  # quoted: sorts under its name, not under the quote mark
        make_entry(4, sender="Zeta <z@example.com>"),
        make_entry(5, sender="dues@example.com"),  # no name: the address is what the column shows
    ])
    groups = index.groups(bounds_at(1), folder=INBOX, sort=SortField.SENDER, descending=False)
    assert [(group.value, group.count) for group in groups] == [("Corner Shop", 1), ("Corner Shop Team", 2), ("dues@example.com", 1), ("Zeta", 1)]
    listed = index.page(INBOX, SortField.SENDER, descending=False)
    assert [message.store_id[-1] for message in listed][:3] == ["3", "1", "2"]  # the quoted name first, then both of one name together


def test_only_messages_with_a_send_time_are_scheduled_soonest_first(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add_many([dataclasses.replace(make_entry(1), send_at=900), make_entry(2), dataclasses.replace(make_entry(3, folder=("Outbox",)), send_at=300), dataclasses.replace(make_entry(4), send_at=0)])
    assert index.scheduled() == [(f"{4:032x}", INBOX, 0), (f"{3:032x}", ("Outbox",), 300), (f"{1:032x}", INBOX, 900)]
    index.close()


def truth(index: Index, folder: tuple[str, ...]) -> tuple[int, int]:
    return index.count(folder), index.count(folder, unread_only=True)


def test_the_numbers_of_a_folder_follow_every_change_of_its_messages(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add_many(make_entry(number, flags=SEEN if number % 3 == 0 else frozenset()) for number in range(1, 10))  # 3, 6, 9 read
    assert index.numbers(INBOX) == truth(index, INBOX) == (9, 6)
    index.update_flags(f"{1:032x}", SEEN, "cur/x")  # read
    index.update_flags(f"{3:032x}", frozenset({MessageFlag.FLAGGED}), "cur/y")  # unread again (and flagged)
    index.update_flags(f"{2:032x}", frozenset({MessageFlag.FLAGGED}), "cur/z")  # flagged only: unread stays
    index.move(f"{4:032x}", ARCHIVE, "new/a")
    index.move(f"{6:032x}", ARCHIVE, "new/b")
    index.remove(f"{5:032x}")
    index.add(make_entry(1, flags=frozenset()))  # the same message written again: replaced, never counted twice
    for folder in (INBOX, ARCHIVE):
        assert index.numbers(folder) == truth(index, folder)
    assert index.numbers(INBOX) == (6, 5) and index.numbers(ARCHIVE) == (2, 1)
    index.rename_folder(ARCHIVE, ("Old",))
    assert index.numbers(("Old",)) == (2, 1) and index.numbers(ARCHIVE) == (0, 0)
    index.remove_folder(INBOX)
    assert index.numbers(INBOX) == (0, 0) and index.numbers(("Nowhere",)) == (0, 0)


def test_a_rebuilt_index_has_the_numbers_too(tmp_path: Path) -> None:
    builder = IndexBuilder(tmp_path / "index.sqlite")
    builder.add_many(make_entry(number, flags=SEEN if number % 2 else frozenset()) for number in range(10))
    assert builder.index.numbers(INBOX) == (10, 5)




def received_and_sent(number: int, received: datetime, sent: datetime) -> IndexEntry:
    """A message the mail server received at one moment that its sender dated at another."""
    raw = (
        f"Received: from mx.example.org by mail.example.com; {format_datetime(received)}\r\n"
        f"Date: {format_datetime(sent)}\r\nFrom: a@example.org\r\nTo: b@example.com\r\nSubject: Message {number}\r\n"
        f"Message-ID: <r{number}@example.org>\r\n\r\nBody\r\n"
    ).encode()
    store_id = f"{number:032x}"
    return entry_from_raw(MessageRef("account1", INBOX, store_id), f"new/{store_id}", frozenset(), len(raw), raw)


def test_the_sent_moment_is_the_messages_own_date_and_sorts_apart_from_received(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    index.add_many([
        received_and_sent(1, BASE + timedelta(days=10), BASE + timedelta(days=1)),  # sent first, received last
        received_and_sent(2, BASE + timedelta(days=5), BASE + timedelta(days=4)),
    ])
    first = index.get(f"{1:032x}")
    assert first is not None and first.date == BASE + timedelta(days=10) and first.sent == BASE + timedelta(days=1)
    assert subjects(index.page(INBOX, SortField.DATE)) == ["Message 1", "Message 2"]
    assert subjects(index.page(INBOX, SortField.SENT)) == ["Message 2", "Message 1"]
    assert subjects(index.page(INBOX, SortField.SENT, descending=False)) == ["Message 1", "Message 2"]
    groups = index.groups(bounds_at(4), folder=INBOX, sort=SortField.SENT)  # by the date the senders wrote
    assert [(group.value, group.count) for group in groups] == [("Today", 1), ("Last week", 1)]  # sent Monday 5 and Friday 2 January
    index.close()
