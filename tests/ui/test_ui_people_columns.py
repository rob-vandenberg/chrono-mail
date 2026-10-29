import json
from datetime import UTC, datetime
from typing import override

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import MessageRow
from chrono_mail.ui import messagecolumns
from chrono_mail.ui.columns import cell_text, valid_columns
from chrono_mail.ui.columnsdialog import ColumnPicker
from chrono_mail.ui.columnsetup import read_setups
from tests.ui.world import World, settle

# --- Version ------------------------------------------------------------
__version__ = "test_ui_people_columns.py 4.2.421"

# --- Version history ----------------------------------------------------
# v4.2.421: one From and one To column, a round choice of how each shows (Don't show, Name and email, Name only, Email only, D-291)
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


def _choices(menu: QMenu) -> list[tuple[str, bool]]:
    return [(action.text(), action.isChecked()) for action in menu.actions()]


def test_from_and_to_are_one_column_each_with_a_round_choice_of_how_it_shows(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    menu = _menu(world, monkeypatch)
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels.count("From") == 1 and labels.count("To") == 1 and "Account" in labels
    from_menu, to_menu = _submenu(menu, "From"), _submenu(menu, "To")
    assert _choices(from_menu) == [("Don't show", False), ("Name and email", False), ("Name only", True), ("Email only", False)]
    assert _choices(to_menu) == [("Don't show", True), ("Name and email", False), ("Name only", False), ("Email only", False)]
    for submenu in (from_menu, to_menu):
        group = submenu.actions()[0].actionGroup()
        assert group is not None and group.isExclusive() and all(action.actionGroup() is group for action in submenu.actions())  # round choices
    assert _entry(menu, "From").isChecked() and not _entry(menu, "To").isChecked()  # incoming mail shows From (the name), not To
    _entry(to_menu, "Name only").trigger()
    columns = world.window.list_model.columns
    assert columns[-1] == "to_names" and world.window.list_model.headerData(len(columns) - 1, Qt.Orientation.Horizontal) == "To"
    menu = _menu(world, monkeypatch)
    assert _entry(menu, "To").isChecked() and _choices(_submenu(menu, "To"))[2] == ("Name only", True)
    _entry(_submenu(menu, "To"), "Don't show").trigger()
    assert not {"to", "to_names", "to_addresses"} & set(world.window.list_model.columns)
    assert not _entry(_menu(world, monkeypatch), "To").isChecked()


def test_another_way_takes_the_place_width_and_sort_of_the_one_shown(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    window = world.window
    window.messages.set_sort("sender", True)
    before = window.list_model.columns
    layout = window.columns._column_layout  # noqa: SLF001
    assert layout is not None and "from" in layout.shares
    share = layout.shares["from"]
    _entry(_submenu(_menu(world, monkeypatch), "From"), "Email only").trigger()
    after = window.list_model.columns
    assert after.index("from_address") == before.index("from") and "from" not in after and len(after) == len(before)
    layout = window.columns._column_layout  # noqa: SLF001
    assert layout is not None and layout.shares.get("from_address") == share and "from" not in layout.shares
    assert window.messages.sort == "sender_address" and window.messages.oldest_first
    assert window.list_model.headerData(after.index("from_address"), Qt.Orientation.Horizontal) == "From"


def test_dont_show_is_greyed_for_the_last_column(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    world.window.columns.set_columns(("from_full",))
    from_menu = _submenu(_menu(world, monkeypatch), "From")
    assert not from_menu.actions()[0].isEnabled() and from_menu.actions()[1].isChecked()
    assert _submenu(_menu(world, monkeypatch), "To").actions()[0].isEnabled()


def test_a_saved_list_keeps_one_way_of_from_and_of_to_the_leftmost() -> None:
    assert valid_columns(["subject", "from_address", "from", "to_names", "from_full", "to", "date"]) == ("subject", "from_address", "to_names", "date")


def test_the_column_lists_have_one_from_row_and_one_to_row_the_way_shown(qtbot: QtBot) -> None:
    picker = ColumnPicker(("from_address", "subject"))
    qtbot.addWidget(picker)
    rows = [(picker.list.item(row).text(), picker.list.item(row).data(Qt.ItemDataRole.UserRole)) for row in range(picker.list.count())]
    assert [text for text, _ in rows].count("From") == 1 and [text for text, _ in rows].count("To") == 1
    assert ("From", "from_address") in rows and ("To", "to") in rows  # From as it shows; To, not shown, its standard way
    assert picker.columns() == ("from_address", "subject")


def test_sorted_by_has_one_sender_and_one_to_sorting_by_what_the_column_shows(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    window = world.window
    labels = [action.text() for action in window.messages.sort_menu.actions()]
    assert not {"From (name and email)", "From (email)", "To (name)", "To (email)"} & set(labels)
    _entry(_submenu(_menu(world, monkeypatch), "From"), "Email only").trigger()
    _entry(window.messages.sort_menu, "Sender").trigger()
    assert window.messages.sort == "sender_address" and window.messages.sort_button.text() == "Sorted by Sender ▾"
    assert _entry(window.messages.sort_menu, "Sender").isChecked()


def test_a_saved_setup_sorted_by_a_way_it_no_longer_shows_sorts_by_the_way_it_shows() -> None:
    setups = read_setups(json.dumps({"inbox": {"columns": ["from", "from_full", "subject"], "sort": "sender_full", "oldest_first": True, "type": "incoming"}}))
    setup, _ = setups["inbox"]
    assert setup.columns == ("from", "subject") and setup.sort == "sender"


def test_a_saved_sort_of_from_or_to_follows_the_way_shown() -> None:
    def sort_of(columns: list[str], sort: str) -> str:
        found = read_setups(json.dumps({"f": {"columns": columns, "sort": sort, "oldest_first": False, "type": "incoming"}}))
        return found["f"][0].sort
    assert sort_of(["from_full", "subject"], "sender") == "sender_full" and sort_of(["from", "subject"], "sender_address") == "sender"
    assert sort_of(["to_addresses", "subject"], "to") == "to_addresses" and sort_of(["subject"], "to_names") == "to_names"
    assert sort_of(["from_to", "from_address", "from", "subject"], "sender") == "sender"  # From / To shows it
    assert sort_of(["to_names", "to", "subject"], "to") == "to_names" and sort_of(["from", "from_full"], "date") == "date"


def test_showing_from_keeps_a_sender_sort_in_its_way(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    window = world.window
    _entry(_submenu(_menu(world, monkeypatch), "From"), "Don't show").trigger()
    _entry(window.messages.sort_menu, "Sender").trigger()
    assert window.messages.sort == "sender"
    _entry(_submenu(_menu(world, monkeypatch), "From"), "Email only").trigger()
    assert window.messages.sort == "sender_address" and _entry(window.messages.sort_menu, "Sender").isChecked()
