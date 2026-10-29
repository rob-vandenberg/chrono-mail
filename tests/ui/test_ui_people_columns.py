from datetime import UTC, datetime
from typing import override

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import MessageRow
from chrono_mail.ui import messagecolumns
from chrono_mail.ui.columns import cell_text
from tests.ui.world import World, settle

# --- Version ------------------------------------------------------------
__version__ = "test_ui_people_columns.py 4.2.417"

# --- Version history ----------------------------------------------------
# v4.2.417: Initial: the people columns and the header menu's From and To submenus (D-291), the Account column (D-292)

ROW: MessageRow = MessageRow(
    "1" * 32, "Inbox", "Plans", "Ann Archer <ann@example.org>", datetime(2026, 3, 4, tzinfo=UTC), 10, (), False, "", "Bob <bob@example.org>, carol@example.org",
    sender_address="ann@example.org", recipient_names="Bob, carol@example.org", recipient_addresses="bob@example.org, carol@example.org", received_by="Work",
)


def test_each_people_column_shows_its_part_and_account_the_receiving_account() -> None:
    assert cell_text("from", ROW, False) == "Ann Archer" and cell_text("from_full", ROW, False) == "Ann Archer <ann@example.org>"
    assert cell_text("from_address", ROW, False) == "ann@example.org" and cell_text("to", ROW, False) == "Bob <bob@example.org>, carol@example.org"
    assert cell_text("to_names", ROW, False) == "Bob, carol@example.org" and cell_text("to_addresses", ROW, False) == "bob@example.org, carol@example.org"
    assert cell_text("account", ROW, False) == "Work"


class _LookedAt(QMenu):
    """The header's menu, looked at and never waited on (AGENTS.md 4.5.5): Qt's own exec cannot be replaced on QMenu itself."""

    shown: list[QMenu] = []

    @override
    def exec(self, *where: object) -> None:  # pyrefly: ignore[bad-override] - shown, not run
        _LookedAt.shown.append(self)


def _menu(world: World, monkeypatch: pytest.MonkeyPatch) -> QMenu:
    monkeypatch.setattr(messagecolumns, "QMenu", _LookedAt)
    world.window.columns.header_menu(QPoint(0, 0))
    return _LookedAt.shown.pop()


def _entry(menu: QMenu, text: str) -> QAction:
    return next(action for action in menu.actions() if action.text() == text)


def _submenu(menu: QMenu, text: str) -> QMenu:
    found = _entry(menu, text).menu()
    assert isinstance(found, QMenu)
    return found


def test_from_and_to_are_submenus_of_ticks_and_each_tick_is_a_column(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    menu = _menu(world, monkeypatch)
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert "Account" in labels and not {"To (name)", "To (email)", "From (email)", "From (name and email)"} & set(labels)  # only inside the submenus
    from_entry, to_entry = _entry(menu, "From"), _entry(menu, "To")
    from_menu, to_menu = _submenu(menu, "From"), _submenu(menu, "To")
    assert [(action.text(), action.isChecked()) for action in from_menu.actions()] == [("Name and email", False), ("Name only", True), ("Email only", False)]
    assert from_entry.isChecked() and not to_entry.isChecked()  # incoming mail shows From (the name), not To
    _entry(to_menu, "Name only").trigger()
    assert world.window.list_model.columns[-1] == "to_names"  # no To shown: at the end
    _entry(_submenu(_menu(world, monkeypatch), "From"), "Email only").trigger()
    columns = world.window.list_model.columns
    assert columns[columns.index("from") + 1] == "from_address"  # next to the From already shown
    menu = _menu(world, monkeypatch)
    assert _entry(menu, "To").isChecked() and [action.isChecked() for action in _submenu(menu, "To").actions()] == [False, True, False]
    _entry(menu, "Account").trigger()
    assert world.window.list_model.columns[-1] == "account" and world.window.list_model.headerData(len(columns), Qt.Orientation.Horizontal) == "Account"
