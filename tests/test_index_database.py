import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import override

import pytest

from chrono_mail.core import StorageError
from chrono_mail.index import Index, IndexDatabase
from chrono_mail.pimstore.index import PimIndex

# --- Version ------------------------------------------------------------
__version__ = "test_index_database.py 2.5.335"

# --- Version history ----------------------------------------------------
# v2.5.335: two openers of a new or old file at the same moment: the second finds it made or upgraded, never damaged (D-247)
# v1.8.263: Initial tests of the one copy of opening an index file and its transactions (R-16)


def test_both_indexes_open_their_file_the_one_way() -> None:
    assert issubclass(Index, IndexDatabase) and issubclass(PimIndex, IndexDatabase)


def test_a_file_that_is_no_database_is_damaged_and_left_closed_for_the_rebuild(tmp_path: Path) -> None:
    for kind in (Index, PimIndex):
        path = tmp_path / f"{kind.__name__}.sqlite"
        path.write_bytes(os.urandom(8192))
        with pytest.raises(StorageError, match="damaged; rebuild it"):
            kind(path)
        path.unlink()  # nothing holds it open (Windows could not delete it then)
        for suffix in ("-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


def test_an_index_of_another_kind_has_an_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    Index(path).close()
    with pytest.raises(StorageError, match="unknown format"):
        PimIndex(path)


class _Notes(IndexDatabase):
    @override
    def _prepare(self) -> None:
        self._connection.execute("CREATE TABLE IF NOT EXISTS notes (text TEXT)")

    def add(self, *texts: str, fail: bool = False) -> None:
        with self._transaction():
            for text in texts:
                self._connection.execute("INSERT INTO notes VALUES (?)", (text,))
            if fail:
                raise RuntimeError("stopped halfway")

    def texts(self) -> list[str]:
        return [str(row[0]) for row in self._connection.execute("SELECT text FROM notes ORDER BY text")]


def test_a_change_is_kept_whole_or_not_at_all(tmp_path: Path) -> None:
    notes = _Notes(tmp_path / "notes.sqlite")
    notes.add("a", "b")
    with pytest.raises(RuntimeError):
        notes.add("c", "d", fail=True)
    assert notes.texts() == ["a", "b"] and notes.check()
    assert notes._connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"  # noqa: SLF001
    notes.close()
    with pytest.raises(sqlite3.ProgrammingError):
        notes.texts()


def another_opener_comes_first(monkeypatch: pytest.MonkeyPatch, kind: type[IndexDatabase], path: Path) -> None:
    """Another part of the program opens the same file at the worst moment: just before this one takes the write lock to make or
    upgrade it. No timing: the other opener runs, whole, at that exact point."""
    real = IndexDatabase._transaction  # noqa: SLF001
    came: list[bool] = []

    @contextmanager
    def racing(self: IndexDatabase) -> Generator[None]:
        if not came:
            came.append(True)
            kind(path).close()
        with real(self):
            yield

    monkeypatch.setattr(IndexDatabase, "_transaction", racing)


def test_a_new_file_opened_twice_at_once_is_made_once_and_never_damaged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for kind in (Index, PimIndex):
        path = tmp_path / f"{kind.__name__}.sqlite"
        with monkeypatch.context() as patch:
            another_opener_comes_first(patch, kind, path)
            index = kind(path)  # before: "The index is damaged; rebuild it", and the file was removed while the other used it
        index.close()
        kind(path).close()


def test_an_old_format_opened_twice_at_once_is_upgraded_once_and_never_damaged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_engine_rebuilds import as_format_11

    path = tmp_path / "index.sqlite"
    Index(path).close()
    as_format_11(path)
    another_opener_comes_first(monkeypatch, Index, path)
    index = Index(path)  # before: the second upgrade added the same column again: "damaged"
    assert index.sent_filling() == (0, 0)  # upgraded once: its Sent dates still to be filled in
    index.close()
