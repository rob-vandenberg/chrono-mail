from dataclasses import replace
from pathlib import Path

import pytest

from chrono_mail.core import MessageFlag, MessageRef
from chrono_mail.index import Index, IndexEntry, entry_from_raw
from chrono_mail.index.model import SearchFilter, SortField
from tests.mailfactory import raw_message
from tests.test_index_unread_sorts import INDEXED_SORTS

# --- Version ------------------------------------------------------------
__version__ = "test_index_mark_views.py 4.2.470"

# --- Version history ----------------------------------------------------
# v4.2.470: Initial: a Flagged or Tags view sorted by any column reads only its marked mail; Unreplied is counted through its index (D-305)


def _entry(number: int, folder: str, flags: frozenset[MessageFlag]) -> IndexEntry:
    raw = raw_message(subject=f"Mail {number:02d}", sender=f"Person {90 - number} <p{number}@example.org>", message_id=f"<f{number}@x>")
    return entry_from_raw(MessageRef("a" * 32, (folder,), f"{number:032d}"), "cur/x", flags, len(raw), raw)


def _index(path: Path) -> Index:
    """An Inbox of 60 read messages: every tenth flagged and tagged work, every third replied; an Archive beside it."""
    index = Index(path)
    for number in range(1, 61):
        flags = {MessageFlag.SEEN} | ({MessageFlag.FLAGGED} if number % 10 == 0 else set()) | ({MessageFlag.REPLIED} if number % 3 == 0 else set())
        index.add(_entry(number, "Inbox", frozenset(flags)))
        if number % 10 == 0:
            index.update_tags(f"{number:032d}", ("Work",))
    for number in range(61, 71):
        index.add(_entry(number, "Archive", frozenset({MessageFlag.SEEN, MessageFlag.FLAGGED})))
    return index


def _plans(index: Index, asked: list[str]) -> list[str]:
    plans = [
        " ".join(str(row[3]) for row in index._connection.execute("EXPLAIN QUERY PLAN " + statement))  # noqa: SLF001
        for statement in asked
        if statement.lstrip().upper().startswith("SELECT") and "FROM messages m" in statement
        and not statement.startswith("SELECT count(*) FROM (SELECT 1 FROM messages m WHERE m.folder")  # the count of its marked mail (_few_marked)
    ]
    index.close()
    return plans


VIEWS: dict[str, SearchFilter] = {"flagged": SearchFilter(flagged=True), "tagged": SearchFilter(tag="work")}


@pytest.mark.parametrize("sort", INDEXED_SORTS, ids=[sort.value for sort in INDEXED_SORTS])
@pytest.mark.parametrize("view", list(VIEWS), ids=list(VIEWS))
@pytest.mark.parametrize("kept", [False, True], ids=["nothing kept", "a message kept"])
def test_a_flagged_or_tagged_view_sorted_by_any_column_reads_only_its_marked_mail(tmp_path: Path, sort: SortField, view: str, kept: bool) -> None:
    # D-305: the folder led and every message of it was opened to test its mark (31 s for 3 flagged, 30 s for a tag of 7, on a spinning disk).
    index = _index(tmp_path / "index.sqlite")
    filters = replace(VIEWS[view], stay=frozenset({f"{1:032d}"})) if kept else VIEWS[view]
    asked: list[str] = []
    index._connection.set_trace_callback(asked.append)  # noqa: SLF001
    index.search("", ("Inbox",), filters, sort=sort, descending=False)
    index.ordered("", ("Inbox",), filters, sort, False)
    index.tally("", ("Inbox",), filters)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    plans = _plans(index, asked)
    assert len(plans) == 3
    for plan in plans:
        if view == "flagged":
            assert "USING INTEGER PRIMARY KEY (rowid=?)" in plan and "messages_by_flagged (folder=? AND <expr>=?)" in plan, plan  # by their rows
        else:
            assert "message_tags_by_tag (tag=?)" in plan and "sqlite_autoindex_messages_1 (store_id=?)" in plan, plan  # by their ids
            assert "SEARCH m USING INDEX messages_by_" not in plan, plan  # never the folder's sort order
        assert "SCAN m" not in plan, plan


@pytest.mark.parametrize("view", list(VIEWS), ids=list(VIEWS))
def test_a_flagged_or_tagged_view_lists_its_marked_mail_and_the_kept_message_in_order(tmp_path: Path, view: str) -> None:
    index = _index(tmp_path / "index.sqlite")
    filters = replace(VIEWS[view], stay=frozenset({f"{1:032d}", f"{61:032d}"}))  # Mail 61 is in the Archive: it stays out
    found = [row.subject for row in index.search("", ("Inbox",), filters, sort=SortField.SENDER, descending=False)]
    assert found == ["Mail 60", "Mail 50", "Mail 40", "Mail 30", "Mail 20", "Mail 10", "Mail 01"]  # Person 30 first, Person 89 last
    assert index.tally("", ("Inbox",), filters) == (7, 0)
    index.close()


def test_an_unreplied_view_is_counted_through_the_replied_index(tmp_path: Path) -> None:
    # Nearly every message is unreplied: the first rows are found at once, but the count opened every message of the folder (14 s).
    index = _index(tmp_path / "index.sqlite")
    asked: list[str] = []
    index._connection.set_trace_callback(asked.append)  # noqa: SLF001
    assert index.tally("", ("Inbox",), SearchFilter(replied=False)) == (40, 0)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    plans = _plans(index, asked)
    assert len(plans) == 1 and "messages_by_replied (folder=? AND <expr>=?)" in plans[0], plans


def test_how_a_tags_mail_is_found_is_chosen_without_reading_its_messages(tmp_path: Path) -> None:
    # Counting the tag's messages walked the whole folder (3.7 s of a 3.9 s tag view); a tag's size comes from its own index (review).
    index = _index(tmp_path / "index.sqlite")
    asked: list[str] = []
    index._connection.set_trace_callback(asked.append)  # noqa: SLF001
    index.search("", ("Inbox",), SearchFilter(tag="work"), 0, 5, SortField.DATE, True)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    index.close()
    assert [statement for statement in asked if statement.startswith("SELECT count(*) FROM messages")] == []
    assert any(statement.startswith("SELECT count(*) FROM (SELECT 1 FROM message_tags WHERE tag") for statement in asked)  # no further than the folder


def test_a_tag_larger_than_the_folder_is_tested_within_the_folder_and_never_walked_across_the_account(tmp_path: Path) -> None:
    # A tag of 61,000 messages walked for every folder of a Favorites view took 13 s instead of 4 (review).
    index = _index(tmp_path / "index.sqlite")
    for number in range(61, 71):
        index.update_tags(f"{number:032d}", ("Work",))  # 16 tagged in all, 10 of them in the Archive of 10
    asked: list[str] = []
    index._connection.set_trace_callback(asked.append)  # noqa: SLF001
    found = index.search("", ("Archive",), SearchFilter(tag="work"), sort=SortField.SENDER, descending=False)
    index._connection.set_trace_callback(None)  # noqa: SLF001
    assert len(found) == 10
    plans = _plans(index, asked)
    assert len(plans) == 1 and "message_tags_by_tag" in plans[0] and "SCAN m" not in plans[0] and "(rowid=?)" not in plans[0], plans
