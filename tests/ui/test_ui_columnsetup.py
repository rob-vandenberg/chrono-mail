from collections.abc import Callable
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.ui.advancedsearch import AdvancedSearch, SearchScope
from chrono_mail.ui.columnfit import layout_from_pixels
from chrono_mail.ui.columns import BY_ID
from chrono_mail.ui.columnsdialog import ColumnPicker, ColumnsDialog, ListChoice, PickerSetup
from chrono_mail.ui.columnsetup import (
    KIND_NAMES, SEARCH_KEY, STARTING, ColumnSetup, FolderType, fallback_sort, folder_key, folder_type, read_setups, renamed_key, setup_key,
    setups_text,
)
from chrono_mail.ui.currentview import CurrentView, EntryKind
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from tests.ui.world import World, menu_named, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_columnsetup.py 4.2.436"

# --- Version history ----------------------------------------------------
# v4.2.436: Apply to all, Default and Reset in the Message list window and Settings > Message list; every change shown before OK,
#           Cancel puts it all back
# v4.2.426: the From / To column falls back to its own sort, no longer to From's
# v4.2.422: a sort no column shows stays when the columns change or only the layout is switched in the Message list window
# v4.2.413: a search of All folders from a Favorites view has the setup of a search of several folders (D-287)
# v4.2.406: where a search looks is a scope; a search of a folder and its subfolders has the search setup (D-287)
# v3.6.354: Chrono Mail's own folders carry their role in the made-up folder data, as the engine now gives it (CR-36)
# v2.0.280: Initial version: columns and sort per folder, the six kinds of folder, Apply to all folders (B-16)

ACCOUNT = "a" * 32


def folder_view(path: str, role: str = "other") -> CurrentView:
    return CurrentView(account=ACCOUNT, kind=EntryKind.FOLDER, key_kind=EntryKind.FOLDER, path=path, folder=path, folder_role=role)


def favorite_view(name: str) -> CurrentView:
    return CurrentView(account="", kind=EntryKind.FAVORITE, key_kind=EntryKind.FAVORITE, path=name, combined=name)


def test_each_folder_and_view_has_its_kind_and_its_own_key() -> None:
    kinds = {
        folder_view("Inbox", "inbox"): "incoming", folder_view("Projects"): "incoming", folder_view("Archive", "archive"): "incoming",
        folder_view("Trash", "trash"): "incoming", folder_view("Sent", "sent"): "sent", folder_view("Drafts", "drafts"): "drafts",
        folder_view("Outbox", "outbox"): "outbox", folder_view("Scheduled", "scheduled"): "scheduled", folder_view("Snoozed", "snoozed"): "snoozed",
        favorite_view("inboxes"): "incoming", favorite_view("unread"): "incoming", favorite_view("sent"): "sent", favorite_view("drafts"): "drafts",
        favorite_view("outbox"): "outbox", favorite_view("scheduled"): "scheduled", favorite_view("snoozed"): "snoozed", favorite_view("trash"): "incoming",
    }
    for view, kind in kinds.items():
        assert folder_type(view) == kind, view
    pinned = CurrentView(account=ACCOUNT, kind=EntryKind.FOLDER, key_kind=EntryKind.PINLINK, path="Sent", folder="Sent", folder_role="sent")
    assert folder_type(pinned) == "sent" and setup_key(pinned) == setup_key(folder_view("Sent", "sent")) == folder_key(ACCOUNT, "Sent")  # a pin is its folder
    tag = CurrentView(account="", kind=EntryKind.TAGVIEW, key_kind=EntryKind.TAGVIEW, path="Work", combined="tagged", combined_tag="Work")
    assert folder_type(tag) == "incoming" and setup_key(tag) != setup_key(favorite_view("inboxes")) != setup_key(favorite_view("sent"))
    everywhere = CurrentView(account=ACCOUNT, kind=EntryKind.FOLDER, path="Sent", folder="Sent", folder_role="sent", query="x", scope=SearchScope.ALL)
    assert setup_key(everywhere) == SEARCH_KEY and folder_type(everywhere) == "search"  # a search in all folders: one setup of its own (D-288)
    inside = CurrentView(account=ACCOUNT, kind=EntryKind.FOLDER, path="Sent", folder="Sent", folder_role="sent", query="x", scope=SearchScope.SUBFOLDERS)
    assert setup_key(inside) == SEARCH_KEY and folder_type(inside) == "search"  # a folder and its subfolders: several folders, as all folders (D-287)
    in_sent = CurrentView(account=ACCOUNT, kind=EntryKind.FOLDER, path="Sent", folder="Sent", folder_role="sent", advanced=AdvancedSearch(), scope=SearchScope.FOLDER)
    assert setup_key(in_sent) == folder_key(ACCOUNT, "Sent")  # a search in one folder: that folder's setup
    from_favorite = CurrentView(account="", kind=EntryKind.FAVORITE, key_kind=EntryKind.FAVORITE, path="sent", combined="sent", query="x", scope=SearchScope.ALL)
    assert setup_key(from_favorite) == SEARCH_KEY and folder_type(from_favorite) == "search"  # All folders from a Favorites view: every folder (D-287)
    in_favorite = CurrentView(account="", kind=EntryKind.FAVORITE, key_kind=EntryKind.FAVORITE, path="sent", combined="sent", query="x", scope=SearchScope.SUBFOLDERS)
    assert setup_key(in_favorite) == setup_key(favorite_view("sent")) and folder_type(in_favorite) == "sent"  # the view itself


def test_the_sort_falls_back_to_the_first_shown_of_received_sent_subject_to_from_size() -> None:
    assert fallback_sort(("subject", "date"), "date") == "date"  # still shown: stays
    assert fallback_sort(("subject", "sent", "date"), "size") == "date"
    assert fallback_sort(("to", "subject", "sent"), "date") == "sent"
    assert fallback_sort(("from", "to", "subject"), "size") == "subject"
    assert fallback_sort(("from_to", "to"), "date") == "to" and fallback_sort(("from_to", "size"), "date") == "from_to"  # the From / To column's own sort (4.2.426)
    assert fallback_sort(("flag", "folder", "preview"), "date") == "folder"  # else the first text column, never an icon column
    assert fallback_sort(("flag", "status"), "subject") == "subject"  # no text column at all: as it was


def test_kept_setups_go_through_text_and_follow_a_renamed_folder() -> None:
    setups: dict[str, tuple[ColumnSetup, FolderType]] = {folder_key(ACCOUNT, "Inbox"): (ColumnSetup(("subject", "date"), "subject", True, '{"icons": {}, "shares": {}}'), "incoming")}
    assert read_setups(setups_text(setups)) == setups
    damaged = '{"x": {"type": "nonsense", "columns": [], "sort": "date", "oldest_first": false}, "y": 3, "z": {"type": "sent", "columns": ["to"], "sort": "nope", "oldest_first": false}}'
    assert read_setups(damaged) == {} and read_setups("not json") == {} and read_setups("") == {}
    assert renamed_key(folder_key(ACCOUNT, "Projects"), ACCOUNT, "Projects", "Work") == folder_key(ACCOUNT, "Work")
    assert renamed_key(folder_key(ACCOUNT, "Projects/Alpha"), ACCOUNT, "Projects", "Work") == folder_key(ACCOUNT, "Work/Alpha")
    assert renamed_key(folder_key(ACCOUNT, "Projects2"), ACCOUNT, "Projects", "Work") is None and renamed_key(folder_key("b" * 32, "Projects"), ACCOUNT, "Projects", "W") is None


def started(qtbot: QtBot, world: World) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3 and world.backend._pending == {})  # noqa: SLF001


def open_folder(qtbot: QtBot, world: World, path: str) -> None:
    window = world.window
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", path)) is not None)  # noqa: SLF001
    found = window.folders.find_item((world.account_id, "folder", path))  # noqa: SLF001
    assert found is not None
    window.tree.setCurrentIndex(found)
    wait(qtbot, lambda: window.mail_state.view.folder == path and world.backend._pending == {})  # noqa: SLF001


def test_each_folder_keeps_its_own_columns_and_sort(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    assert window.list_model.columns == STARTING["incoming"].columns and window.sort_button.text() == "Sorted by Received ▾"
    window.columns.toggle_column("preview")  # noqa: SLF001
    window.messages.set_sort("subject", True)
    world.call("folder.create", account=world.account_id, folder="Projects")
    open_folder(qtbot, world, "Projects")
    assert window.list_model.columns == STARTING["incoming"].columns and window.messages.sort == "date"  # untouched: the standard of its kind
    open_folder(qtbot, world, "Inbox")
    assert window.list_model.columns == (*STARTING["incoming"].columns, "preview") and (window.messages.sort, window.messages.oldest_first) == ("subject", True)
    assert window.sort_button.text() == "Sorted by Subject ▾"


def test_removing_the_sorted_column_moves_the_sort_to_the_next_shown(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.columns.toggle_column("date")  # noqa: SLF001 - Received went: no Sent either, so Subject
    assert window.messages.sort == "subject" and not window.messages.oldest_first and window.sort_button.text() == "Sorted by Subject ▾"
    wait(qtbot, lambda: window.list_model.spec is not None and window.list_model.spec.sort == "subject")


def test_a_layout_switch_in_the_message_list_window_keeps_a_sort_no_column_shows(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.columns.set_list_layout("cards")
    window.columns.toggle_column("size")  # noqa: SLF001 - no Size column
    window.messages.set_sort("size", False)
    columns, reading = window.columns.columns, window.reading.place
    window.apply_list_choice(ListChoice("columns", columns, reading, window.columns.date_style, "normal"))
    assert window.columns.layout == "columns" and window.messages.sort == "size" and window.sort_button.text() == "Sorted by Size ▾"


def test_a_column_change_keeps_a_sort_no_column_showed_and_moves_one_whose_column_went(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.columns.toggle_column("size")  # noqa: SLF001
    window.messages.set_sort("size", False)
    window.columns.toggle_column("preview")  # noqa: SLF001 - another column: Size was not shown, so it stays
    assert window.messages.sort == "size"
    window.messages.set_sort("subject", True)
    window.columns.toggle_column("subject")  # noqa: SLF001 - the sorted column went: the next shown
    assert window.messages.sort == "date"


def test_apply_to_all_folders_gives_the_setup_to_every_folder_of_its_kind_and_those_made_later(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    world.call("folder.create", account=world.account_id, folder="Projects")
    open_folder(qtbot, world, "Projects")
    window.columns.toggle_column("folder")  # noqa: SLF001 - a setup of its own, overwritten below
    open_folder(qtbot, world, "Inbox")
    window.columns.toggle_column("preview")  # noqa: SLF001
    window.messages.set_sort("size", False)
    action = next(item for item in menu_named(window, "Folder").actions() if item.text() == "Apply to all folders")
    assert action is window.columns.apply_action and action.isEnabled()
    action.trigger()
    wanted = (*STARTING["incoming"].columns, "preview")
    for path in ("Projects", "Trash"):
        open_folder(qtbot, world, path)
        assert window.list_model.columns == wanted and window.messages.sort == "size", path
    world.call("folder.create", account=world.account_id, folder="Later")
    open_folder(qtbot, world, "Later")
    assert window.list_model.columns == wanted and window.messages.sort == "size"  # made after: the kind's setup
    open_folder(qtbot, world, "Sent")
    assert window.list_model.columns == STARTING["sent"].columns and window.messages.sort == "sent"  # another kind: untouched


def test_drafts_call_the_sent_column_saved(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    open_folder(qtbot, world, "Drafts")
    model = window.list_model
    assert model.columns == STARTING["drafts"].columns and window.sort_button.text() == "Sorted by Saved ▾"
    assert model.headerData(model.columns.index("sent"), Qt.Orientation.Horizontal) == "Saved"
    open_folder(qtbot, world, "Sent")
    assert model.headerData(model.columns.index("sent"), Qt.Orientation.Horizontal) == "Sent" and window.sort_button.text() == "Sorted by Sent ▾"


def test_a_renamed_folder_keeps_its_setup_and_a_deleted_one_loses_it(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    world.call("folder.create", account=world.account_id, folder="Projects")
    open_folder(qtbot, world, "Projects")
    window.columns.toggle_column("preview")  # noqa: SLF001
    open_folder(qtbot, world, "Inbox")
    world.call("folder.rename", account=world.account_id, folder="Projects", new_name="Work")
    open_folder(qtbot, world, "Work")
    assert "preview" in window.list_model.columns
    open_folder(qtbot, world, "Inbox")
    world.call("folder.delete", account=world.account_id, folder="Work", include_subfolders=True)
    assert "Work" not in str(window.settings.value("folder_columns"))
    world.call("folder.create", account=world.account_id, folder="Work")
    open_folder(qtbot, world, "Work")
    assert window.list_model.columns == STARTING["incoming"].columns  # a new folder of the same name starts fresh


def test_settings_shows_and_changes_the_columns_of_the_open_folder(world: World, qtbot: QtBot) -> None:
    window = world.window
    choices = PickerSetup(("to", "subject"), STARTING["sent"].columns, KIND_NAMES["sent"])
    context = SettingsContext(tray_available=False, shortcut_actions=window._shortcut_registry(), columns=("to", "subject", "sent"), column_choices=choices)  # noqa: SLF001
    dialog = SettingsDialog(world.backend, window.settings, context, window)
    picker = dialog.columns_picker
    assert picker.columns() == ("to", "subject", "sent")
    picker.reset_button.click()  # Reset: those of the open kind of folder after a fresh install
    assert picker.columns() == STARTING["sent"].columns
    picker.default_button.click()  # Default: those stored for it
    assert picker.columns() == ("to", "subject")
    dialog.deleteLater()


# --- Apply to all, Default and Reset; every change shown before OK (4.2.436, D-104, D-228) ---------------------------------------


def stored_default(world: World, qtbot: QtBot) -> tuple[str, ...]:
    """Inbox gets Preview and the Size sort, given to every folder of received mail by Menu > Folder > Apply to all folders."""
    window = world.window
    window.columns.toggle_column("preview")  # noqa: SLF001
    window.messages.set_sort("size", False)
    window.columns.apply_action.trigger()
    return (*STARTING["incoming"].columns, "preview")


def message_list_window(monkeypatch: pytest.MonkeyPatch, inside: Callable[[ColumnsDialog], bool]) -> None:
    """The Message list window: `inside` does what the person does while it is open and says OK (True) or Cancel (False)."""

    def fake_exec(self: ColumnsDialog) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted if inside(self) else QDialog.DialogCode.Rejected

    monkeypatch.setattr(ColumnsDialog, "exec", fake_exec)


def test_the_three_buttons_name_the_kind_of_folder_and_apply_to_all_greys_when_nothing_is_shown(qtbot: QtBot) -> None:
    picker = ColumnPicker(("to", "subject"), None, PickerSetup(kind_name=KIND_NAMES["search"], can_apply=False))
    qtbot.addWidget(picker)
    assert [picker.apply_button.text(), picker.default_button.text(), picker.reset_button.text()] == ["Apply to all", "Default", "Reset"]
    assert picker.apply_button.toolTip() == "Use this column configuration for all search folders."
    assert picker.default_button.toolTip() == "Load the default configuration for search folders."
    assert picker.reset_button.toolTip() == "Restore the factory default configuration for search folders."
    picker.set_active(True)
    assert not picker.apply_button.isEnabled() and picker.default_button.isEnabled() and picker.reset_button.isEnabled()
    assert KIND_NAMES == {
        "incoming": "received mail folders", "sent": "Sent folders", "drafts": "Drafts folders", "outbox": "Outbox folders",
        "scheduled": "Scheduled folders", "snoozed": "Snoozed folders", "search": "search folders",
    }


def test_default_shows_the_stored_setup_at_once_and_ok_keeps_it(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    wanted = stored_default(world, qtbot)
    world.call("folder.create", account=world.account_id, folder="Projects")
    open_folder(qtbot, world, "Projects")
    window.columns.toggle_column("size")  # noqa: SLF001 - a setup of its own
    window.messages.set_sort("subject", True)
    seen: list[tuple[tuple[str, ...], str, bool]] = []

    def inside(dialog: ColumnsDialog) -> bool:
        dialog.picker.default_button.click()
        seen.append((window.list_model.columns, window.messages.sort, window.messages.oldest_first))  # shown before OK
        return True

    message_list_window(monkeypatch, inside)
    window.act_columns.trigger()
    assert seen == [(wanted, "size", False)]
    assert window.list_model.columns == wanted and window.messages.sort == "size"
    assert "Projects" not in str(window.settings.value("folder_columns"))  # it follows the stored setup again


def test_default_brings_the_stored_widths(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    order = STARTING["incoming"].columns
    widths = layout_from_pixels(order, [BY_ID[column].symbol for column in order], [BY_ID[column].width * 2 for column in order]).to_text()
    window.settings.setValue("type_columns", setups_text({"incoming": (ColumnSetup(order, "subject", True, widths), "incoming")}))
    window.settings.setValue("folder_columns", setups_text({folder_key(world.account_id, "Inbox"): (ColumnSetup(("subject", "date"), "date"), "incoming")}))
    window.columns.take_in_setups()
    started(qtbot, world)
    assert window.list_model.columns == ("subject", "date")
    message_list_window(monkeypatch, lambda dialog: dialog.picker.default_button.click() or True)
    window.act_columns.trigger()
    assert window.columns._current_setup() == ColumnSetup(order, "subject", True, widths)  # noqa: SLF001 - columns, order, widths, sort, direction


def test_reset_gives_the_fresh_install_setup_and_leaves_the_stored_one(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    wanted = stored_default(world, qtbot)
    message_list_window(monkeypatch, lambda dialog: dialog.picker.reset_button.click() or True)
    window.act_columns.trigger()
    assert window.list_model.columns == STARTING["incoming"].columns and (window.messages.sort, window.messages.oldest_first) == ("date", False)
    world.call("folder.create", account=world.account_id, folder="Projects")
    open_folder(qtbot, world, "Projects")
    assert window.list_model.columns == wanted and window.messages.sort == "size"  # the stored setup stays


def test_reset_then_apply_to_all_gives_every_folder_of_its_kind_the_fresh_install_setup(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    stored_default(world, qtbot)

    def inside(dialog: ColumnsDialog) -> bool:
        dialog.picker.reset_button.click()
        dialog.picker.apply_button.click()
        return True

    message_list_window(monkeypatch, inside)
    window.act_columns.trigger()
    for path in ("Trash", "Junk"):
        open_folder(qtbot, world, path)
        assert window.list_model.columns == STARTING["incoming"].columns and window.messages.sort == "date", path
    open_folder(qtbot, world, "Sent")
    assert window.list_model.columns == STARTING["sent"].columns  # another kind: untouched


def saved_setups(world: World) -> tuple[dict[str, tuple[ColumnSetup, FolderType]], dict[str, tuple[ColumnSetup, FolderType]]]:
    settings = world.window.settings
    return read_setups(str(settings.value("folder_columns", ""))), read_setups(str(settings.value("type_columns", "")))


def test_ticks_and_moves_show_at_once_and_cancel_puts_everything_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    before = saved_setups(world)
    seen: list[tuple[str, ...]] = []
    saved: list[tuple[dict[str, tuple[ColumnSetup, FolderType]], dict[str, tuple[ColumnSetup, FolderType]]]] = []

    def inside(dialog: ColumnsDialog) -> bool:
        picker = dialog.picker
        row = next(row for row in range(picker.list.count()) if picker.list.item(row).data(Qt.ItemDataRole.UserRole) == "preview")
        picker.list.item(row).setCheckState(Qt.CheckState.Checked)
        seen.append(window.list_model.columns)
        picker.list.setCurrentRow(1)
        picker.up_button.click()
        seen.append(window.list_model.columns)
        picker.apply_button.click()
        saved.append(saved_setups(world))  # nothing saved before OK
        return False

    message_list_window(monkeypatch, inside)
    window.act_columns.trigger()
    start = STARTING["incoming"].columns
    assert seen == [(*start, "preview"), (start[1], start[0], *start[2:], "preview")] and saved == [before]
    assert window.list_model.columns == start and window.messages.sort == "date" and saved_setups(world) == before
    world.call("folder.create", account=world.account_id, folder="Projects")
    open_folder(qtbot, world, "Projects")
    assert window.list_model.columns == start  # Apply to all was cancelled too


def test_the_layout_choices_of_the_message_list_window_show_at_once_and_cancel_puts_them_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    seen: list[tuple[str, ...]] = []

    def inside(dialog: ColumnsDialog) -> bool:
        dialog.layout_box.setCurrentIndex(dialog.layout_box.findData("cards"))
        seen.append(window.list_model.columns)
        return False

    message_list_window(monkeypatch, inside)
    window.act_columns.trigger()
    assert seen == [("card",)]
    assert window.list_model.columns == STARTING["incoming"].columns and str(window.settings.value("list_layout", "columns")) == "columns"


def test_settings_shows_default_at_once_and_cancel_puts_it_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    wanted = stored_default(world, qtbot)
    world.call("folder.create", account=world.account_id, folder="Projects")
    open_folder(qtbot, world, "Projects")
    window.columns.toggle_column("size")  # noqa: SLF001
    own = window.list_model.columns
    seen: list[tuple[str, ...]] = []

    def try_and_cancel(self: SettingsDialog) -> QDialog.DialogCode:
        self.columns_picker.default_button.click()
        seen.append(window.list_model.columns)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SettingsDialog, "exec", try_and_cancel)
    window.open_settings()
    assert seen == [wanted] and window.list_model.columns == own and window.messages.sort == "date"


def test_settings_ok_keeps_a_change_shown_early_and_saves_nothing_before(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    before = saved_setups(world)
    saved: list[tuple[dict[str, tuple[ColumnSetup, FolderType]], dict[str, tuple[ColumnSetup, FolderType]]]] = []

    def tick_and_ok(self: SettingsDialog) -> QDialog.DialogCode:
        picker = self.columns_picker
        row = next(row for row in range(picker.list.count()) if picker.list.item(row).data(Qt.ItemDataRole.UserRole) == "preview")
        picker.list.item(row).setCheckState(Qt.CheckState.Checked)
        saved.append(saved_setups(world))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", tick_and_ok)
    window.open_settings()
    wanted = (*STARTING["incoming"].columns, "preview")
    assert saved == [before] and window.list_model.columns == wanted
    assert saved_setups(world)[0][folder_key(world.account_id, "Inbox")][0].columns == wanted


def test_cancel_after_apply_to_all_brings_back_the_own_setups_of_other_folders(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    world.call("folder.create", account=world.account_id, folder="Projects")
    open_folder(qtbot, world, "Projects")
    window.columns.toggle_column("folder")  # noqa: SLF001 - a setup of its own
    own = window.list_model.columns
    open_folder(qtbot, world, "Inbox")

    def inside(dialog: ColumnsDialog) -> bool:
        dialog.picker.reset_button.click()
        dialog.picker.apply_button.click()
        return False

    message_list_window(monkeypatch, inside)
    window.act_columns.trigger()
    open_folder(qtbot, world, "Projects")
    assert window.list_model.columns == own


def test_the_column_list_follows_another_folder_opened_while_it_is_shown(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    seen: list[tuple[tuple[str, ...], str]] = []

    def inside(dialog: ColumnsDialog) -> bool:
        open_folder(qtbot, world, "Sent")  # the folder shown changes meanwhile (an IMAP folder deleted, an account removed)
        picker = dialog.picker
        seen.append((picker.columns(), picker.default_button.toolTip()))
        picker.reset_button.click()
        return True

    message_list_window(monkeypatch, inside)
    window.act_columns.trigger()
    assert seen == [(STARTING["sent"].columns, "Load the default configuration for Sent folders.")]
    assert window.list_model.columns == STARTING["sent"].columns
    open_folder(qtbot, world, "Inbox")
    assert window.list_model.columns == STARTING["incoming"].columns  # never given the Sent columns


def test_apply_to_all_is_greyed_while_the_list_shows_nothing(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    assert window.columns.picker_setup().can_apply
    window.folders.entry_chosen.emit(replace(window.mail_state.view, account=None, folder=None, combined=None, kind=EntryKind.NONE))
    wait(qtbot, lambda: window.mail_state.view.account is None)
    assert not window.columns.picker_setup().can_apply


def test_a_restore_while_settings_is_open_is_what_cancel_puts_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    restored: dict[str, tuple[ColumnSetup, FolderType]] = {folder_key(world.account_id, "Inbox"): (ColumnSetup(("subject", "date"), "subject", True), "incoming")}

    def restore_then_cancel(self: SettingsDialog) -> QDialog.DialogCode:
        self.columns_picker.reset_button.click()
        window.settings.setValue("folder_columns", setups_text(restored))  # a restore writes the backup's setups and takes them in
        window.columns.take_in_setups()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SettingsDialog, "exec", restore_then_cancel)
    window.open_settings()
    assert saved_setups(world)[0] == restored and window.list_model.columns == ("subject", "date")


def test_a_tick_in_the_outbox_keeps_send_at_in_its_place_and_an_open_list_greys_apply_to_all_with_nothing_shown(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    window = world.window
    open_folder(qtbot, world, "Outbox")
    seen: list[tuple[tuple[str, ...], bool, bool]] = []

    def inside(dialog: ColumnsDialog) -> bool:
        picker = dialog.picker
        row = next(row for row in range(picker.list.count()) if picker.list.item(row).data(Qt.ItemDataRole.UserRole) == "preview")
        picker.list.item(row).setCheckState(Qt.CheckState.Checked)
        columns, before = window.columns.columns, picker.apply_button.isEnabled()
        window.folders.entry_chosen.emit(replace(window.mail_state.view, account=None, folder=None, combined=None, kind=EntryKind.NONE))
        seen.append((columns, before, picker.apply_button.isEnabled()))
        return False

    message_list_window(monkeypatch, inside)
    window.act_columns.trigger()
    assert seen == [((*STARTING["outbox"].columns, "preview"), True, False)]  # Send at kept its place; nothing shown: Apply to all greyed
