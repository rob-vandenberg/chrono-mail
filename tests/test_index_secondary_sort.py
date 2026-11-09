import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chrono_mail.core import MessageRef
from chrono_mail.index import Index, IndexEntry, entry_from_raw
from chrono_mail.index.model import SearchFilter, SortField
from chrono_mail.index.schema import SCHEMA_VERSION, SORT_INDEX_PREFIX, SORT_INDEXES
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "test_index_secondary_sort.py 4.4.562"

# --- Version history ----------------------------------------------------
# v4.4.562: Initial: messages with the same value by when they were received, newest or oldest first, from a sort index alone; format 20
#           upgraded to 21 with its new sort indexes made and the old ones dropped afterwards (D-320)

# Stored out of date order, so the row numbers (the order before D-320) differ from the received order.
DAYS: tuple[int, ...] = (5, 1, 9, 3)


def _entry(number: int, day: int) -> IndexEntry:
    moment = datetime(2026, 3, day, 12, 0, tzinfo=UTC)
    raw = raw_message(subject="Same subject", sender="Same Person <same@example.org>", date=moment, message_id=f"<t{number}@x>")
    return entry_from_raw(MessageRef("a" * 32, ("Inbox",), f"{number:032d}"), "cur/x", frozenset(), len(raw), raw, stored=moment)


def _index(path: Path) -> Index:
    index = Index(path)
    for number, day in enumerate(DAYS):
        index.add(_entry(number, day))
    return index


@pytest.mark.parametrize("descending", [True, False], ids=["down", "up"])
@pytest.mark.parametrize("sort", [SortField.SUBJECT, SortField.SENDER], ids=["subject", "from"])
def test_messages_with_the_same_value_are_ordered_by_when_they_were_received_as_set(tmp_path: Path, sort: SortField, descending: bool) -> None:
    # D-320: they came in the order the index was given them (random after a rebuild); now by received time, whichever way the sort goes.
    index = _index(tmp_path / "index.sqlite")
    newest = index.search("", ("Inbox",), SearchFilter(), 0, 10, sort, descending)
    oldest = index.search("", ("Inbox",), SearchFilter(), 0, 10, sort, descending, ties_oldest_first=True)
    ordered = [store_id for store_id, _, _ in index.ordered("", ("Inbox",), SearchFilter(), sort, descending, ties_oldest_first=True)]
    index.close()
    days = [row.date.day for row in newest if row.date is not None]
    assert days == sorted(DAYS, reverse=True)
    assert [row.store_id for row in oldest] == [row.store_id for row in reversed(newest)]
    assert ordered == [row.store_id for row in oldest]  # the order question follows the same setting


@pytest.mark.parametrize("ties_oldest_first", [False, True], ids=["ties-newest", "ties-oldest"])
@pytest.mark.parametrize("descending", [True, False], ids=["down", "up"])
@pytest.mark.parametrize("sort", [SortField.SUBJECT, SortField.SIZE, SortField.FLAGGED, SortField.SENT], ids=["subject", "size", "flagged", "sent"])
def test_every_order_of_the_ties_is_read_from_a_sort_index_alone(tmp_path: Path, sort: SortField, descending: bool, ties_oldest_first: bool) -> None:
    # SQLite reads an index both ways but never in mixed directions: without the second index of each sort, two of the four orders sort again.
    index = _index(tmp_path / "index.sqlite")
    asked: list[str] = []
    index._connection.set_trace_callback(asked.append)  # noqa: SLF001
    index.ordered("", ("Inbox",), SearchFilter(), sort, descending, ties_oldest_first=ties_oldest_first)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    statement = next(statement for statement in asked if statement.startswith("SELECT m.store_id, m.id"))
    plan = " ".join(str(row[3]) for row in index._connection.execute("EXPLAIN QUERY PLAN " + statement))  # noqa: SLF001
    index.close()
    assert "COVERING INDEX" in plan and "TEMP B-TREE" not in plan, plan


def test_a_format_20_index_becomes_21_and_makes_its_new_sort_indexes_and_drops_the_old_ones_afterwards(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    _index(path).close()
    connection = sqlite3.connect(path)
    connection.execute(f"DROP INDEX {SORT_INDEX_PREFIX}messages_by_subject_ties_newest")
    connection.execute(f"CREATE INDEX {SORT_INDEX_PREFIX}messages_by_subject ON messages (folder_id, subject COLLATE NOCASE, id, store_id)")  # as format 20 had it
    connection.execute("UPDATE meta SET value = '20' WHERE key = 'schema'")
    connection.commit()
    connection.close()
    index = Index(path)
    waiting = index.sort_indexes_to_make()
    assert index._connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()[0] == str(SCHEMA_VERSION)  # noqa: SLF001
    assert waiting == [next(s for s in SORT_INDEXES if f" {SORT_INDEX_PREFIX}messages_by_subject_ties_newest " in s), f"DROP INDEX {SORT_INDEX_PREFIX}messages_by_subject"]
    for step in waiting:
        index.make_sort_index(step)
    assert index.sort_indexes_to_make() == []
    assert len(index.search("", ("Inbox",), SearchFilter(), 0, 10, SortField.SUBJECT, True)) == len(DAYS)
    index.close()
