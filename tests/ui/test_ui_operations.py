from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QShowEvent
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.operations import DONE, FAILED, NO_ERRORS, OperationsWindow
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.world import World, menu_named, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_operations.py 0.5.146"

# --- Version history ----------------------------------------------------
# v0.5.146: Every send and receive opens the window (startup, timers, push); only Refresh gives it the focus
# v0.5.145: Initial tests of the Operations window: rows, errors, log, and when it opens and closes


def opened(window: MainWindow) -> list[bool]:
    """Records every time the Operations window opens: True when it takes the keyboard focus, False when it opens quietly."""
    shown: list[bool] = []
    operations = window.operations
    original = operations.showEvent

    def show(event: QShowEvent) -> None:
        shown.append(not operations.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        original(event)

    operations.showEvent = show  # type-checked as an attribute of the instance
    return shown


def idle(qtbot: QtBot, world: World) -> None:
    wait(qtbot, lambda: not world.window._checking and not world.window._sending and world.backend._pending == {})  # noqa: SLF001


def ready(qtbot: QtBot, world: World) -> None:
    """The window has its accounts (a check before that has nothing to check)."""
    world.window.start()
    wait(qtbot, lambda: bool(world.window._accounts))  # noqa: SLF001


def wait_outbox(world: World, subject: str = "Waiting") -> None:
    world.engine.passwords.set(world.account_id, ServerRole.OUTGOING, "wrong")
    world.call("mail.send", account=world.account_id, to="you@example.com", subject=subject, body="x")
    world.engine.passwords.set(world.account_id, ServerRole.OUTGOING, "secret")


def test_refresh_opens_the_window_shows_the_steps_and_hides_it_when_done(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    window = world.window
    shown = opened(window)
    window.send_and_receive()
    idle(qtbot, world)
    rows = window.operations.rows()
    assert list(rows) == [(world.account_id, "receive")]  # the Outbox is empty: no row for sending
    row = rows[(world.account_id, "receive")]
    assert row.name_label.text() == "me@example.com (POP3)" and row.step_label.text() == DONE and row.bar.value() == row.bar.maximum() == 1
    assert row.name_label.font().bold()
    assert shown == [True] and not window.operations.isVisible()  # opened by Refresh with the focus, closed when everything was done
    log = window.operations.log.toPlainText()
    for step in ("Waiting", "Connecting", "Connected", "Listing messages", "Disconnected", DONE):
        assert f"me@example.com (POP3) {step}" in log


def test_the_window_stays_when_it_should_not_hide(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    world.window.settings.setValue("operations_hide_when_done", False)
    world.window.send_and_receive()
    idle(qtbot, world)
    assert world.window.operations.isVisible()


def test_it_does_not_open_on_refresh_when_that_is_off(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    world.window.settings.setValue("operations_show_on_send_receive", False)
    shown = opened(world.window)
    world.window.send_and_receive()
    idle(qtbot, world)
    assert not shown and world.window.operations.rows()  # the rows are there for when it is opened


def test_an_automatic_check_opens_it_without_the_focus(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    shown = opened(world.window)
    world.window.check_mail()  # the timer and the push check this way
    idle(qtbot, world)
    assert shown == [False] and not world.window.operations.isVisible()  # opened quietly, closed when it was done


def test_the_check_at_startup_opens_it_without_the_focus(qtbot: QtBot, world: World) -> None:
    world.window.settings.setValue("check_on_start", True)
    shown = opened(world.window)
    world.window.start()
    wait(qtbot, lambda: bool(shown))
    idle(qtbot, world)
    assert shown == [False] and list(world.window.operations.rows()) == [(world.account_id, "receive")]


def test_an_error_opens_it_and_keeps_it_open(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    world.engine.passwords.set(world.account_id, ServerRole.INCOMING, "wrong")
    world.window.settings.setValue("operations_show_on_send_receive", False)
    world.window.check_mail()  # automatic, and the window does not open for send and receive
    idle(qtbot, world)
    operations = world.window.operations
    assert operations.isVisible()  # the error opened it, and it stays although it hides when done
    row = operations.rows()[(world.account_id, "receive")]
    assert row.failed and row.step_label.text().startswith(f"{FAILED}: ")
    (text,) = operations.error_texts()
    assert row.step_label.text() == f"{FAILED}: {text}"
    assert operations.ignore_all.isEnabled() and operations._errors_stack.currentIndex() == 1  # noqa: SLF001


def test_without_show_on_error_an_error_does_not_open_it_and_does_not_keep_it(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    world.engine.passwords.set(world.account_id, ServerRole.INCOMING, "wrong")
    world.window.settings.setValue("operations_show_on_error", False)
    world.window.settings.setValue("operations_show_on_send_receive", False)
    shown = opened(world.window)
    world.window.check_mail()
    idle(qtbot, world)
    assert not shown and len(world.window.operations.error_texts()) == 1
    world.window.settings.setValue("operations_show_on_send_receive", True)
    world.window.send_and_receive()  # opened by Refresh: it hides when done, the error does not keep it
    idle(qtbot, world)
    assert shown and not world.window.operations.isVisible()


def test_a_send_gets_a_row_only_with_mail_waiting(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    wait_outbox(world)
    world.window.send_and_receive()
    idle(qtbot, world)
    rows = world.window.operations.rows()
    assert set(rows) == {(world.account_id, "send"), (world.account_id, "receive")}
    sending = rows[(world.account_id, "send")]
    assert sending.name_label.text() == "me@example.com (SMTP)" and sending.step_label.text() == DONE
    assert "me@example.com (SMTP) Sending" in world.window.operations.log.toPlainText()
    world.window.send_and_receive()  # the next round: nothing waits any more
    idle(qtbot, world)
    assert set(world.window.operations.rows()) == {(world.account_id, "receive")}


def test_a_failed_send_is_an_error_of_its_account(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    wait_outbox(world)
    world.engine.passwords.set(world.account_id, ServerRole.OUTGOING, "wrong")
    world.window.send_outbox(manual=True)
    idle(qtbot, world)
    operations = world.window.operations
    sending = operations.rows()[(world.account_id, "send")]
    assert sending.failed and operations.isVisible()
    assert operations._errors[0].name_label.text() == "me@example.com (SMTP)"  # noqa: SLF001


def test_ignore_all_copy_log_and_clear_log(qtbot: QtBot) -> None:
    operations = OperationsWindow(lambda account_id, direction: f"{account_id} (POP3)")
    qtbot.addWidget(operations)
    assert not operations.ignore_all.isEnabled() and operations.no_errors.text() == NO_ERRORS
    operations.begin_round()
    operations.add("a", "receive")
    operations.step("a", "receive", "Downloading", 2, 5)
    row = operations.rows()[("a", "receive")]
    assert row.step_label.text() == "Downloading 2 of 5" and (row.bar.value(), row.bar.maximum()) == (2, 5)
    operations.step("b", "receive", "Connecting", None, None)  # an account the window was not told about
    assert list(operations.rows()) == [("a", "receive")]
    operations.finish("a", "receive", "the server went away")
    assert operations.error_texts() == ["the server went away"] and operations.round_errors == 1
    operations.tabs.setCurrentIndex(1)
    assert not operations.ignore_all.isHidden() and operations.copy_log.isHidden()
    operations.ignore_all.click()
    assert operations.error_texts() == [] and not operations.ignore_all.isEnabled()
    operations.tabs.setCurrentIndex(2)
    assert operations.ignore_all.isHidden() and not operations.copy_log.isHidden() and not operations.clear_log.isHidden()
    operations.copy_log.click()
    copied = QGuiApplication.clipboard().text()
    assert "a (POP3) Waiting" in copied and "a (POP3) Downloading" in copied and "a (POP3) Failed: the server went away" in copied
    operations.clear_log.click()
    assert operations.log.toPlainText() == ""
    operations.tabs.setCurrentIndex(0)
    assert operations.ignore_all.isHidden() and operations.copy_log.isHidden() and operations.clear_log.isHidden()


def test_the_tick_box_is_the_setting_of_settings(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    window = world.window
    tick = window.operations.hide_when_done
    assert tick.isChecked() and tick.text() == "Hide when all operations are complete"
    tick.setChecked(False)
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    assert not dialog.operations_hide.isChecked() and dialog.operations_show.isChecked() and dialog.operations_error.isChecked()
    assert [box.text() for box in (dialog.operations_show, dialog.operations_hide, dialog.operations_error)] == [
        "Show window on send and receive", "Hide window when send and receive completes", "Show window when an error occurs",
    ]
    dialog.operations_hide.setChecked(True)
    dialog.save()
    window.apply_preferences()
    assert tick.isChecked()
    assert dialog.values()["operations_show_on_send_receive"] is True and dialog.values()["operations_show_on_error"] is True


def test_view_has_operations(qtbot: QtBot, world: World) -> None:
    ready(qtbot, world)
    view = menu_named(world.window, "View")
    (item,) = [action for action in view.actions() if action.text() == "Operations…"]
    item.trigger()
    assert world.window.operations.isVisible()
    world.window.operations.close()
    assert not world.window.operations.isVisible()
    world.window.progress_bar.clicked.emit()
    assert world.window.operations.isVisible()
