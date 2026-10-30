import json
from datetime import UTC, datetime
from dataclasses import replace
from typing import override

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import MessageRow
from chrono_mail.ui import messagecolumns
from chrono_mail.ui.columns import cell_text, person_address
from chrono_mail.ui.columns import SNOOZED_COLUMN
from chrono_mail.ui.columnsetup import SORT_COLUMNS, ColumnSetup, read_setups, setups_text
from chrono_mail.ui.currentview import CurrentView, EntryKind
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_column_sorts.py 4.2.430"

# --- Version history ----------------------------------------------------
# v4.2.430: From / To shows the person the index keeps; the Contact column takes the first address
# v4.2.429: From / To in Sent sorts by its first recipient as shown (to_shown)
# v4.2.426: Initial: a column sorts by itself (From / To never shares a sort with From, the owner); Favorites > Scheduled, Snoozed and
#           Outbox show their time column; the last column's tick is greyed in the header menu (OR-15); Sorted by's ticked entry changes
#           nothing and Sender sorts by the From / To column where only it shows the sender; a sort saved since is never converted


class _LookedAt(QMenu):
    """The header's menu, looked at and never waited on (AGENTS.md 4.5.5)."""

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


def _arrow_on(world: World) -> str | None:
    """The column the header's sort arrow is on (None: on none)."""
    section = world.window.messages.view.header().sortIndicatorSection()
    columns = world.window.list_model.columns
    return columns[section] if 0 <= section < len(columns) else None


def test_every_sort_belongs_to_one_column() -> None:
    owners = [column for columns in SORT_COLUMNS.values() for column in columns]
    assert len(owners) == len(set(owners)) and SORT_COLUMNS["sender"] == ("from",) and SORT_COLUMNS["from_to"] == ("from_to",)


def test_a_click_on_from_sorts_by_from_and_a_change_of_its_way_takes_the_sort_along(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    window = world.window
    window.columns.set_columns(("from_to", "from", "subject", "date"))
    window.columns.header_clicked(window.list_model.columns.index("from"))
    assert window.messages.sort == "sender" and _arrow_on(world) == "from"
    window.columns.show_people("from", "from_address")
    assert window.messages.sort == "sender_address" and _arrow_on(world) == "from_address"  # never the From / To column
    window.columns.header_clicked(window.list_model.columns.index("from_to"))
    assert window.messages.sort == "from_to" and _arrow_on(world) == "from_to"
    window.columns.show_people("from", "from_full")
    assert window.messages.sort == "from_to" and _arrow_on(world) == "from_to"  # From changed its way: the From / To sort stays


def test_the_from_to_column_sorts_by_what_it_shows(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    messages = world.window.messages
    messages.set_sort("from_to", True)
    assert messages.engine_sort() == "sender" and messages.sort_button.text() == "Sorted by Sender ▾"  # the Inbox shows who sent it
    view = world.window.mail_state.view
    world.window.mail_state.view = replace(view, path="Sent", folder="Sent", folder_role="sent")
    assert messages.engine_sort() == "to_shown"  # Sent shows who it went to: its first recipient (D-295)
    world.window.mail_state.view = view


def test_the_tick_of_the_last_column_is_greyed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    world.window.columns.set_columns(("subject",))
    menu = _menu(world, monkeypatch)
    assert not _entry(menu, "Subject").isEnabled() and _entry(menu, "Subject").isChecked()
    assert _entry(menu, "Size").isEnabled()
    world.window.columns.set_columns(("subject", "size"))
    assert _entry(_menu(world, monkeypatch), "Subject").isEnabled()


@pytest.mark.parametrize(("name", "label"), [("scheduled", "Send at"), ("outbox", "Send at"), ("snoozed", "Snoozed until")])
def test_favorites_scheduled_outbox_and_snoozed_show_their_time(world: World, qtbot: QtBot, name: str, label: str) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    window = world.window
    window.messages.change_view(CurrentView(account="", kind=EntryKind.FAVORITE, key_kind=EntryKind.FAVORITE, path=name, combined=name))
    wait(qtbot, lambda: (shown := window.list_model.spec) is not None and shown.combined == name)
    columns = window.list_model.columns
    assert window.mail_state.in_timed() and SNOOZED_COLUMN in columns and columns == window.columns.columns  # nothing of the list is hidden
    assert window.list_model.headerData(columns.index(SNOOZED_COLUMN), Qt.Orientation.Horizontal) == label
    assert label in [action.text() for action in window.messages.sort_menu.actions()]


def test_a_saved_sort_by_sender_shown_only_by_from_to_is_the_from_to_sort() -> None:
    def sort_of(columns: list[str], sort: str) -> str:
        found = read_setups(json.dumps({"f": {"columns": columns, "sort": sort, "oldest_first": False, "type": "snoozed"}}))
        return found["f"][0].sort
    assert sort_of(["from_to", "subject"], "sender") == "from_to" and sort_of(["from_to", "from_address", "subject"], "sender") == "from_to"
    assert sort_of(["from", "from_to", "subject"], "sender") == "sender" and sort_of(["from_to", "from"], "sender") == "from_to"  # the first showing it
    assert sort_of(["from_full", "subject"], "sender") == "sender_full" and sort_of(["subject"], "sender") == "sender"


def test_sorted_by_sender_sorts_by_from_to_where_only_it_shows_the_sender_and_its_ticked_entry_changes_nothing(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    window = world.window
    window.columns.set_columns(("from_to", "subject", "date"))
    _entry(window.messages.sort_menu, "Sender").trigger()
    assert window.messages.sort == "from_to" and _arrow_on(world) == "from_to"
    window.messages.set_sort("from_to", False)
    _entry(window.messages.sort_menu, "Sender").trigger()  # ticked already: nothing changes, and it stays ticked
    assert window.messages.sort == "from_to" and not window.messages.oldest_first and _arrow_on(world) == "from_to"
    assert _entry(window.messages.sort_menu, "Sender").isChecked()
    window.columns.set_columns(("from_to", "from", "subject", "date"))
    window.columns.header_clicked(window.list_model.columns.index("from_to"))
    _entry(window.messages.sort_menu, "Sender").trigger()  # ticked (From / To shows the sender): the sort stays on the column clicked
    assert window.messages.sort == "from_to" and _arrow_on(world) == "from_to"


def test_from_to_shows_the_recipients_in_favorites_sent_and_drafts_as_in_their_folders(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    messages, state = world.window.messages, world.window.mail_state
    view = state.view
    for name in ("sent", "drafts", "outbox", "scheduled"):
        state.view = CurrentView(account="", kind=EntryKind.FAVORITE, key_kind=EntryKind.FAVORITE, path=name, combined=name)
        assert messages.shows_recipients(), name
    state.view = CurrentView(account="", kind=EntryKind.FAVORITE, key_kind=EntryKind.FAVORITE, path="inboxes", combined="inboxes")
    assert not messages.shows_recipients()
    state.view = view


def test_a_sort_saved_since_every_column_has_its_own_is_never_converted() -> None:
    text = setups_text({"f": (ColumnSetup(("from_to", "subject"), "sender"), "sent")})
    assert json.loads(text)["f"]["format"] == 2 and read_setups(text)["f"][0].sort == "sender"


def test_from_to_shows_the_person_the_index_keeps_and_contact_takes_the_first_address() -> None:
    row = MessageRow(
        "1" * 32, "Sent", "Odd name", "Me <me@example.com>", datetime(2026, 3, 4, tzinfo=UTC), 10, (), False, "",
        "zed@example.org <zed@example.org>, Doe, John <john@example.org>", recipient_addresses="zed@example.org, john@example.org", to_shown="zed@example.org",
    )
    assert cell_text("from_to", row, True) == "zed@example.org"  # never (unknown) for a name that looks like an address (D-295)
    assert person_address(row, True) == "zed@example.org" and person_address(row, False) == "me@example.com"  # its sender, read as before while not filled in


def test_an_escaped_quote_inside_a_quoted_name_never_splits_it() -> None:
    from chrono_mail.core import displayed_address, written_address
    from chrono_mail.ui.addresscompleter import split_last_address
    from chrono_mail.ui.composer import parse_recipients

    written = written_address('Bob "x, y', "p@q.org")
    assert parse_recipients(f"{written}, ann@example.org") is not None and len(parse_recipients(f"{written}, ann@example.org") or []) == 2
    assert split_last_address(f"{written}, an") == (f"{written},", "an")
    assert displayed_address(written_address("Doe, John", "john@example.org")) == "Doe, John <john@example.org>"  # shown without the quotes
