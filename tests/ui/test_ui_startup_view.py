from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import KIND_ROLE
from chrono_mail.ui.sections import CALENDAR, MAIL
from tests.ui.test_ui_tree_rows import account_row, quiet, second_account
from tests.ui.test_ui_window import started
from tests.ui.world import REGISTRY, World, dialogs, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_startup_view.py 0.5.124"

# --- Version history ----------------------------------------------------
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
    choices = world.window._startup_choices()  # noqa: SLF001
    texts = [text for text, _, _, _ in choices]
    assert texts[0] == "Last selected folder" and choices[0][1] == ""
    assert texts[1:9] == [f"Favorites {name}" for name in ("All Inboxes", "Unread", "Flagged", "Outbox", "Sent", "Drafts", "Junk", "Trash")]
    local, calendar, other = texts.index("Local Folders"), texts.index("Calendar"), texts.index("other@example.com")
    assert 9 == local < calendar and texts[calendar : calendar + 3] == ["Calendar", "Contacts", "Tasks"] and calendar + 3 == other
    assert all(not choosable for text, _, choosable, _ in choices if text in ("Local Folders", "other@example.com"))
    assert choices[local + 1][3] == 1 and choices[other + 1][3] == 1  # the folders under their account


def test_a_chosen_folder_opens_at_start_also_after_closing_in_another_section(world: World, qtbot: QtBot) -> None:
    other = second_account(world)
    started(qtbot, world)
    world.window.tree.setCurrentIndex(account_row(world.window, world.account_id))  # last selected: the first account
    again = restarted(world, qtbot, startup_view=f"mail\t{other}\tfolder\tInbox", last_section=CALENDAR)
    assert again._section == MAIL and again._account_id == other and again._folder == "Inbox"  # noqa: SLF001
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
    assert again._account_id == world.account_id and again._folder == "Inbox"  # noqa: SLF001
    assert str(again.tree.currentIndex().data(KIND_ROLE)) == "folder"
    again.close()


def test_last_selected_folder_is_what_it_was(world: World, qtbot: QtBot) -> None:
    other = second_account(world)
    started(qtbot, world)
    world.window.tree.setCurrentIndex(account_row(world.window, other))
    quiet(qtbot, world)
    again = restarted(world, qtbot, startup_view="")
    assert again._account_id == other  # noqa: SLF001
    again.close()
