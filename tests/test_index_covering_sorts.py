import os
import sqlite3
from pathlib import Path

import pytest

from chrono_mail.core import MessageFlag, MessageRef
from chrono_mail.index import Index, IndexEntry, entry_from_raw
from chrono_mail.index.model import SearchFilter, SortField
from chrono_mail.index.schema import SORT_INDEXES
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture
from tests.test_index_unread_sorts import INDEXED_SORTS

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_index_covering_sorts.py 4.2.471"

# --- Version history ----------------------------------------------------
# v4.2.471: Initial: a sort reads only its index, several folders are ordered in one question, rows are read by the numbers an order
#           gave and found again by their ids when those no longer hold them, format 18 is upgraded and its indexes remade (D-306)

FOLDERS: tuple[str, ...] = ("Inbox", "Archive", "Lists")


def _entry(number: int, folder: str) -> IndexEntry:
    raw = raw_message(subject=f"Mail {number:03d}", sender=f"Person {300 - number} <p{number}@example.org>", message_id=f"<c{number}@x>")
    flags = frozenset({MessageFlag.SEEN}) if number % 4 else frozenset[MessageFlag]()
    return entry_from_raw(MessageRef("a" * 32, (folder,), f"{number:032d}"), "cur/x", flags, len(raw), raw)


def _index(path: Path) -> Index:
    index = Index(path)
    for number in range(1, 91):
        index.add(_entry(number, FOLDERS[number % 3]))
    return index


def _plan(index: Index, asked: list[str]) -> str:
    statements = [statement for statement in asked if statement.startswith("SELECT m.store_id, m.id")]
    assert len(statements) == 1, asked
    return " ".join(str(row[3]) for row in index._connection.execute("EXPLAIN QUERY PLAN " + statements[0]))  # noqa: SLF001


@pytest.mark.parametrize("sort", INDEXED_SORTS, ids=[sort.value for sort in INDEXED_SORTS])
def test_the_order_of_a_folder_is_read_from_its_sort_index_alone(tmp_path: Path, sort: SortField) -> None:
    # D-306: a sort opened every message's row for its id and read mark: 1.7 s on a spinning disk for 16,000, 0.07 s from the index.
    index = _index(tmp_path / "index.sqlite")
    asked: list[str] = []
    index._connection.set_trace_callback(asked.append)  # noqa: SLF001
    index.ordered("", ("Inbox",), SearchFilter(), sort, True)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    plan = _plan(index, asked)
    index.close()
    assert "COVERING INDEX" in plan and "SCAN" not in plan, plan


@pytest.mark.parametrize("sort", INDEXED_SORTS, ids=[sort.value for sort in INDEXED_SORTS])
def test_the_first_rows_of_a_folder_come_from_its_sort_index_with_the_value_they_are_sorted_by(tmp_path: Path, sort: SortField) -> None:
    # A Favorites view merges these instead of reading the first rows of every folder whole (82 folders: 8,200 random reads, D-306).
    index = _index(tmp_path / "index.sqlite")
    asked: list[str] = []
    index._connection.set_trace_callback(asked.append)  # noqa: SLF001
    first = index.first_rows("", ("Inbox",), SearchFilter(replied=False), sort, True, 5)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    assert [store_id for store_id, _, _ in first] == [row.store_id for row in index.search("", ("Inbox",), SearchFilter(replied=False), 0, 5, sort, True)]
    statements = [statement for statement in asked if statement.startswith("SELECT m.store_id, m.id")]
    plan = " ".join(str(row[3]) for row in index._connection.execute("EXPLAIN QUERY PLAN " + statements[0]))  # noqa: SLF001
    index.close()
    assert "COVERING INDEX" in plan and "SCAN" not in plan, plan


def test_rows_are_read_by_the_numbers_an_order_gave_and_found_by_their_ids_when_those_changed(tmp_path: Path) -> None:
    # Further rows by their ids: one lookup per message, 0.7 s per 100 on a spinning disk; by their numbers 0.2 s (D-306).
    index = _index(tmp_path / "index.sqlite")
    order = [store_id for store_id, _, _ in index.ordered("", ("Inbox",), SearchFilter(), SortField.SUBJECT, False)]
    asked: list[str] = []
    index._connection.set_trace_callback(asked.append)  # noqa: SLF001
    found = index.messages(order[:5])
    index._connection.set_trace_callback(None)  # noqa: SLF001
    assert list(found) == order[:5]
    assert any("WHERE m.id IN" in statement for statement in asked) and not any("WHERE m.store_id IN" in statement for statement in asked)
    moved = order[0]
    index.add(_entry(int(moved), "Archive"))  # written again: a new row number, another folder
    assert index.messages([moved])[moved].folder == ("Archive",)  # its old number holds nothing now: found by its id
    index.close()


def test_rows_remembered_for_an_index_file_are_never_used_for_a_rebuilt_one(tmp_path: Path) -> None:
    index = _index(tmp_path / "index.sqlite")
    order = [store_id for store_id, _, _ in index.ordered("", ("Inbox",), SearchFilter(), SortField.SUBJECT, False)]
    index.close()
    os.replace(tmp_path / "index.sqlite", tmp_path / "old.sqlite")
    rebuilt = Index(tmp_path / "index.sqlite")  # a new file under the same name, its rows numbered afresh
    for number in reversed(range(1, 91)):
        rebuilt.add(_entry(number, FOLDERS[number % 3]))
    asked: list[str] = []
    rebuilt._connection.set_trace_callback(asked.append)  # noqa: SLF001
    found = rebuilt.messages(order[:3])
    rebuilt._connection.set_trace_callback(None)  # noqa: SLF001
    rebuilt.close()
    assert list(found) == order[:3] and not any("WHERE m.id IN" in statement for statement in asked)


def _as_format_18(path: Path) -> None:
    connection = sqlite3.connect(path)
    for statement in SORT_INDEXES:
        connection.execute(f"DROP INDEX {statement.split()[2]}")
        connection.execute(statement.replace(", store_id, flags)", ")").replace(", store_id)", ")"))
    connection.execute("UPDATE meta SET value = '18' WHERE key = 'schema'")
    connection.commit()
    connection.close()


def test_an_index_of_format_18_is_upgraded_at_once_and_its_sort_indexes_remade_afterwards(setup: Setup) -> None:
    assert call(setup.engine, "mail.send", account=setup.account_id, to="bea@example.org", subject="Sent", body="hi").sent
    path = setup.engine.directories.index_file(setup.account_id)
    _as_format_18(path)
    index = Index(path)
    assert index.sort_indexes_to_make() == list(SORT_INDEXES) and index.sent_filling() is None
    assert len(index.ordered("", ("Sent",), SearchFilter(), SortField.SENDER)) == 1  # usable at once
    index.close()
    assert {state.need for state in call(setup.engine, "index.check")} == {"fill"}
    assert call(setup.engine, "index.fill-sent", account=setup.account_id).filled == 0  # no message read: only the indexes remade
    assert {state.need for state in call(setup.engine, "index.check")} == {""}
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() == ("19",)
    connection.close()


def test_favorites_pages_follow_one_order_also_where_messages_tie(setup: Setup) -> None:
    # Ties were broken by row number in the index and by folder when the rows were merged: pages of 3 showed 4 of 12 twice (review).
    path = setup.engine.directories.index_file(setup.account_id)
    assert call(setup.engine, "mail.send", account=setup.account_id, to="bea@example.org", subject="Start", body="hi").sent
    index = Index(path)
    for number in range(1, 13):
        raw = raw_message(subject="Same", sender="Ann <a@example.org>", message_id=f"<t{number}@x>")
        folder = ("Sent",) if number % 2 else ("Drafts",)
        index.add(entry_from_raw(MessageRef(setup.account_id, folder, f"{number:032d}"), "cur/x", frozenset(), len(raw), raw))
    index.close()
    whole = [row.message_id for row in call(setup.engine, "message.combined", view="everywhere", sort="subject", limit=1000)]
    paged = [row.message_id for offset in range(0, 15, 3) for row in call(setup.engine, "message.combined", view="everywhere", sort="subject", offset=offset, limit=3)]
    assert paged == whole and len(set(whole)) == len(whole) == 13


def test_the_row_numbers_kept_are_the_newest_and_trimmed_in_one_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Trimmed one at a time from the front, 500,000 numbers into a limit of 300,000 took 7 s, holding the lock (review).
    from chrono_mail.index import index as index_module
    monkeypatch.setattr(index_module, "_ROW_NUMBERS_KEPT", 3)
    index = Index(tmp_path / "index.sqlite")
    index._remember_rows({f"{number:032d}": number for number in range(1, 6)})  # noqa: SLF001
    assert index._row_numbers() == {f"{number:032d}": number for number in (3, 4, 5)}  # noqa: SLF001
    index.close()


@pytest.mark.parametrize("sort", INDEXED_SORTS, ids=[sort.value for sort in INDEXED_SORTS])
def test_an_unread_view_is_read_from_its_sort_index_alone(tmp_path: Path, sort: SortField) -> None:
    # The unread copies lacked flags, which their own condition is read from: one row per unread message (review).
    index = _index(tmp_path / "index.sqlite")
    asked: list[str] = []
    index._connection.set_trace_callback(asked.append)  # noqa: SLF001
    index.ordered("", ("Inbox",), SearchFilter(unread=True), sort, True)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    plan = _plan(index, asked)
    index.close()
    assert "COVERING INDEX" in plan and "SCAN" not in plan, plan
