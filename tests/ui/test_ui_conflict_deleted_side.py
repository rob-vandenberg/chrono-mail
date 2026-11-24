from dataclasses import replace

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_conflicts import ConflictSummary
from chrono_mail.ui.syncconflicts import ConflictDialog
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_conflict_deleted_side.py 4.8.663"

# --- Version history ----------------------------------------------------
# v4.8.663: Initial (the owner: "We show what we know!"): an item deleted on one side shows every field of the version that remains, "(deleted)"
#           on the other side, and Apply settles it with either side as a whole; a side that could not be read is never called deleted

APPOINTMENT = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\nUID:lunch-1\r\nSUMMARY:Lunch with Mark\r\nLOCATION:Restaurant Het Veer\r\n"
    "DTSTART:20261130T113000Z\r\nDTEND:20261130T123000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)
TASK = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VTODO\r\nUID:hotel-1\r\nSUMMARY:Book the hotel in Antwerp for two nights\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
CONTACT = "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:tom-1\r\nFN:Tom Bakker\r\nN:Bakker;Tom;;;\r\nORG:Bakker Bouw en Onderhoud\r\nEND:VCARD\r\n"
CASES = {"events": (APPOINTMENT, ("Lunch with Mark", "Restaurant Het Veer")), "tasks": (TASK, ("Book the hotel in Antwerp for two nights",)),
         "contacts": (CONTACT, ("Tom Bakker", "Bakker Bouw en Onderhoud"))}


def conflict(kind: str, deleted_here: bool) -> ConflictSummary:
    text = CASES[kind][0]
    reason, words = ("changed_on_server", "Deleted here, changed on the server") if deleted_here else ("deleted_on_server", "Changed here, deleted on the server")
    return ConflictSummary(
        conflict="c1", account="a", account_name="Work", collection="col", collection_name="Things", item="" if deleted_here else "item-1", kind=kind,
        reason=reason, reason_text=words, name="An item", detail="", whole=False, local_allowed=True, when=0.0, fields=(),
        base_text="", local_text="" if deleted_here else text, server_text=text if deleted_here else "",
    )


def column_texts(dialog: ConflictDialog, column: int) -> list[str]:
    """The texts the table shows in one column (1: Chrono Mail, 2: the server), row by row below the header."""
    grid = dialog._grid  # noqa: SLF001
    found: list[str] = []
    for row in range(1, grid.rowCount()):
        item = grid.itemAtPosition(row, column)
        widget = item.widget() if item is not None else None
        found.extend(label.text() for label in ([widget] if isinstance(widget, QLabel) else widget.findChildren(QLabel) if widget is not None else []))
    return found


@pytest.mark.parametrize("kind", ["events", "tasks", "contacts"])
@pytest.mark.parametrize("deleted_here", [True, False], ids=["deleted here", "deleted on the server"])
def test_the_version_that_remains_is_shown_beside_deleted(world: World, qtbot: QtBot, kind: str, deleted_here: bool) -> None:
    dialog = ConflictDialog(conflict(kind, deleted_here))
    qtbot.addWidget(dialog)
    remaining, gone = (2, 1) if deleted_here else (1, 2)
    shown = column_texts(dialog, remaining)
    assert all(any(value in text for text in shown) for value in CASES[kind][1]), shown
    assert column_texts(dialog, gone) and set(column_texts(dialog, gone)) == {"(deleted)"}
    assert dialog.groups == {}  # for reading only: the choice is a whole side


@pytest.mark.parametrize("side", ["local", "server"])
def test_apply_settles_it_with_either_side_as_a_whole(world: World, qtbot: QtBot, side: str) -> None:
    dialog = ConflictDialog(conflict("tasks", True))
    qtbot.addWidget(dialog)
    assert not dialog.apply_button.isEnabled()
    (dialog.all_local if side == "local" else dialog.all_server).click()
    assert dialog.apply_button.isEnabled()
    dialog.apply_button.click()
    assert dialog.side == side and dialog.choices == {}


def test_a_side_that_could_not_be_read_is_never_called_deleted(world: World, qtbot: QtBot) -> None:
    unreadable = replace(conflict("tasks", True), reason="clash", reason_text="Changed on both sides")
    dialog = ConflictDialog(unreadable)
    qtbot.addWidget(dialog)
    assert "(deleted)" not in column_texts(dialog, 1)
