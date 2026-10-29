from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from chrono_mail.ui.advancedsearch import AdvancedSearch, SearchScope
from chrono_mail.ui.columnsdialog import ListChoice
from chrono_mail.ui.columnsetup import (
    SEARCH_KEY, STARTING, ColumnSetup, FolderType, fallback_sort, folder_key, folder_type, read_setups, renamed_key, setup_key, setups_text,
)
from chrono_mail.ui.currentview import CurrentView, EntryKind
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from tests.ui.world import World, menu_named, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_columnsetup.py 4.2.422"

# --- Version history ----------------------------------------------------
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
    assert fallback_sort(("from_to", "to"), "date") == "to" and fallback_sort(("from_to", "size"), "date") == "sender"  # From / To counts as From
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
    context = SettingsContext(tray_available=False, shortcut_actions=window._shortcut_registry(), columns=("to", "subject", "sent"), standard_columns=STARTING["sent"].columns)  # noqa: SLF001
    dialog = SettingsDialog(world.backend, window.settings, context, window)
    assert dialog.chosen_columns() == ("to", "subject", "sent")
    dialog.columns_picker.reset_button.click()  # Standard columns: those of the open kind of folder
    assert dialog.chosen_columns() == STARTING["sent"].columns
    dialog.deleteLater()
