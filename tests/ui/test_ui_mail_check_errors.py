from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.ui.mailchecking import mail_check_line
from chrono_mail.ui.operations import ERRORS_TAB
from chrono_mail.ui.statusreport import CLICK_ERRORS
from tests.ui.world import World, menu_named, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_mail_check_errors.py 4.6.649"

# --- Version history ----------------------------------------------------
# v4.6.649: Initial: the status line after a failed mail check (one account named, more counted), its click to the Errors tab, and
#           Ctrl+Shift+O for the Operations window (D-366)

NO_NAME = "The server's name could not be found (no internet connection?)"


def named(account_id: str) -> str:
    return {"a": "Home", "b": "Work", "c": "Club", "d": "Shop", "e": "Club 2"}[account_id]


def test_one_account_that_failed_is_named() -> None:
    assert mail_check_line([("a", NO_NAME)], named) == f"Mail check: Home: {NO_NAME}"


def test_the_same_error_of_five_accounts_is_said_once_with_the_count() -> None:
    problems: list[tuple[str, str]] = [(account_id, NO_NAME) for account_id in ("a", "b", "c", "d", "e")]
    assert mail_check_line(problems, named) == f"Mail check: 5 accounts: {NO_NAME}"


def test_different_errors_of_more_accounts_each_have_their_count_and_no_names() -> None:
    problems: list[tuple[str, str]] = [("a", NO_NAME), ("b", "Login refused"), ("c", NO_NAME)]
    assert mail_check_line(problems, named) == f"Mail check: 2 accounts: {NO_NAME}; 1 account: Login refused"


def failed_check(qtbot: QtBot, world: World) -> None:
    world.window.start()
    wait(qtbot, lambda: bool(world.window.mail_state.accounts))
    world.window.settings.setValue("operations_show_on_send_receive", False)
    world.window.settings.setValue("operations_show_on_error", False)
    world.engine.passwords.set(world.account_id, ServerRole.INCOMING, "wrong")
    world.window.checking.check_accounts(None)
    wait(qtbot, lambda: world.window.status_label.text().startswith("Mail check: "))


def test_a_click_on_the_error_of_a_mail_check_opens_the_operations_window_at_the_errors_tab(qtbot: QtBot, world: World) -> None:
    failed_check(qtbot, world)
    label, operations = world.window.status_label, world.window.operations
    assert label.text().startswith(f"Mail check: {world.window.mail_state.account_name(world.account_id)}: ")
    assert label.toolTip() == CLICK_ERRORS and label.cursor().shape() == Qt.CursorShape.PointingHandCursor
    operations.close()
    operations.tabs.setCurrentIndex(0)
    qtbot.mouseClick(label, Qt.MouseButton.RightButton)
    assert not operations.isVisible()  # a right-click opens nothing
    qtbot.mouseClick(label, Qt.MouseButton.LeftButton)
    assert operations.isVisible() and operations.tabs.currentIndex() == ERRORS_TAB


def test_another_line_can_not_be_clicked(qtbot: QtBot, world: World) -> None:
    failed_check(qtbot, world)
    world.window.report.show("Mail checked: nothing new")
    label = world.window.status_label
    assert label.toolTip() == "" and label.cursor().shape() != Qt.CursorShape.PointingHandCursor
    world.window.operations.close()
    qtbot.mouseClick(label, Qt.MouseButton.LeftButton)
    assert not world.window.operations.isVisible()


def test_ctrl_shift_o_opens_the_operations_window(qtbot: QtBot, world: World) -> None:
    window = world.window
    window.start()
    wait(qtbot, lambda: bool(window.mail_state.accounts))
    (item,) = [action for action in menu_named(window, "View").actions() if action.text() == "Operations…"]
    assert item is window.act_operations and item.shortcut().toString() == "Ctrl+Shift+O"
    assert window._shortcut_registry()["operations"] == ("Operations…", "Ctrl+Shift+O")  # noqa: SLF001 - Settings > Keyboard lists it
    item.trigger()
    assert window.operations.isVisible()
