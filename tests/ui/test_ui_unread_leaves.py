from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, QModelIndex, QSettings, Qt
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import MessageRow
from chrono_mail.ui import forms
from chrono_mail.ui.prefs import read_preferences
from chrono_mail.ui.models import KIND_ROLE
from tests.ui.world import World, account_node, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_unread_leaves.py 4.4.560"

# --- Version history ----------------------------------------------------
# v4.4.560: a click on the unread mark follows the setting as Mark as read does, and the selection stays when another message is clicked (D-319)
# v4.2.396: Remove messages from Unread when I mark them as read (D-282): off by default; on, a message marked as read leaves a list of
#           unread mail as with Junk, also when the window waits for the server; Undo brings it back, Redo takes it out again, also one read
#           already; a sort does not bring it back; one read by showing it stays


def started(world: World, qtbot: QtBot, leaves: bool, server: bool = False) -> None:
    """The window with the three unread messages of the Inbox; leaves: the setting on; server: actions wait for the server's word (IMAP)."""
    window = world.window
    window.settings.setValue("mark_read", "never")  # only the person's own Mark as read counts here
    window.settings.setValue("unread_leaves_on_read", leaves)
    window.apply_preferences()
    if server:
        window.mail_state.confirmed_by_server = lambda account: True  # type: ignore[method-assign]  # as an IMAP account: rows leave with their fact
    window.start()
    settle(qtbot, world, 3)


def show_unread(world: World, qtbot: QtBot) -> None:
    window = world.window
    top = window.tree_model.index(0, 0)
    unread = next(window.tree_model.index(row, 0, top) for row in range(window.tree_model.rowCount(top)) if window.tree_model.index(row, 0, top).data() == "Unread")
    window.tree.setCurrentIndex(unread)
    wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.combined == "unread" and window.list_model.rowCount() == 3)
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001


def message_at(world: World, row: int) -> str:
    found = world.window.list_model.row_at(row)
    assert found is not None
    return found.message_id


def select_row(world: World, qtbot: QtBot, row: int) -> str:
    """Select the message on this row of the list (it opens in the reading pane); its id."""
    window = world.window
    window.list_view.setCurrentIndex(window.list_model.index(row, 0))
    message_id = message_at(world, row)
    wait(qtbot, lambda: window.reading.shown is not None and window.reading.shown[2] == message_id)  # noqa: SLF001
    return message_id


def selected(world: World) -> list[str]:
    return [message_at(world, index.row()) for index in world.window.list_view.selectionModel().selectedRows()]


def read_in_store(world: World, message_id: str) -> bool:
    return any(row.message_id == message_id and "seen" in row.flags for row in world.call("message.list", account=world.account_id, folder="Inbox").messages)


def test_the_setting_is_off_by_default_and_on_the_message_list_page(tmp_path: Path) -> None:
    assert read_preferences(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)).unread_leaves_on_read is False
    text = forms.FORMS_DIRECTORY.joinpath("settings_message_list.ui").read_text(encoding="utf-8")
    assert 'name="unreadLeavesOnRead"' in text and "Remove messages from Unread when I mark them as read" in text


def test_without_the_setting_a_message_marked_as_read_stays_in_unread(world: World, qtbot: QtBot) -> None:
    started(world, qtbot, leaves=False)
    show_unread(world, qtbot)
    chosen = select_row(world, qtbot, 1)
    world.window.act_read.trigger()
    wait(qtbot, lambda: world.backend._pending == {} and read_in_store(world, chosen))  # noqa: SLF001
    model = world.window.list_model
    assert model.find(chosen) == 1 and model.message_count() == 3 and selected(world) == [chosen]  # only the bold goes (D-149)


@pytest.mark.parametrize("server", [False, True], ids=["here", "server"])
def test_with_the_setting_a_message_marked_as_read_leaves_unread_and_undo_brings_it_back(world: World, qtbot: QtBot, server: bool) -> None:
    started(world, qtbot, leaves=True, server=server)
    show_unread(world, qtbot)
    model = world.window.list_model
    chosen = select_row(world, qtbot, 1)
    below = message_at(world, 2)
    world.window.act_read.trigger()
    wait(qtbot, lambda: model.find(chosen) < 0 and world.backend._pending == {})  # noqa: SLF001
    assert model.message_count() == 2 and selected(world) == [below] and read_in_store(world, chosen)  # the next one below, as with Junk
    world.window.act_undo.trigger()
    wait(qtbot, lambda: model.find(chosen) == 1 and world.backend._pending == {})  # noqa: SLF001 - back at its place
    wait(qtbot, lambda: selected(world) == [chosen])
    assert model.message_count() == 3 and not read_in_store(world, chosen)
    world.window.act_read.trigger()  # and once more: it leaves again
    wait(qtbot, lambda: model.find(chosen) < 0 and model.message_count() == 2 and world.backend._pending == {})  # noqa: SLF001


def test_toggle_read_to_read_leaves_unread_and_the_last_message_selects_nothing(world: World, qtbot: QtBot) -> None:
    started(world, qtbot, leaves=True)
    show_unread(world, qtbot)
    model = world.window.list_model
    last = select_row(world, qtbot, 2)
    world.window.act_toggle_read.trigger()
    wait(qtbot, lambda: model.find(last) < 0 and world.backend._pending == {})  # noqa: SLF001
    assert model.message_count() == 2 and selected(world) == []  # after the last message nothing is selected (D-266)


def click_unread_mark(world: World, qtbot: QtBot, row: int) -> None:
    window = world.window
    window.resize(1200, 600)
    window.show()
    qtbot.wait(50)
    rect = window.list_view.visualRect(window.list_model.index(row, window.list_model.columns.index("status")))
    qtbot.mouseClick(window.list_view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def test_a_click_on_the_unread_mark_with_the_setting_on_takes_the_message_out_and_selects_the_next(world: World, qtbot: QtBot) -> None:
    started(world, qtbot, leaves=True)
    show_unread(world, qtbot)
    model = world.window.list_model
    clicked = select_row(world, qtbot, 1)
    following = message_at(world, 2)
    click_unread_mark(world, qtbot, 1)
    wait(qtbot, lambda: model.find(clicked) < 0 and world.backend._pending == {})  # noqa: SLF001
    assert model.message_count() == 2 and selected(world) == [following]


@pytest.mark.parametrize("server", [False, True])
def test_a_click_on_the_unread_mark_of_another_message_leaves_the_selection_where_it_is(world: World, qtbot: QtBot, server: bool) -> None:
    started(world, qtbot, leaves=True, server=server)
    show_unread(world, qtbot)
    model = world.window.list_model
    chosen = select_row(world, qtbot, 0)
    clicked = message_at(world, 2)
    click_unread_mark(world, qtbot, 2)
    wait(qtbot, lambda: model.find(clicked) < 0 and world.backend._pending == {})  # noqa: SLF001
    assert selected(world) == [chosen] and world.window.reading.shown is not None and world.window.reading.shown[2] == chosen  # noqa: SLF001


@pytest.mark.parametrize("server", [False, True])
def test_a_click_on_the_unread_mark_with_nothing_selected_selects_and_opens_nothing(world: World, qtbot: QtBot, server: bool) -> None:
    started(world, qtbot, leaves=True, server=server)
    show_unread(world, qtbot)
    window, model = world.window, world.window.list_model
    window.list_view.selectionModel().clear()
    window.list_view.setCurrentIndex(QModelIndex())
    clicked = message_at(world, 0)
    shown_before = window.reading.shown  # noqa: SLF001
    click_unread_mark(world, qtbot, 0)
    wait(qtbot, lambda: model.find(clicked) < 0 and world.backend._pending == {})  # noqa: SLF001
    assert selected(world) == [] and not window.list_view.currentIndex().isValid() and window.reading.shown == shown_before  # noqa: SLF001


@pytest.mark.parametrize("server", [False, True])
def test_a_click_on_the_unread_mark_of_a_message_deselected_with_ctrl_selects_and_opens_nothing(world: World, qtbot: QtBot, server: bool) -> None:
    started(world, qtbot, leaves=True, server=server)
    show_unread(world, qtbot)
    window, model = world.window, world.window.list_model
    clicked = select_row(world, qtbot, 1)
    window.list_view.selectionModel().clearSelection()  # as Ctrl+click on it: nothing selected, the row is still the current one
    assert window.list_view.currentIndex().row() == 1
    click_unread_mark(world, qtbot, 1)
    wait(qtbot, lambda: model.find(clicked) < 0 and world.backend._pending == {})  # noqa: SLF001
    qtbot.wait(50)
    assert selected(world) == [] and not window.list_view.currentIndex().isValid() and window.reading.shown is None  # noqa: SLF001


@pytest.mark.parametrize("server", [False, True])
def test_a_click_on_the_unread_mark_of_the_current_row_that_is_not_selected_keeps_the_selected_message(world: World, qtbot: QtBot, server: bool) -> None:
    started(world, qtbot, leaves=True, server=server)
    show_unread(world, qtbot)
    window, model = world.window, world.window.list_model
    chosen = select_row(world, qtbot, 0)
    clicked = message_at(world, 1)
    window.list_view.selectionModel().setCurrentIndex(model.index(1, 0), QItemSelectionModel.SelectionFlag.NoUpdate)  # as Ctrl+click on it twice
    click_unread_mark(world, qtbot, 1)
    wait(qtbot, lambda: model.find(clicked) < 0 and world.backend._pending == {} and window.reading.shown is not None and window.reading.shown[2] == chosen)  # noqa: SLF001
    assert selected(world) == [chosen]


def test_a_message_still_opening_does_not_appear_after_a_click_took_it_out(world: World, qtbot: QtBot) -> None:
    started(world, qtbot, leaves=True, server=True)
    show_unread(world, qtbot)
    window, model = world.window, world.window.list_model
    clicked = message_at(world, 1)
    window.resize(1200, 600)
    window.show()
    qtbot.wait(50)
    rect = window.list_view.visualRect(model.index(1, model.columns.index("status")))
    window.list_view.setCurrentIndex(model.index(1, 0))  # it starts opening; the click comes before its answer can
    window.list_view.selectionModel().clearSelection()
    qtbot.mouseClick(window.list_view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    wait(qtbot, lambda: model.find(clicked) < 0 and world.backend._pending == {})  # noqa: SLF001
    assert window.reading.shown is None  # noqa: SLF001


def test_a_click_on_the_unread_mark_moves_no_selection_on_a_row_still_on_its_way(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(world, qtbot, leaves=True, server=True)
    show_unread(world, qtbot)
    model = world.window.list_model
    chosen = select_row(world, qtbot, 0)
    clicked = message_at(world, 2)
    real_row_at = model.row_at

    def on_its_way(row: int, fetch: bool = False) -> MessageRow | None:
        return None if row == 0 else real_row_at(row, fetch)

    monkeypatch.setattr(model, "row_at", on_its_way)
    click_unread_mark(world, qtbot, 2)
    monkeypatch.undo()
    wait(qtbot, lambda: model.find(clicked) < 0 and world.backend._pending == {})  # noqa: SLF001
    assert selected(world) == [chosen]


def test_a_click_on_the_unread_mark_with_the_setting_off_keeps_the_message_in_unread(world: World, qtbot: QtBot) -> None:
    started(world, qtbot, leaves=False)
    show_unread(world, qtbot)
    model = world.window.list_model
    clicked = message_at(world, 1)
    click_unread_mark(world, qtbot, 1)
    wait(qtbot, lambda: world.backend._pending == {} and "seen" in getattr(model.row_at(1), "flags"))  # noqa: SLF001
    assert model.find(clicked) == 1 and model.message_count() == 3


def test_a_message_read_by_showing_it_stays_in_unread_with_the_setting_on(world: World, qtbot: QtBot) -> None:
    started(world, qtbot, leaves=True)
    world.window.settings.setValue("mark_read", "delay")
    world.window.settings.setValue("mark_read_delay_ms", 0)
    world.window.apply_preferences()
    show_unread(world, qtbot)
    chosen = select_row(world, qtbot, 1)
    wait(qtbot, lambda: world.backend._pending == {} and read_in_store(world, chosen))  # noqa: SLF001
    assert world.window.list_model.find(chosen) == 1 and world.window.list_model.message_count() == 3  # read while reading it: it stays


def test_filter_unread_and_an_account_s_unread_count_as_lists_of_unread_mail(world: World, qtbot: QtBot) -> None:
    started(world, qtbot, leaves=True)
    window, model = world.window, world.window.list_model
    account = account_node(window)
    unread = next(window.tree_model.index(row, 0, account) for row in range(window.tree_model.rowCount(account)) if window.tree_model.index(row, 0, account).data(KIND_ROLE) == "unread")
    window.tree.setCurrentIndex(unread)
    wait(qtbot, lambda: model.spec is not None and model.spec.unread is True and model.spec.combined is None and model.rowCount() == 3)
    assert model.shows_unread_only() and window.messages.reads_away()
    window.tree.setCurrentIndex(window.tree_model.index(0, 0, account))  # the Inbox
    wait(qtbot, lambda: model.spec is not None and model.spec.unread is None and model.rowCount() == 3 and world.backend._pending == {})  # noqa: SLF001
    assert not model.shows_unread_only() and not world.window.messages.reads_away()  # the Inbox itself: a message read stays
    world.window.filters.unread.trigger()
    wait(qtbot, lambda: model.spec is not None and model.spec.unread is True and world.backend._pending == {})  # noqa: SLF001
    assert model.shows_unread_only() and world.window.messages.reads_away()
    chosen = select_row(world, qtbot, 0)
    world.window.act_read.trigger()
    wait(qtbot, lambda: model.find(chosen) < 0 and model.message_count() == 2 and world.backend._pending == {})  # noqa: SLF001


@pytest.mark.parametrize("server", [False, True], ids=["here", "server"])
def test_a_message_read_by_showing_it_leaves_on_mark_as_read_and_undo_and_redo_follow(world: World, qtbot: QtBot, server: bool) -> None:
    """Read while shown, it stays (D-149); Mark as read then takes it out; Undo brings it back as it was (read), Redo takes it out again,
    and a sort afterwards keeps it out."""
    started(world, qtbot, leaves=True, server=server)
    world.window.settings.setValue("mark_read", "delay")
    world.window.settings.setValue("mark_read_delay_ms", 0)
    world.window.apply_preferences()
    show_unread(world, qtbot)
    model = world.window.list_model
    chosen = select_row(world, qtbot, 1)
    wait(qtbot, lambda: world.backend._pending == {} and read_in_store(world, chosen))  # noqa: SLF001
    assert model.find(chosen) == 1
    world.window.act_read.trigger()
    wait(qtbot, lambda: model.find(chosen) < 0 and model.message_count() == 2 and world.backend._pending == {})  # noqa: SLF001
    world.window.list_view.header().sectionClicked.emit(model.columns.index("subject"))  # a sort: it stays out
    wait(qtbot, lambda: model.spec is not None and model.spec.sort == "subject" and world.backend._pending == {})  # noqa: SLF001
    assert model.find(chosen) < 0 and model.message_count() == 2
    world.window.act_undo.trigger()
    wait(qtbot, lambda: model.find(chosen) >= 0 and world.backend._pending == {})  # noqa: SLF001
    wait(qtbot, lambda: selected(world) == [chosen])
    assert model.message_count() == 3 and read_in_store(world, chosen)  # back as it was: read
    world.window.act_redo.trigger()
    wait(qtbot, lambda: model.find(chosen) < 0 and model.message_count() == 2 and world.backend._pending == {})  # noqa: SLF001


def test_redo_takes_out_again_a_message_undo_brought_back_unread(world: World, qtbot: QtBot) -> None:
    started(world, qtbot, leaves=True)
    show_unread(world, qtbot)
    model = world.window.list_model
    chosen = select_row(world, qtbot, 0)
    world.window.act_read.trigger()
    wait(qtbot, lambda: model.find(chosen) < 0 and world.backend._pending == {})  # noqa: SLF001
    world.window.act_undo.trigger()
    wait(qtbot, lambda: model.find(chosen) == 0 and world.backend._pending == {})  # noqa: SLF001
    world.window.act_redo.trigger()
    wait(qtbot, lambda: model.find(chosen) < 0 and model.message_count() == 2 and world.backend._pending == {})  # noqa: SLF001
    assert read_in_store(world, chosen)


@pytest.mark.parametrize("leaves", [False, True], ids=["off", "on"])
def test_a_message_read_while_shown_and_then_archived_stays_away_until_undo(world: World, qtbot: QtBot, leaves: bool) -> None:
    """Kept in Unread while read (D-149), then moved away: it stays away, also after a sort (it came back from Archive); Undo brings it
    back, kept again."""
    started(world, qtbot, leaves=leaves)
    world.window.settings.setValue("mark_read", "delay")
    world.window.settings.setValue("mark_read_delay_ms", 0)
    world.window.apply_preferences()
    show_unread(world, qtbot)
    model = world.window.list_model
    chosen = select_row(world, qtbot, 1)
    wait(qtbot, lambda: world.backend._pending == {} and read_in_store(world, chosen))  # noqa: SLF001
    assert model.find(chosen) == 1
    world.window.act_archive.trigger()
    wait(qtbot, lambda: model.find(chosen) < 0 and world.backend._pending == {})  # noqa: SLF001
    world.window.list_view.header().sectionClicked.emit(model.columns.index("subject"))
    wait(qtbot, lambda: model.spec is not None and model.spec.sort == "subject" and world.backend._pending == {})  # noqa: SLF001
    qtbot.wait(100)
    assert model.find(chosen) < 0 and model.message_count() == 2
    world.window.act_undo.trigger()  # Undo of the Archive: back in the Inbox, so back in the list, kept as before (D-149)
    wait(qtbot, lambda: model.find(chosen) >= 0 and world.backend._pending == {})  # noqa: SLF001
    world.window.list_view.header().sectionClicked.emit(model.columns.index("subject"))  # and a sort keeps it
    wait(qtbot, lambda: model.spec is not None and model.spec.sort == "subject" and world.backend._pending == {})  # noqa: SLF001
    qtbot.wait(100)
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    assert model.find(chosen) >= 0 and model.message_count() == 3


def test_a_kept_read_message_whose_delete_fails_comes_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-266: taken out at the key press, a message whose action fails comes back at its place, also one kept while read (D-149)."""
    from chrono_mail.core import ChronoMailError
    from chrono_mail.core import MessageRef
    from chrono_mail.engine.mailbox import Mailbox

    started(world, qtbot, leaves=False)
    world.window.settings.setValue("mark_read", "delay")
    world.window.settings.setValue("mark_read_delay_ms", 0)
    world.window.apply_preferences()
    show_unread(world, qtbot)
    model = world.window.list_model
    chosen = select_row(world, qtbot, 1)
    wait(qtbot, lambda: world.backend._pending == {} and read_in_store(world, chosen))  # noqa: SLF001

    def refused(self: Mailbox, ref: MessageRef, permanent: bool = False) -> object:
        raise ChronoMailError("The disk said no")

    monkeypatch.setattr(Mailbox, "delete", refused)
    world.window.list_view.setFocus()
    world.window.mail_actions.delete_selected()
    wait(qtbot, lambda: world.backend._pending == {} and model.find(chosen) == 1)  # noqa: SLF001 - back at its place
    assert model.message_count() == 3
