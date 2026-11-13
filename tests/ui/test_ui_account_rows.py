from collections.abc import Callable

from PySide6.QtCore import QModelIndex
from PySide6.QtGui import QFont
import pytest
from PySide6.QtWidgets import QInputDialog, QListWidget, QListWidgetItem, QStyleOptionViewItem
from pytestqt.qtbot import QtBot

from chrono_mail.ui.accountrows import SECOND_LINE_ROLE, TwoLineRows, second_line_font
from chrono_mail.ui.collectionpanel import collection_items
from chrono_mail.ui.sections import CALENDAR
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_account_rows.py 4.4.608"

# --- Version history ----------------------------------------------------
# v4.4.608: Initial tests: an account in Settings > Accounts on two lines, the second smaller, a long name cut; Rename left as shown renames
#           nothing (D-346)


def test_the_second_line_is_smaller_and_the_row_holds_both(qtbot: QtBot) -> None:
    rows = QListWidget()
    qtbot.addWidget(rows)
    painter = TwoLineRows(rows)
    rows.setItemDelegate(painter)
    item = QListWidgetItem("Rob <rob@example.com>")
    item.setData(SECOND_LINE_ROLE, "Google Calendar")
    rows.addItem(item)
    font = QFont(rows.font())
    font.setPointSizeF(10.0)
    assert second_line_font(font).pointSizeF() < font.pointSizeF()
    option = QStyleOptionViewItem()
    option.initFrom(rows)
    setattr(option, "font", font)
    index: QModelIndex = rows.model().index(0, 0)
    one_line = rows.fontMetrics().height()
    assert painter.sizeHint(option, index).height() > one_line  # two lines, never one


def test_a_long_name_is_cut_never_scrolled(qtbot: QtBot) -> None:
    """A review of 4.4.608: the rows are as wide as the list; a long name is cut with …, never a horizontal scrollbar."""
    rows = QListWidget()
    qtbot.addWidget(rows)
    rows.setItemDelegate(TwoLineRows(rows))
    rows.resize(250, 200)
    item = QListWidgetItem("A very long Shown as name <" + "someone" * 10 + "@example.com>")
    item.setData(SECOND_LINE_ROLE, "Mail (IMAP), Calendars and contacts")
    rows.addItem(item)
    rows.show()
    qtbot.waitExposed(rows)
    assert rows.horizontalScrollBar().maximum() == 0


def test_rename_left_as_shown_renames_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """A review of 4.4.608: a calendar named only by an address shows the contact's name; Rename left as shown must not send that name."""
    collections = {summary.kind: summary.collection for summary in world.call("pim.enable")}
    world.call("contact.add", collection=collections["contacts"], name="Bob Stone", email=("bob@example.com",))
    made = world.call("collection.create", kind="events", name="bob@example.com")
    window = world.window
    window.switch_section(CALENDAR)
    panel = window.pages[CALENDAR].panel
    wait(qtbot, lambda: "Bob Stone" in [entry.text(0) for entry in collection_items(panel.tree)])
    asked: list[object] = []

    def ask(*args: object, **kwargs: object) -> None:
        asked.append(args)

    def answer(text: str) -> Callable[..., tuple[str, bool]]:
        def given(*args: object, **kwargs: object) -> tuple[str, bool]:
            return text, True
        return given

    monkeypatch.setattr(panel._backend, "ask", ask)  # noqa: SLF001 - what Rename would send
    monkeypatch.setattr(QInputDialog, "getText", answer("Bob Stone"))
    panel.rename_collection(made.collection)
    assert asked == []  # left as shown: no rename sent
    monkeypatch.setattr(QInputDialog, "getText", answer("Bob"))
    panel.rename_collection(made.collection)
    assert len(asked) == 1  # a name typed is a rename
