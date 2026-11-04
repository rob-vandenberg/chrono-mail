import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from chrono_mail.core import MessageFlag, MessageRef, StorageError
from chrono_mail.index import Index, IndexBuilder, IndexEntry, entry_from_raw
from chrono_mail.index.model import SearchFilter, SortField
from tests.mailfactory import raw_message
from tests.test_index_index import bounds_at

# --- Version ------------------------------------------------------------
__version__ = "test_index_folder_rows.py 4.4.539"

# --- Version history ----------------------------------------------------
# v4.4.539: Initial: a folder is a row of its own: a rename or a move of a tree changes one row, and an older index is upgraded safely (D-314)

TREE: tuple[str, ...] = ("Clients",)
INNER: tuple[str, ...] = ("Clients", "Acme")
DEEP: tuple[str, ...] = ("Clients", "Acme", "2026")


def _entry(number: int, folder: tuple[str, ...], seen: bool = False, subject: str = "") -> IndexEntry:
    raw = raw_message(subject=subject or f"Mail {number:02d}", sender=f"Person {number} <p{number}@example.org>", message_id=f"<f{number}@x>")
    flags = frozenset({MessageFlag.SEEN}) if seen else frozenset[MessageFlag]()
    return entry_from_raw(MessageRef("a" * 32, folder, f"{number:032d}"), "cur/x", flags, len(raw), raw)


def _filled(path: Path) -> Index:
    """A tree of three folders beside the Inbox: 1 to 3 in Clients, 4 to 6 in Acme, 7 to 9 in 2026, 10 in the Inbox; the even ones are read."""
    index = Index(path)
    folders = (TREE, TREE, TREE, INNER, INNER, INNER, DEEP, DEEP, DEEP, ("Inbox",))
    index.add_many(_entry(number, folder, seen=number % 2 == 0) for number, folder in enumerate(folders, 1))
    return index


def _changes(index: Index, old: tuple[str, ...], new: tuple[str, ...]) -> int:
    connection = index._connection  # noqa: SLF001
    before = connection.total_changes
    index.rename_folder(old, new)
    return connection.total_changes - before


def _subjects(index: Index, folder: tuple[str, ...]) -> list[str]:
    return sorted(message.subject for message in index.page(folder))


def test_the_rename_of_a_tree_changes_one_row_and_every_folder_in_it_follows(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    assert _changes(index, TREE, ("Customers",)) == 1
    assert _subjects(index, ("Customers", "Acme", "2026")) == ["Mail 07", "Mail 08", "Mail 09"]
    assert index.page(DEEP) == [] and index.numbers(DEEP) == (0, 0)
    assert index.numbers(("Customers", "Acme")) == (3, 1)
    assert index.get(f"{5:032d}").folder == ("Customers", "Acme")  # pyrefly: ignore[missing-attribute]
    assert index.tally("", ("Customers",), SearchFilter(subfolders=True)) == (9, 5)
    assert index.snapshot()[f"{8:032d}"] == (("Customers", "Acme", "2026"), "cur/x")
    assert index.check()
    index.close()


def test_the_rename_of_a_folder_inside_a_tree_changes_one_row(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    assert _changes(index, INNER, ("Clients", "Apex")) == 1
    assert _subjects(index, ("Clients", "Apex", "2026")) == ["Mail 07", "Mail 08", "Mail 09"]
    assert _subjects(index, TREE) == ["Mail 01", "Mail 02", "Mail 03"]
    index.close()


def test_a_tree_moved_to_another_parent_changes_its_row_and_makes_the_parent_it_lacks(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    assert _changes(index, INNER, ("Archive", "2025", "Acme")) == 3  # the two parents it lacked, and its own row
    assert _subjects(index, ("Archive", "2025", "Acme", "2026")) == ["Mail 07", "Mail 08", "Mail 09"]
    assert index.tally("", TREE, SearchFilter(subfolders=True)) == (3, 2)
    index.close()


def test_a_folder_renamed_back_is_as_it_was(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    index.rename_folder(TREE, ("Customers",))
    assert _changes(index, ("Customers",), TREE) == 1
    assert _subjects(index, DEEP) == ["Mail 07", "Mail 08", "Mail 09"] and index.numbers(DEEP) == (3, 2)
    index.close()


def test_a_name_whose_mail_has_left_is_taken_over_by_the_tree(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    index.add(_entry(11, ("Old", "Inner")))
    index.remove(f"{11:032d}")
    index.rename_folder(TREE, ("Old",))
    assert _subjects(index, ("Old", "Acme", "2026")) == ["Mail 07", "Mail 08", "Mail 09"]
    assert index.numbers(("Old",)) == (3, 2) and index.numbers(("Old", "Inner")) == (0, 0)
    assert index.tally("", ("Old",), SearchFilter(subfolders=True)) == (9, 5)
    index.close()


def test_a_tree_renamed_to_a_name_that_holds_mail_joins_it(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    index.add(_entry(11, ("Other", "Acme")))
    index.rename_folder(TREE, ("Other",))
    assert _subjects(index, ("Other", "Acme")) == ["Mail 04", "Mail 05", "Mail 06", "Mail 11"]
    assert index.numbers(("Other", "Acme")) == (4, 2) and index.numbers(INNER) == (0, 0)
    assert _subjects(index, ("Other", "Acme", "2026")) == ["Mail 07", "Mail 08", "Mail 09"]
    index.close()


def test_a_folder_moved_inside_itself_stays_whole(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    index.rename_folder(INNER, ("Clients", "Acme", "Moved"))
    assert _subjects(index, ("Clients", "Acme", "Moved")) == ["Mail 04", "Mail 05", "Mail 06"]
    assert _subjects(index, ("Clients", "Acme", "Moved", "2026")) == ["Mail 07", "Mail 08", "Mail 09"]
    assert index.tally("", TREE, SearchFilter(subfolders=True)) == (9, 5)
    index.close()


def test_a_folder_the_index_has_no_mail_of_changes_nothing(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    assert _changes(index, ("Nowhere",), ("Elsewhere",)) == 0
    assert _changes(index, TREE, TREE) == 0
    assert index.page(("Nowhere",)) == [] and index.numbers(("Nowhere",)) == (0, 0) and index.count(("Nowhere",)) == 0
    assert index.tally("", ("Nowhere",), SearchFilter(subfolders=True)) == (0, 0)
    assert index.remove_folder(("Nowhere",)) == 0
    index.close()


def test_a_moved_message_counts_in_its_new_folder_also_a_new_one(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    index.move(f"{7:032d}", ("New", "Place"), "cur/y")
    assert index.numbers(DEEP) == (2, 1) and index.numbers(("New", "Place")) == (1, 1)
    moved = index.get(f"{7:032d}")
    assert moved is not None and moved.folder == ("New", "Place") and moved.path == "cur/y"
    index.close()


def test_the_numbers_of_a_renamed_folder_follow_every_later_change(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    index.rename_folder(TREE, ("Customers",))
    folder = ("Customers", "Acme", "2026")
    index.update_flags(f"{7:032d}", frozenset({MessageFlag.SEEN}), "cur/x")
    assert index.numbers(folder) == (3, 1)
    index.remove(f"{8:032d}")
    assert index.numbers(folder) == (2, 1)
    index.add(_entry(11, folder))
    assert index.numbers(folder) == (3, 2) and index.count(folder, unread_only=True) == 2
    assert index.remove_folder(folder) == 3 and index.numbers(folder) == (0, 0)
    index.close()


def test_a_sort_and_the_groups_by_folder_show_the_new_name(tmp_path: Path) -> None:
    index = _filled(tmp_path / "index.sqlite")
    assert index.search("", None, sort=SortField.FOLDER, descending=False)[0].folder == TREE
    index.rename_folder(TREE, ("Zebra",))
    order = [message.folder[0] for message in index.search("", None, sort=SortField.FOLDER, descending=False)]
    assert order == ["Inbox", *["Zebra"] * 9]
    groups = index.groups(bounds_at(2), sort=SortField.FOLDER, descending=False)
    assert [(group.value, group.count) for group in groups] == [("Inbox", 1), ("Zebra", 3), ("Zebra/Acme", 3), ("Zebra/Acme/2026", 3)]
    index.close()


def test_what_another_connection_does_to_the_folders_shows_at_once(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    index = _filled(path)
    assert _subjects(index, DEEP) == ["Mail 07", "Mail 08", "Mail 09"]  # its folders read
    other = Index(path)
    other.rename_folder(TREE, ("Customers",))
    other.add(_entry(11, ("Made", "There")))
    assert index.get(f"{7:032d}").folder == ("Customers", "Acme", "2026")  # pyrefly: ignore[missing-attribute]
    assert _subjects(index, ("Made", "There")) == ["Mail 11"]
    index.add(_entry(12, ("Made", "There", "Here")))  # the row of its parent, made by the other connection, is used: no second one
    assert other.numbers(("Made", "There", "Here")) == (1, 1) and index.check()
    assert index.search("", None, sort=SortField.FOLDER, descending=False)[0].folder == ("Customers",)
    other.rename_folder(("Customers",), ("Zebra",))
    assert index.search("", None, sort=SortField.FOLDER, descending=True)[0].folder[0] == "Zebra"
    other.close()
    index.close()


def test_a_folder_of_a_change_that_failed_is_gone_again(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    index = _filled(path)

    def broken() -> "list[IndexEntry]":
        return [_entry(11, ("Short", "Lived")), _entry(12, ("Short", "Lived")), None]  # pyrefly: ignore[bad-return]

    with pytest.raises(AttributeError):
        index.add_many(broken())
    assert index.page(("Short", "Lived")) == []
    index.add(_entry(13, ("Short", "Lived")))
    index.close()
    index = Index(path)  # as a new connection reads it: the message holds a row that is there
    assert _subjects(index, ("Short", "Lived")) == ["Mail 13"]
    index.close()


def test_a_rebuilt_index_finds_every_folder_by_its_path_with_ids_of_its_own(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    index = _filled(path)
    index.rename_folder(TREE, ("Customers",))
    index.close()
    builder = IndexBuilder(path)
    builder.add_many([_entry(20, ("Inbox",)), _entry(21, ("Customers", "Acme", "2026"))])
    rebuilt = builder.finish()
    assert _subjects(rebuilt, ("Customers", "Acme", "2026")) == ["Mail 21"] and rebuilt.numbers(("Customers", "Acme")) == (0, 0)
    rebuilt.close()


# --- the upgrade of an older index --------------------------------------------------------


def _format_19(path: Path) -> None:
    from tests.index_formats import as_format_19

    index = _filled(path)
    index.add(_entry(11, ("Tagged",), subject="Quarterly invoice"))
    index.update_tags(f"{11:032d}", ("Work",))
    index.close()
    connection = sqlite3.connect(path)
    as_format_19(connection)
    connection.commit()
    connection.close()


def _holds_everything(path: Path) -> None:
    index = Index(path)
    assert index.message_count() == 11
    assert _subjects(index, DEEP) == ["Mail 07", "Mail 08", "Mail 09"]
    assert index.numbers(DEEP) == (3, 2) and index.numbers(INNER) == (3, 1) and index.numbers(("Inbox",)) == (1, 0)
    assert [message.subject for message in index.search("invoice")] == ["Quarterly invoice"]  # the words still lead to their messages
    assert [message.subject for message in index.search("", None, SearchFilter(tag="work"))] == ["Quarterly invoice"]
    assert index.tally("", TREE, SearchFilter(subfolders=True)) == (9, 5)
    assert index.check()
    index.close()


def test_an_index_of_format_19_keeps_everything_and_is_usable_before_its_steps_afterwards(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    _format_19(path)
    _holds_everything(path)
    index = Index(path)
    waiting = index.sort_indexes_to_make()
    assert waiting[-1] == "DROP TABLE messages_old" and sum(step.startswith("DROP INDEX") for step in waiting) >= 3
    assert _changes(index, TREE, ("Customers",)) == 1  # a rename is one row from the first moment
    index.rename_folder(("Customers",), TREE)
    while waiting := index.sort_indexes_to_make():
        index.make_sort_index(waiting[0])
    names = {str(row[0]) for row in index._connection.execute("SELECT name FROM sqlite_master")}  # noqa: SLF001
    assert "messages_old" not in names and not any(name.startswith("messages_by_") for name in names)
    index.close()
    _holds_everything(path)


def test_mail_that_arrives_between_the_steps_afterwards_stays(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    _format_19(path)
    index = Index(path)
    index.make_sort_index(index.sort_indexes_to_make()[0])
    index.add(_entry(12, DEEP))
    index.move(f"{7:032d}", ("Inbox",), "cur/z")
    while waiting := index.sort_indexes_to_make():
        index.make_sort_index(waiting[0])
    assert index.numbers(DEEP) == (3, 2) and index.numbers(("Inbox",)) == (2, 1) and index.check()
    index.close()


def _killed_after(path: Path, statements: int, steps: bool) -> bool:
    """A child process opens the index and dies at once, as a power cut does, at this statement: True when it got that far."""
    fork: Callable[[], int] = getattr(os, "fork")  # not on Windows: the tests that come here are skipped there
    child = fork()
    if child == 0:
        ran = [0]

        def count(_: str) -> None:
            ran[0] += 1
            if ran[0] == statements:
                os._exit(9)

        real = sqlite3.connect

        def traced(*arguments: object, **options: object) -> sqlite3.Connection:
            connection = real(*arguments, **options)  # pyrefly: ignore[no-matching-overload]
            connection.set_trace_callback(count)
            return connection

        sqlite3.connect = traced  # pyrefly: ignore[bad-assignment]
        index = Index(path)
        while steps and (waiting := index.sort_indexes_to_make()):
            index.make_sort_index(waiting[0])
        os._exit(0)
    return os.waitpid(child, 0)[1] != 0


@pytest.mark.skipif(sys.platform == "win32", reason="needs fork")
@pytest.mark.parametrize("steps", [False, True], ids=["the upgrade", "the steps afterwards"])
def test_the_upgrade_killed_at_any_statement_loses_nothing_and_finishes_later(tmp_path: Path, steps: bool) -> None:
    made = tmp_path / "made.sqlite"
    _format_19(made)
    data = made.read_bytes()
    statements = 0
    while True:
        statements += 3 if steps else 1  # the steps afterwards repeat three kinds of statement over and over
        path = tmp_path / "index.sqlite"
        for leftover in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            leftover.unlink(missing_ok=True)
        path.write_bytes(data)
        if steps:
            Index(path).close()  # the upgrade done: only the steps afterwards are killed
        killed = _killed_after(path, statements, steps)
        _holds_everything(path)
        index = Index(path)
        while waiting := index.sort_indexes_to_make():
            index.make_sort_index(waiting[0])
        index.close()
        _holds_everything(path)
        if not killed:
            break
    assert statements > 20


def test_a_message_of_a_folder_the_numbers_do_not_know_means_the_index_is_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    _format_19(path)
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM folder_numbers WHERE folder = 'Inbox'")
    connection.commit()
    connection.close()
    with pytest.raises(StorageError):
        Index(path)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone() == ("19",)  # nothing half done
    connection.close()


def test_a_long_upgrade_of_another_connection_is_waited_for_and_never_taken_for_damage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "index.sqlite"
    _format_19(path)
    monkeypatch.setattr("chrono_mail.index.database.DATABASE_WAIT_MS", 50, raising=False)
    busy = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    busy.execute("BEGIN IMMEDIATE")  # the other connection's upgrade, longer than the usual wait
    threading.Timer(0.5, lambda: busy.execute("COMMIT")).start()
    started = time.monotonic()
    index = Index(path)
    assert time.monotonic() - started >= 0.4  # it waited, past its usual wait
    assert index.message_count() == 11
    index.close()
    busy.close()
