import re
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from chrono_mail.core import MessageFlag, MessageRef
from chrono_mail.engine.changes import RebuildStep
from chrono_mail.index import Index, IndexEntry, entry_from_raw
from chrono_mail.index.model import SearchFilter, SortField
from chrono_mail.index.schema import SCHEMA_VERSION, SENT_FILL_KEY, SORT_INDEXES, UNREAD_INDEXES
from tests.index_formats import as_format_19
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_index_unread_sorts.py 4.4.562"

# --- Version history ----------------------------------------------------
# v4.4.562: the sort indexes by their format 21 names (D-320)
# v4.4.539: format 17 starts from format 19; sort indexes by the folder's id (D-314)
# v4.2.471: format 19: the unread copies are sort indexes as 19 has them (D-306)
# v4.2.470: the count of a folder's marked mail is no list question; Flagged gathers now (D-305)
# v4.2.469: Initial: an unread view reads only its unread mail through the unread sort indexes, whatever the sort, also while it keeps
#           messages read in it (D-305, D-149); format 17 is upgraded without a filling in and gets them afterwards (D-229)

# Every sort a sort index serves, and so an unread sort index too (the Unread sort has its own, the others none: OR-42 needs no number here).
INDEXED_SORTS: tuple[SortField, ...] = (
    SortField.DATE, SortField.SENT, SortField.SUBJECT, SortField.SENDER, SortField.SIZE, SortField.TO_SHOWN, SortField.FLAGGED, SortField.REPLIED,
    SortField.ATTACHMENT, SortField.PREVIEW, SortField.SENDER_FULL, SortField.SENDER_ADDRESS, SortField.RECIPIENT_NAMES, SortField.RECIPIENT_ADDRESSES,
)
UNREAD: SearchFilter = SearchFilter(unread=True)


def _entry(number: int, folder: str, seen: bool) -> IndexEntry:
    raw = raw_message(subject=f"Mail {number:02d}", sender=f"Person {250 - number} <p{number}@example.org>", message_id=f"<u{number}@x>")
    flags = frozenset({MessageFlag.SEEN}) if seen else frozenset[MessageFlag]()
    return entry_from_raw(MessageRef("a" * 32, (folder,), f"{number:032d}"), "cur/x", flags, len(raw), raw)


def _index(path: Path) -> Index:
    """Two folders, mostly read mail: every third message of the Inbox unread."""
    index = Index(path)
    for number in range(1, 31):
        index.add(_entry(number, "Inbox", seen=number % 3 != 0))
    for number in range(31, 41):
        index.add(_entry(number, "Archive", seen=True))
    return index


def _plans(index: Index, ask: list[str]) -> list[str]:
    """The query plan of every statement the index ran while asked, as SQLite reports it for that exact statement (on the index's own
    connection: a row reads its temporary table of account names)."""
    plans = [
        " ".join(str(row[3]) for row in index._connection.execute("EXPLAIN QUERY PLAN " + statement))  # noqa: SLF001
        for statement in ask
        if statement.lstrip().upper().startswith("SELECT") and "FROM messages m" in statement  # not the folder's numbers looked up first,
        and not statement.startswith(("SELECT count(*) FROM (SELECT 1 FROM messages m WHERE m.folder_id", "SELECT m.store_id FROM "))  # nor the view's unread ones (D-306)  # nor the count of its marked mail (_few_marked)
    ]
    index.close()
    return plans


@pytest.mark.parametrize("sort", INDEXED_SORTS, ids=[sort.value for sort in INDEXED_SORTS])
@pytest.mark.parametrize("kept", [False, True], ids=["nothing kept", "a read message kept"])
def test_an_unread_view_sorted_by_any_column_reads_only_its_unread_mail(tmp_path: Path, sort: SortField, kept: bool) -> None:
    # D-305: through the full sort index every message of the folder was read to find the unread ones (21 s on a spinning disk).
    index = _index(tmp_path / "index.sqlite")
    filters = replace(UNREAD, stay=frozenset({f"{1:032d}"})) if kept else UNREAD  # Mail 01 is read: kept because it was read while shown (D-149)
    ran: list[str] = []
    index._connection.set_trace_callback(ran.append)  # noqa: SLF001
    index.search("", ("Inbox",), filters, sort=sort, descending=False)
    index.ordered("", ("Inbox",), filters, sort, False)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    plans = _plans(index, ran)
    assert len(plans) == 2
    for plan in plans:
        assert "_unread (folder_id=?)" in plan or re.search(r"messages_by_unread(_ties_oldest)? \(folder_id=\? AND", plan) is not None, plan  # through an unread sort index, or the Unread one
        assert "SCAN m" not in plan, plan  # never every message
        if kept:
            assert "USING INTEGER PRIMARY KEY (rowid=?)" in plan, plan  # the outer list only by the ids found: never the folder's sort order


def test_an_unread_view_lists_its_unread_mail_and_the_kept_messages_in_order(tmp_path: Path) -> None:
    index = _index(tmp_path / "index.sqlite")
    kept = frozenset({f"{1:032d}", f"{35:032d}"})  # Mail 01 read in the Inbox; Mail 35 is in another folder and stays out
    found = [row.subject for row in index.search("", ("Inbox",), replace(UNREAD, stay=kept), sort=SortField.SUBJECT, descending=False)]
    assert found == ["Mail 01", *[f"Mail {number:02d}" for number in range(3, 31, 3)]]
    order = [store_id for store_id, _, _ in index.ordered("", ("Inbox",), replace(UNREAD, stay=kept), SortField.SENDER, False)]
    assert order == [f"{number:032d}" for number in (30, 27, 24, 21, 18, 15, 12, 9, 6, 3, 1)]  # Person 220 first, Person 249 last
    assert index.tally("", ("Inbox",), replace(UNREAD, stay=kept)) == (11, 10)
    index.close()


@pytest.mark.parametrize("marks", [SearchFilter(replied=False), SearchFilter(unread=False)], ids=["unreplied", "read"])  # flagged gathers (D-305)
def test_another_view_that_keeps_messages_still_walks_its_sort_index_only_as_far_as_asked(tmp_path: Path, marks: SearchFilter) -> None:
    # Only unread has sort indexes of its own: gathering every unreplied message of the Inbox first took 320 ms instead of 0.5 (review).
    index = _index(tmp_path / "index.sqlite")
    ran: list[str] = []
    index._connection.set_trace_callback(ran.append)  # noqa: SLF001
    index.search("", ("Inbox",), replace(marks, stay=frozenset({f"{3:032d}"})), 0, 5, SortField.DATE, True)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    plans = _plans(index, ran)
    assert len(plans) == 1 and "messages_by_date (folder_id=?)" in plans[0] and "TEMP B-TREE" not in plans[0], plans


def test_a_folder_mostly_unread_that_keeps_a_message_walks_its_sort_order_only_as_far_as_asked(tmp_path: Path) -> None:
    # Gathering every unread message first took 0.69 s a page instead of 0.001 s with 300,000 unread (review): there a walk fills a page at once.
    index = Index(tmp_path / "index.sqlite")
    for number in range(1, 201):  # 199 unread of 200: a page of the sort order holds a page of unread mail
        index.add(_entry(number, "Lists", seen=number == 1))
    ran: list[str] = []
    index._connection.set_trace_callback(ran.append)  # noqa: SLF001
    found = index.search("", ("Lists",), replace(UNREAD, stay=frozenset({f"{1:032d}"})), 0, 5, SortField.SUBJECT, False)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    assert [row.subject for row in found] == ["Mail 01", "Mail 02", "Mail 03", "Mail 04", "Mail 05"]
    plans = _plans(index, ran)
    assert len(plans) == 1 and re.search(r"messages_by_subject_ties_\w+ \(folder_id=\?\)", plans[0]) is not None and "TEMP B-TREE" not in plans[0], plans


def test_the_whole_order_and_the_count_of_a_folder_mostly_unread_that_keeps_a_message_gather_its_unread_mail(tmp_path: Path) -> None:
    # Without rows asked the walk reads the whole folder: 5,000 unread of 150,000 read every message again once one was kept (review).
    index = Index(tmp_path / "index.sqlite")
    for number in range(1, 201):
        index.add(_entry(number, "Lists", seen=number == 1))
    kept = replace(UNREAD, stay=frozenset({f"{1:032d}"}))
    ran: list[str] = []
    index._connection.set_trace_callback(ran.append)  # noqa: SLF001
    assert len(index.ordered("", ("Lists",), kept, SortField.SUBJECT, False)) == 200
    assert index.tally("", ("Lists",), kept) == (200, 199)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    plans = _plans(index, ran)
    assert len(plans) == 2 and all("USING INTEGER PRIMARY KEY (rowid=?)" in plan for plan in plans), plans


def test_a_new_index_has_every_unread_sort_index(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    assert index.sort_indexes_to_make() == []
    index.close()


UNREAD_COPIES: list[str] = [statement for statement in SORT_INDEXES if "WHERE" in statement]


def _as_format_17(path: Path) -> None:
    connection = sqlite3.connect(path)
    as_format_19(connection)
    for statement in UNREAD_INDEXES:
        connection.execute(f"DROP INDEX {statement.split()[5]}")
    connection.execute("UPDATE meta SET value = '17' WHERE key = 'schema'")
    connection.commit()
    connection.close()


def test_an_index_of_format_17_is_upgraded_at_once_and_makes_its_sort_indexes_afterwards(tmp_path: Path) -> None:
    _index(tmp_path / "index.sqlite").close()
    _as_format_17(tmp_path / "index.sqlite")
    index = Index(tmp_path / "index.sqlite")
    assert index.sent_filling() is None  # nothing to fill in from the messages
    waiting = index.sort_indexes_to_make()
    assert set(UNREAD_COPIES) <= set(waiting)
    assert len(index.search("", ("Inbox",), UNREAD, sort=SortField.SENDER)) == 10  # usable at once
    index.make_sort_index(UNREAD_COPIES[0])
    assert UNREAD_COPIES[0] not in index.sort_indexes_to_make() and len(index.sort_indexes_to_make()) == len(waiting) - 1
    with pytest.raises(ValueError):
        index.make_sort_index("DROP TABLE messages")
    index.close()
    connection = sqlite3.connect(tmp_path / "index.sqlite")
    assert connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() == (str(SCHEMA_VERSION),)
    assert connection.execute("SELECT count(*) FROM meta WHERE key = ?", (SENT_FILL_KEY,)).fetchone() == (0,)
    connection.close()


def test_after_a_filling_in_the_bar_stays_full_while_the_unread_sort_indexes_are_made(setup: Setup) -> None:
    from tests.test_engine_rebuilds import without_format_16
    assert call(setup.engine, "mail.send", account=setup.account_id, to="bea@example.org", subject="Sent", body="hi").sent
    connection = sqlite3.connect(setup.engine.directories.index_file(setup.account_id))
    without_format_16(connection)  # also without the unread sort indexes
    connection.execute("UPDATE meta SET value = '15' WHERE key = 'schema'")
    connection.commit()
    connection.close()
    steps: list[RebuildStep] = []
    setup.engine.rebuilding = steps.append
    assert call(setup.engine, "index.fill-sent", account=setup.account_id).filled >= 1
    shown = [(step.done, step.total) for step in steps if not step.ended]
    assert shown and shown == sorted(shown) and shown[-1][0] == shown[-1][1]  # it ends full and never went back
    index = Index(setup.engine.directories.index_file(setup.account_id))
    assert index.sort_indexes_to_make() == []
    index.close()


def test_the_filling_in_makes_the_unread_sort_indexes_an_upgraded_index_lacks(setup: Setup) -> None:
    assert call(setup.engine, "mail.send", account=setup.account_id, to="bea@example.org", subject="Sent", body="hi").sent
    _as_format_17(setup.engine.directories.index_file(setup.account_id))
    assert {state.need for state in call(setup.engine, "index.check")} == {"fill"}
    steps: list[RebuildStep] = []
    setup.engine.rebuilding = steps.append
    assert call(setup.engine, "index.fill-sent", account=setup.account_id).filled == 0  # no message read: only the indexes made
    shown = [(step.done, step.total) for step in steps if not step.ended]
    assert len(shown) > len(UNREAD_INDEXES) and shown[0][0] == 0 and shown == sorted(shown)  # told before each step, never backwards
    assert shown[-1][0] == shown[-1][1]  # and full before it ends
    assert {state.need for state in call(setup.engine, "index.check")} == {""}
    index = Index(setup.engine.directories.index_file(setup.account_id))
    assert index.sort_indexes_to_make() == []
    index.close()
