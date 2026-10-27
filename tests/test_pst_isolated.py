import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from chrono_mail.pst.isolated import items_in_child, store_in_child
from chrono_mail.pst.model import PstError, PstFailure, PstGroup, PstItem, PstStore
from chrono_mail.pst.reading import Progress

# --- Version ------------------------------------------------------------
__version__ = "test_pst_isolated.py 4.2.385"

# --- Version history ----------------------------------------------------
# v4.2.385: Initial tests: the reading in a child process, a crash or hang there costing one item, and resuming

TOTAL = 8
CRASH_AT = 3
HANG_AT = 2
FOLDER = ("Inbox",)


# The readers below run in the child process (they are found there by name), each acting up on one item number.


def _items(start: int, progress: Progress | None, trouble: int | None, kind: str) -> Iterator[tuple[int, PstItem]]:
    if progress is not None:
        progress.total(TOTAL)
    for number in range(start, TOTAL):
        if progress is not None:
            progress.at(number, FOLDER)
        if number == trouble:
            if kind == "crash":
                os._exit(7)  # what a crash inside the reading library looks like from outside
            if kind == "raise":
                os.write(2, b"secret from the library\n")  # what a library might print on its own
                print("secret printed", file=sys.stderr)
                raise RuntimeError("secret subject")  # an error the reader did not catch: its text never leaves the child
            time.sleep(3600)  # a hang: the parent stops the child
        yield number, PstGroup(FOLDER, f"item {number}")


def good_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    return _items(start, progress, None, "")


def crashing_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    return _items(start, progress, CRASH_AT, "crash")


def raising_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    return _items(start, progress, CRASH_AT, "raise")


def hanging_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    return _items(start, progress, HANG_AT, "hang")


def dying_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    """Dies in the file's structure (before it reaches an item) whenever it starts at 5 or later."""
    if start >= 5:
        os._exit(9)
    return _items(start, progress, None, "")


def dying_at_close_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    """Gives every item, then dies while closing the file (before it can say it is done)."""
    yield from _items(start, progress, None, "")
    os._exit(5)


def refusing_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    raise PstError("other.pst is not a data file of Outlook (.pst)")


def good_store(path: Path) -> PstStore:
    return PstStore("Personal Folders", ())


def refusing_store(path: Path) -> PstStore:
    raise PstError("other.pst is not a data file of Outlook (.pst)")


def crashing_store(path: Path) -> PstStore:
    os._exit(11)


def _names(found: list[tuple[int, PstItem]]) -> list[tuple[int, str]]:
    return [(number, item.name if isinstance(item, PstGroup) else "failed") for number, item in found]


def test_every_item_once_in_order(tmp_path: Path) -> None:
    found = list(items_in_child(tmp_path / "a.pst", 0, good_reader))
    assert _names(found) == [(number, f"item {number}") for number in range(TOTAL)]


def test_resume_at_a_number(tmp_path: Path) -> None:
    found = list(items_in_child(tmp_path / "a.pst", 6, good_reader))
    assert _names(found) == [(6, "item 6"), (7, "item 7")]


def test_a_crash_costs_one_item(tmp_path: Path) -> None:
    found = list(items_in_child(tmp_path / "a.pst", 0, crashing_reader))
    assert [number for number, _ in found] == list(range(TOTAL))
    failure = found[CRASH_AT][1]
    assert isinstance(failure, PstFailure) and failure.folder == FOLDER and "could not be read" in failure.what
    assert all(isinstance(item, PstGroup) for number, item in found if number != CRASH_AT)


def test_an_uncaught_error_costs_one_item_and_says_nothing(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """Review fault 19: an error the reader did not catch is the kind of fault only; nothing of it reaches a terminal or log."""
    found = list(items_in_child(tmp_path / "a.pst", 0, raising_reader))
    assert [number for number, _ in found] == list(range(TOTAL))
    failure = found[CRASH_AT][1]
    assert isinstance(failure, PstFailure) and "secret" not in failure.what
    captured = capfd.readouterr()
    assert "secret" not in captured.err and "secret" not in captured.out


def test_a_hang_costs_one_item(tmp_path: Path) -> None:
    found = list(items_in_child(tmp_path / "a.pst", 0, hanging_reader, seconds=3.0))  # the start-up has its own, long wait
    assert [number for number, _ in found] == list(range(TOTAL))
    assert isinstance(found[HANG_AT][1], PstFailure)


def test_dying_before_any_item_is_not_finished(tmp_path: Path) -> None:
    """The engine's review: children dying twice before an item end the reading with PstError, so the import is not marked finished and
    a later one goes on from the next number."""
    found = list(items_in_child(tmp_path / "a.pst", 3, dying_reader))
    assert _names(found) == [(number, f"item {number}") for number in range(3, TOTAL)]  # the first child gets through
    with pytest.raises(PstError, match="a later import tries again"):
        list(items_in_child(tmp_path / "a.pst", 5, dying_reader))


def test_dying_after_the_last_item_is_finished(tmp_path: Path) -> None:
    """Review fault 15: a child that dies while closing the file after its last item made phantom failures (or a refusal)."""
    found = list(items_in_child(tmp_path / "a.pst", 0, dying_at_close_reader))
    assert _names(found) == [(number, f"item {number}") for number in range(TOTAL)]


def test_a_file_that_cannot_be_opened(tmp_path: Path) -> None:
    with pytest.raises(PstError, match="not a data file"):
        list(items_in_child(tmp_path / "other.pst", 0, refusing_reader))


def test_store(tmp_path: Path) -> None:
    assert store_in_child(tmp_path / "a.pst", good_store) == PstStore("Personal Folders", ())
    with pytest.raises(PstError, match="not a data file"):
        store_in_child(tmp_path / "other.pst", refusing_store)
    with pytest.raises(PstError, match="damaged"):
        store_in_child(tmp_path / "broken.pst", crashing_store)


def slow_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    """Works long on item 1 (as when it looks through many contacts for a group) while saying it is alive."""
    if progress is not None:
        progress.total(3)
    for number in range(start, 3):
        if progress is not None:
            progress.at(number, FOLDER)
            if number == 1:
                for _ in range(8):
                    time.sleep(0.5)
                    progress.alive()
        yield number, PstGroup(FOLDER, f"item {number}")


def test_long_work_that_says_it_is_alive_is_not_a_hang(tmp_path: Path) -> None:
    """Second review fault 7: building the contact index sent nothing, so the silence limit took it for a hang and killed it."""
    found = list(items_in_child(tmp_path / "a.pst", 0, slow_reader, seconds=2.0))
    assert _names(found) == [(0, "item 0"), (1, "item 1"), (2, "item 2")]


def endless_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    """Says it is alive on item 1 for ever."""
    if progress is not None:
        progress.total(3)
    for number in range(start, 3):
        if progress is not None:
            progress.at(number, FOLDER)
            while number == 1:
                time.sleep(0.2)
                progress.alive()
        yield number, PstGroup(FOLDER, f"item {number}")


def test_signs_of_life_do_not_stretch_an_item_for_ever(tmp_path: Path) -> None:
    """Third review fault 3: an item that only says it is alive is stopped at its own limit and counts as failed."""
    found = list(items_in_child(tmp_path / "a.pst", 0, endless_reader, seconds=2.0, item_seconds=2.0))
    assert [number for number, _ in found] == [0, 1, 2]
    assert isinstance(found[1][1], PstFailure)


def partial_contacts_reader(path: Path, start: int, progress: Progress | None) -> Iterator[tuple[int, PstItem]]:
    """Says the contacts were looked through only in part in every child (as each child builds its own index), and dies on item 2 the
    first time (the marker file tells the next child it already did)."""
    from chrono_mail.pst.reading import CONTACTS_PARTIAL

    if progress is not None:
        progress.total(5)
    said = False
    for number in range(start, 5):
        if progress is not None:
            progress.at(number, FOLDER)
        if number == 2 and not path.exists():
            path.write_bytes(b"died once")
            os._exit(7)
        yield number, PstGroup(FOLDER, f"item {number}")
        if not said:
            said = True
            yield number, PstFailure(FOLDER, CONTACTS_PARTIAL)


def test_contacts_looked_through_in_part_is_said_once(tmp_path: Path) -> None:
    """Final review: after a new child, the note on the contacts came a second time under another number."""
    from chrono_mail.pst.reading import CONTACTS_PARTIAL

    found = list(items_in_child(tmp_path / "marker", 0, partial_contacts_reader))
    assert [item.what for _, item in found if isinstance(item, PstFailure)].count(CONTACTS_PARTIAL) == 1
    assert sorted({number for number, _ in found}) == [0, 1, 2, 3, 4]
