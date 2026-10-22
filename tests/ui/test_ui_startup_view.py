from PySide6.QtCore import QSettings, Qt
from pytestqt.qtbot import QtBot

from chrono_mail.ui.folderpane import FolderPane
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import KIND_ROLE
from chrono_mail.ui.sections import CALENDAR, MAIL
from tests.ui.test_ui_tree_rows import account_row, quiet, second_account
from tests.ui.test_ui_window import started
from tests.ui.world import REGISTRY, World, dialogs, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_startup_view.py 2.0.298"

# --- Version history ----------------------------------------------------
# v2.0.298: the owner's order: All Inboxes, Unread, Outbox, Sent, Drafts, Flagged, Unreplied in Favorites (D-061, D-232)
# v2.0.290: Favorites and the standard folders in the owner's order (D-061, D-232)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.6.261: The start asks each place where mail is kept once and lays the folder list out once (no early build); the tray counts Local Folders once;
#           the window's view is read as window.mail_state.view (R-14)
# v1.1.204: A hidden standard folder of Local Folders is not in the folder list and greyed among the choices
# v1.1.195: A choice has five parts (shown added)
# v1.1.192: The startup choice wins over the last selected folder and the tags; an early build never falls back
# v1.1.191: Favorites in the owner's order with Unreplied
# v1.1.184: The tree is in the drop-down's own popup: Escape and a click outside close it and keep the choice
# v1.1.182: Favorites is a heading with its entries below it (the choices are a folded tree)
# v0.5.124: Initial tests of Settings > General > Show on startup (D-121)


def restarted(world: World, qtbot: QtBot, **values: object) -> MainWindow:
    """The program started again with these settings."""
    world.window.close()
    for key, value in values.items():
        world.window.settings.setValue(key, value)
    world.window.settings.sync()
    again = MainWindow(world.backend, QSettings(str(world.settings_path), QSettings.Format.IniFormat))
    qtbot.addWidget(again)
    again.show()
    again.start()
    wait(qtbot, lambda: again.tree_model.rowCount() > 0 and again.tree.currentIndex().isValid())
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    return again


def test_the_choices_are_in_the_owners_order(world: World, qtbot: QtBot) -> None:
    REGISTRY.run(world.engine, "account.set-storage", {"account": world.account_id, "storage": "local"})  # Local Folders
    second_account(world)  # another account, with its own folders
    world.window.start()
    wait(qtbot, lambda: world.window.tree_model.rowCount() > 0)
    quiet(qtbot, world)
    choices = world.window.folders.startup_choices()  # noqa: SLF001
    texts = [text for text, _, _, _, _ in choices]
    assert texts[0] == "Last selected folder" and choices[0][1] == ""
    assert texts[1] == "Favorites" and not choices[1][2]  # a heading, with its entries below it
    assert texts[2:11] == ["All Inboxes", "Unread", "Outbox", "Sent", "Drafts", "Flagged", "Unreplied", "Junk", "Trash"] and all(choice[3] == 1 for choice in choices[2:11])
    local, calendar, other = texts.index("Local Folders"), texts.index("Calendar"), texts.index("other@example.com")
    assert 11 == local < calendar and texts[calendar : calendar + 3] == ["Calendar", "Contacts", "Tasks"] and calendar + 3 == other
    assert all(not choosable for text, _, choosable, _, _ in choices if text in ("Local Folders", "other@example.com"))
    assert choices[local + 1][3] == 1 and choices[other + 1][3] == 1  # the folders under their account


def test_a_hidden_folder_of_local_folders_is_greyed_among_the_choices(world: World, qtbot: QtBot) -> None:
    REGISTRY.run(world.engine, "account.set-storage", {"account": world.account_id, "storage": "local"})  # Local Folders
    REGISTRY.run(world.engine, "sidebar.show-local-folder", {"folder": "trash", "visible": False})
    world.window.start()
    wait(qtbot, lambda: world.window.tree_model.rowCount() > 0)
    quiet(qtbot, world)
    local = world.window.mail_state.local_id()  # noqa: SLF001
    assert world.window.tree_model.find_item(local, "folder", "Inbox") is not None
    assert world.window.tree_model.find_item(local, "folder", "Trash") is None  # switched off in Settings > Sidebar
    choices = world.window.folders.startup_choices()  # noqa: SLF001
    trash = next(choice for choice in choices if choice[1] == f"mail\t{local}\tfolder\tTrash")
    assert not trash[2] and not trash[4]  # listed, greyed, cannot be chosen
    expected = [("inbox", "Inbox"), ("outbox", "Outbox"), ("sent", "Sent"), ("drafts", "Drafts"), ("junk", "Junk"), ("trash", "Trash")]  # the owner's order (D-232)
    assert world.window.mail_state.local_standard_folders() == expected  # noqa: SLF001  (what exists, in the order of the folder list)


def test_a_chosen_folder_opens_at_start_also_after_closing_in_another_section(world: World, qtbot: QtBot) -> None:
    other = second_account(world)
    started(qtbot, world)
    world.window.tree.setCurrentIndex(account_row(world.window, world.account_id))  # last selected: the first account
    again = restarted(world, qtbot, startup_view=f"mail\t{other}\tfolder\tInbox", last_section=CALENDAR)
    assert again._section == MAIL and again.mail_state.view.account == other and again.mail_state.view.folder == "Inbox"  # noqa: SLF001
    again.close()


def test_a_chosen_section_opens_at_start(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    again = restarted(world, qtbot, startup_view=f"section\t{CALENDAR}", last_section=MAIL)
    assert again._section == CALENDAR  # noqa: SLF001
    again.close()


def test_a_chosen_folder_that_is_gone_falls_back_to_the_last_selected_one(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.close()  # remembers the Inbox of the account as the last selected folder
    again = restarted(world, qtbot, startup_view=f"mail\t{world.account_id}\tfolder\tNo such folder")
    assert again.mail_state.view.account == world.account_id and again.mail_state.view.folder == "Inbox"  # noqa: SLF001
    assert str(again.tree.currentIndex().data(KIND_ROLE)) == "folder"
    again.close()


def test_last_selected_folder_is_what_it_was(world: World, qtbot: QtBot) -> None:
    other = second_account(world)
    started(qtbot, world)
    world.window.tree.setCurrentIndex(account_row(world.window, other))
    quiet(qtbot, world)
    again = restarted(world, qtbot, startup_view="")
    assert again.mail_state.view.account == other  # noqa: SLF001
    again.close()


def test_the_startup_tree_opens_folded_and_a_heading_only_unfolds(world: World, qtbot: QtBot) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    from chrono_mail.ui.startupchooser import VISIBLE_ROWS, StartupChooser

    started(qtbot, world)
    chooser = StartupChooser(world.window.folders.startup_choices())  # noqa: SLF001
    qtbot.addWidget(chooser)
    chooser.show()
    chooser.showPopup()
    tree, model = chooser.tree, chooser.tree_model
    popup = tree.window()
    assert popup is not tree and popup.isVisible()  # the drop-down's own popup, which holds the tree
    assert tree.height() <= VISIBLE_ROWS * tree.sizeHintForRow(0) + 2 * tree.frameWidth() + 4
    favorites = model.index(1, 0)
    assert favorites.data() == "Favorites" and not tree.isExpanded(favorites)  # only the top level at first
    centre = tree.visualRect(favorites).center()
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre)
    assert tree.isExpanded(favorites) and popup.isVisible() and chooser.currentText() == "Last selected folder"  # a heading only unfolds
    unread = model.index(1, 0, favorites)  # Unread after All Inboxes in Favorites (the owner, 2.0.298)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, tree.visualRect(unread).center())
    wait(qtbot, lambda: not popup.isVisible())
    assert chooser.currentText() == "Unread" and str(chooser.currentData()).startswith("mail\t")
    chooser.showPopup()
    assert tree.isExpanded(favorites) and tree.currentIndex() == unread  # open down to the choice
    QTest.keyClick(tree, Qt.Key.Key_Escape)  # Escape closes it and keeps the choice
    wait(qtbot, lambda: not popup.isVisible())
    assert chooser.currentText() == "Unread"
    chooser.showPopup()
    QTest.mouseClick(popup, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(-20, -20))  # a click outside it
    wait(qtbot, lambda: not popup.isVisible())
    assert chooser.currentText() == "Unread"


def test_the_startup_choice_wins_over_the_last_selected_folder_and_the_tags(world: World, qtbot: QtBot) -> None:
    world.call("tag.create", name="Work", colour="#00aa00")  # a tags answer comes at start too
    started(qtbot, world)
    world.window.close()  # remembers the Inbox of the account as the last selected folder
    again = restarted(world, qtbot, startup_view="mail\t\tfavorite\tunread")
    assert again.tree.currentIndex().data(KIND_ROLE) == "favorite" and again.mail_state.view.combined == "unread"  # noqa: SLF001
    again.close()


def accounts_in_local_folders(world: World) -> list[str]:
    """The world's account and two more, all three keeping their mail in Local Folders (as the owner's 25 POP3 accounts)."""
    found = [world.account_id]
    for number in (2, 3):
        added = REGISTRY.run(world.engine, "account.add", dict(
            display_name=f"Other {number}", email_address=f"other{number}@example.com", username=f"other{number}", password="secret",
            incoming_host="127.0.0.1", incoming_port=world.pop.port, incoming_security="none",
            outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none",
        ))
        found.append(str(getattr(added, "account_id")))
    for account in found:
        REGISTRY.run(world.engine, "account.set-storage", {"account": account, "storage": "local"})
    return found


def asked_at_start(world: World, qtbot: QtBot, monkeypatch: object, **values: object) -> tuple[MainWindow, list[str], list[int]]:
    """The program started again; the folder lists it asked for and the times it laid the folder list out."""
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    folders: list[str] = []
    real_ask = world.backend.ask

    def ask(command: object, parameters: object, *rest: object, **named: object) -> int:
        if getattr(command, "id", "") == "folder.list":
            folders.append(str(getattr(parameters, "account")))
        return real_ask(command, parameters, *rest, **named)  # type: ignore[arg-type]

    monkeypatch.setattr(world.backend, "ask", ask)
    laid_out: list[int] = []
    real_lay_out = FolderPane.lay_out

    def lay_out(self: FolderPane, select_saved: bool = False) -> None:
        laid_out.append(self.model.rowCount())
        real_lay_out(self, select_saved)

    monkeypatch.setattr(FolderPane, "lay_out", lay_out)
    return restarted(world, qtbot, **values), folders, laid_out


def test_the_start_asks_each_place_once_and_lays_the_folder_list_out_once(world: World, qtbot: QtBot, monkeypatch: object) -> None:
    """The owner's start: 25 POP3 accounts keep their mail in Local Folders. Their folders are Local Folders', so the start asks
    Local Folders once (it asked once per account) and lays the folder list out once, so it appears in one step (D-219)."""
    accounts_in_local_folders(world)
    started(qtbot, world)
    local = next(str(getattr(account, "account_id")) for account in world.window.mail_state.accounts if str(getattr(account, "kind")) == "local")  # noqa: SLF001
    again, folders, laid_out = asked_at_start(world, qtbot, monkeypatch, startup_view="mail\t\tfavorite\tunread")
    assert folders == [local]  # only Local Folders: the three accounts keep their mail there
    assert laid_out == [0]  # once, from an empty list
    assert again.tree.currentIndex().data(KIND_ROLE) == "favorite"
    again.close()


def test_a_chosen_folder_that_is_not_there_falls_back_at_the_one_lay_out(world: World, qtbot: QtBot) -> None:
    from PySide6.QtCore import QModelIndex

    started(qtbot, world)
    window = world.window
    window.settings.setValue("startup_view", f"mail\t{world.account_id}\tfolder\tNot there")
    window.settings.setValue("last_folder", "")
    window.tree.setCurrentIndex(QModelIndex())
    window.folders.lay_out(select_saved=True)  # noqa: SLF001
    assert window.tree.currentIndex().isValid()  # the first Inbox


def test_the_tray_counts_the_unread_mail_of_local_folders_once(world: World, qtbot: QtBot, monkeypatch: object) -> None:
    accounts_in_local_folders(world)  # the world's 3 unread messages are now in Local Folders' Inbox
    started(qtbot, world)
    again, _, _ = asked_at_start(world, qtbot, monkeypatch)
    assert again.tray._unread == 3  # noqa: SLF001 - it said 12: the Inbox of Local Folders once for itself and once per account keeping its mail there
    again.close()

