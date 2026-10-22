import time
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox, QPushButton
from pytestqt.qtbot import QtBot

from chrono_mail.ui.scheduledpanel import ScheduledPanel
from chrono_mail.ui.whendialog import WhenDialog, presets
from tests.ui.test_ui_composer import fill, new_composer
from chrono_mail.ui.columnsetup import STARTING
from chrono_mail.ui.columns import keep_snoozed_column, with_snoozed_column
from chrono_mail.ui.formatting import coming_date
from tests.ui.test_ui_window import started
from tests.ui.world import World, gone, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_schedule.py 2.0.292"

# --- Version history ----------------------------------------------------
# v2.0.292: Scheduled in the owner's order of the standard folders (D-232)
# v2.0.280: the Snoozed folder starts with its own columns, sorted by Snoozed until soonest first; switched off, its sort falls back to Subject (B-16)
# v1.9.268: R-21: Send now, Cancel snooze, Pause and Resume change only their own line; showing the page again keeps the lines and the selection; a failed Send now says why
# v1.9.266: R-45: the snooze tests wait for the status line itself (it comes with the answer, after the row has left)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.6.260: A row read with row_at (the list has no _message_at, R-15); imports from messagelist.py
# v1.1.208: Edit opens again after a close; the page buttons New, Edit, Send now, Pause; the menu of a Scheduled view; Favorites > Scheduled edits; a drop on Drafts unschedules, other folders refuse (D-178)
# v1.1.207: New and Edit on the Scheduled page; a scheduled message is edited in place, never a draft, from the page and from the folder (D-177)
# v1.1.203: The first button of the Scheduled page says Send now or Cancel snooze; Schedule… and the Scheduled folder in the message window
# v1.1.202: Favorites > Archive and Snoozed appear the moment their folder is made; Favorites > Scheduled: shown once used, on the Sidebar page and in Show on startup, a message moved back to drafts leaves it at once
# v1.1.201: The button that unschedules is called Move back to drafts
# v1.1.200: The Scheduled folder follows the Outbox; its column and sort are Send at
# v1.1.199: No draft is saved while the confirmation of a scheduled message waits for the person
# v1.1.198: New… on the Scheduled page makes a scheduled message in a window that never sends at once; it joins the list in place
# v0.5.167: the toolbar's buttons are found with button_for (the folding toolbar, D-154)
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.5.145: the Refresh menu checks as the person's own choice (manual)
# v0.2.101: Cancel snooze
# v0.2.100: The Snoozed until column of the Snoozed folder, its sorting, groups and the cards
# v0.0.86: The Refresh menu (send and receive), the Snooze button with ready-made times, the Forward menu
# v0.0.67: Repeating messages
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.34: Initial send later and snooze window tests


def real_clock(world: World) -> None:
    """The test engine's clock stands still in 2027; these tests schedule from the real now."""
    world.engine.clock = time.time


def choose_time(monkeypatch: pytest.MonkeyPatch, text: str, accept: bool = True) -> None:
    def execute(self: WhenDialog) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    def chosen(self: WhenDialog) -> str:
        return text

    monkeypatch.setattr(WhenDialog, "exec", execute)
    monkeypatch.setattr(WhenDialog, "chosen", chosen)


def in_hours(hours: float) -> str:
    return (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


def test_the_presets_are_ahead_and_in_order() -> None:
    now = datetime(2026, 10, 7, 10, 30)  # a Wednesday morning
    choices = dict(presets(now))
    assert choices["In an hour"] == datetime(2026, 10, 7, 11, 30) and choices["This evening"] == datetime(2026, 10, 7, 18, 0)
    assert choices["Tomorrow morning"] == datetime(2026, 10, 8, 8, 0) and choices["Monday morning"] == datetime(2026, 10, 12, 8, 0)
    late = dict(presets(datetime(2026, 10, 7, 17, 50)))
    assert "This evening" not in late  # less than half an hour away: not offered
    sunday = dict(presets(datetime(2026, 10, 11, 12, 0)))
    assert sunday["Monday morning"] == datetime(2026, 10, 12, 8, 0)


def test_the_dialog_returns_the_chosen_time(qtbot: QtBot) -> None:
    dialog = WhenDialog("t", "q", "ok", now=datetime(2026, 10, 7, 10, 30))
    qtbot.addWidget(dialog)
    assert dialog.chosen() == "2026-10-08T08:00"
    next(button for button in dialog.preset_buttons if button.text() == "In an hour").click()
    assert dialog.chosen() == "2026-10-07T11:30"


def test_send_later_keeps_the_message_in_the_outbox(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = new_composer(world, qtbot)
    fill(window)
    real_clock(world)
    choose_time(monkeypatch, in_hours(3))
    window.later_button.click()
    wait(qtbot, lambda: gone(window))
    assert world.smtp.received == []
    (info,) = world.call("mail.scheduled")
    assert info.subject == "Hello"


def test_cancelling_the_time_dialog_sends_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = new_composer(world, qtbot)
    fill(window)
    choose_time(monkeypatch, in_hours(3), accept=False)
    window.later_button.click()
    qtbot.wait(200)
    assert window.isVisible() and world.call("mail.scheduled") == ()
    window._finished = True  # noqa: SLF001
    window.close()


def test_the_window_sends_what_is_due_and_wakes_snoozed_mail(world: World, qtbot: QtBot) -> None:
    real_clock(world)
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    target = world.call("message.list", account=world.account_id, folder="Inbox").messages[0]
    world.call("message.snooze", account=world.account_id, folder="Inbox", message=target.message_id, until=in_hours(1))
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Due soon", body="x", send_at=in_hours(1))
    world.engine.clock = lambda: datetime.now().timestamp() + 2 * 3600
    world.window.checking.run_due()
    wait(qtbot, lambda: "1 scheduled message(s) sent, 1 snoozed message(s) back" in world.window.status_label.text())
    assert len(world.smtp.received) == 1


def test_snoozing_from_the_window(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    real_clock(world)
    choose_time(monkeypatch, in_hours(5))
    world.window.act_snooze.trigger()
    # The row leaves at the engine's fact, the status line comes with its answer a moment later: wait for both (R-45).
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2 and "Snoozed 1 message(s)" in world.window.status_label.text())
    assert len(world.call("message.snoozed")) == 1


def test_the_scheduled_window_sends_now(world: World, qtbot: QtBot) -> None:
    real_clock(world)
    first = world.call("mail.send", account=world.account_id, to="you@example.com", subject="One", body="x", send_at=in_hours(4))
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Two", body="x", send_at=in_hours(8))
    dialog = ScheduledPanel(world.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    wait(qtbot, lambda: dialog.list.count() == 2)
    assert "One" in dialog.list.item(0).text() and dialog.now_button.isEnabled()
    dialog.now_button.click()
    wait(qtbot, lambda: dialog.list.count() == 1 and len(world.smtp.received) == 1)
    assert dialog.changed and "Two" in dialog.list.item(0).text() and first.message_id


def test_a_snoozed_message_can_be_brought_back_from_the_window(world: World, qtbot: QtBot) -> None:
    real_clock(world)
    target = world.call("message.list", account=world.account_id, folder="Inbox").messages[0]
    world.call("message.snooze", account=world.account_id, folder="Inbox", message=target.message_id, until=in_hours(6))
    dialog = ScheduledPanel(world.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    wait(qtbot, lambda: dialog.list.count() == 1)
    assert not dialog.edit_button.isEnabled()
    dialog.now_button.click()
    wait(qtbot, lambda: dialog.list.count() == 0)
    assert world.call("message.list", account=world.account_id, folder="Snoozed").total == 0


def test_the_dialog_gives_the_repeat_values_only_when_asked(qtbot: QtBot) -> None:
    plain = WhenDialog("t", "q", "ok", now=datetime(2026, 10, 7, 10, 30))
    qtbot.addWidget(plain)
    assert plain.repeat_values() == {} and plain.repeat_box.isHidden()
    dialog = WhenDialog("t", "q", "ok", now=datetime(2026, 10, 7, 10, 30), repeat=True)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.repeat_values() == {} and not dialog.repeating.isEnabled()
    dialog.repeat_box.setChecked(True)
    dialog.days.setValue(5)
    assert dialog.repeat_values() == {"repeat_months": 0, "repeat_weeks": 0, "repeat_days": 5}
    dialog.until_box.setChecked(True)
    assert str(dialog.repeat_values()["repeat_until"]).startswith("2026-11-06")


def test_a_repeating_message_is_listed_paused_and_resumed_in_the_window(world: World, qtbot: QtBot) -> None:
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Weekly", body="x", send_at=in_hours(4), repeat_days=7)
    dialog = ScheduledPanel(world.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    wait(qtbot, lambda: dialog.list.count() == 1)
    assert "every 7 days" in dialog.list.item(0).text() and dialog.pause_button.isEnabled() and dialog.pause_button.text() == "Pause"
    dialog.pause_button.click()
    wait(qtbot, lambda: "paused" in dialog.list.item(0).text())
    assert dialog.pause_button.text() == "Resume"
    dialog.pause_button.click()
    wait(qtbot, lambda: "sent 0 times" in dialog.list.item(0).text())


def test_refresh_has_a_menu_to_receive_all_or_one_account(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QToolButton

    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    button = world.window.toolbar.button_for(world.window.act_refresh)
    assert isinstance(button, QToolButton) and button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup and button.menu() is world.window.refresh_menu
    checked: list[set[str] | None] = []

    def record(only: set[str] | None, manual: bool = False, new_round: bool = True) -> None:
        checked.append(only)
        assert manual  # the person chose it from the menu: the Operations window may open

    monkeypatch.setattr(world.window.checking, "check_accounts", record)
    world.window._fill_refresh_menu()  # noqa: SLF001
    actions = world.window.refresh_menu.actions()
    assert [action.text() for action in actions] == ["Send and Receive All", "Receive All", "Send All", "", '"me@example.com" only']
    actions[4].trigger()
    actions[1].trigger()
    assert checked == [{world.account_id}, None]


def test_the_snooze_button_offers_ready_made_times(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QToolButton

    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    button = world.window.toolbar.button_for(world.window.act_snooze)
    assert isinstance(button, QToolButton) and button.text() == "Snooze" and button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup and button.menu() is world.window.snooze_menu
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    real_clock(world)
    world.window.mail_actions.fill_snooze_menu(world.window.snooze_menu, world.window.act_cancel_snooze)  # noqa: SLF001
    texts = [action.text() for action in world.window.snooze_menu.actions()]
    assert texts[0].startswith("Tomorrow (") and texts[0].endswith(" 09:00)") and "Next week" in texts[-3] and texts[-2:] == ["", "Custom date and time…"]
    world.window.snooze_menu.actions()[0].trigger()
    # The row leaves at the engine's fact, the status line comes with its answer a moment later: wait for both (R-45).
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2 and "Snoozed 1 message(s)" in world.window.status_label.text())
    assert len(world.call("message.snoozed")) == 1


def test_refresh_sends_what_waits_in_the_outbox_and_then_receives(world: World, qtbot: QtBot) -> None:
    from chrono_mail.config import ServerRole

    world.window.start()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.engine.passwords.set(world.account_id, ServerRole.OUTGOING, "wrong")
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Waiting", body="x")
    world.engine.passwords.set(world.account_id, ServerRole.OUTGOING, "secret")
    assert world.smtp.received == []
    world.window.act_refresh.trigger()
    wait(qtbot, lambda: len(world.smtp.received) == 1 and world.window.act_refresh.isEnabled())
    assert len(world.call("message.list", account=world.account_id, folder="Outbox").messages) == 0


def test_forward_has_a_menu_with_forward_as_attachment(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QToolButton

    button = world.window.toolbar.button_for(world.window.act_forward)
    assert isinstance(button, QToolButton) and button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
    assert [action.text() for action in world.window.forward_menu.actions()] == ["Forward as attachment"]


def test_snoozed_until_is_placed_after_received_and_keeps_its_place() -> None:
    assert with_snoozed_column(("subject", "date", "size")) == ("subject", "date", "snoozed", "size")
    assert with_snoozed_column(("subject",)) == ("subject", "snoozed") and with_snoozed_column(("snoozed", "date")) == ("snoozed", "date")
    assert keep_snoozed_column(("size", "subject", "date"), ("subject", "snoozed", "date", "size")) == ("size", "subject", "snoozed", "date")
    assert keep_snoozed_column(("size",), ("snoozed", "subject", "size")) == ("snoozed", "size")
    assert keep_snoozed_column(("size",), ("subject", "size")) == ("size",)  # switched off: stays off


def test_a_coming_time_is_short() -> None:
    now = datetime(2026, 10, 14, 15, 0).astimezone()  # a Wednesday
    assert coming_date(datetime(2026, 10, 14, 18, 0).astimezone(), now) == "18:00"
    monday = datetime(2026, 10, 19, 9, 0).astimezone()
    assert coming_date(monday, now) == monday.strftime("%a 09:00")  # the weekday in the language of the system, like all dates
    november, january = datetime(2026, 11, 2, 9, 0).astimezone(), datetime(2027, 1, 4, 9, 0).astimezone()
    assert coming_date(november, now) == november.strftime("%d %b 09:00") and coming_date(january, now) == january.strftime("%d %b %Y 09:00")


def test_the_snoozed_folder_has_a_snoozed_until_column(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    window = world.window
    assert "snoozed" not in window.list_model.columns  # only in the Snoozed folder
    window.list_view.selectAll()
    window.mail_actions.snooze_until(in_hours(30))
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Snoozed")) is not None)  # noqa: SLF001
    snoozed = window.folders.find_item((world.account_id, "folder", "Snoozed"))  # noqa: SLF001
    assert snoozed is not None
    window.tree.setCurrentIndex(snoozed)
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and window.list_model.row_at(0) is not None)
    columns = window.list_model.columns
    assert columns == STARTING["snoozed"].columns and window.sort_button.text() == "Sorted by Snoozed until ▾"  # its own columns and sort (B-16)
    cell = window.list_model.index(0, columns.index("snoozed")).data()
    assert cell and cell == coming_date(getattr(window.list_model.row_at(0), "snoozed_until"))
    assert "Snoozed until" in [action.text() for action in window.sort_menu.actions()]
    window.columns.header_clicked(columns.index("snoozed"))  # noqa: SLF001 - already sorted by it: a click turns it round
    assert window.messages.sort == "snoozed" and not window.messages.oldest_first  # noqa: SLF001
    window.columns.header_clicked(columns.index("snoozed"))  # noqa: SLF001 - and back: soonest first
    assert window.messages.sort == "snoozed" and window.messages.oldest_first and window.sort_button.text() == "Sorted by Snoozed until ▾"  # noqa: SLF001
    window.act_show_groups.setChecked(True)
    wait(qtbot, lambda: bool(window.list_model.header_rows()) and window.list_model.index(0, 0).data() in ("Tomorrow", "Today", "This week", "Next week"))
    window.columns.toggle_column("snoozed")  # noqa: SLF001 - switched off
    assert "snoozed" not in window.list_model.columns and "snoozed" not in window.columns.columns  # noqa: SLF001 - and it stays off
    assert window.messages.sort == "subject" and window.messages.oldest_first  # its column went: the first shown of Received, Sent, Subject …
    inbox = window.folders.find_item((world.account_id, "folder", "Inbox"))  # noqa: SLF001
    assert inbox is not None
    window.tree.setCurrentIndex(inbox)
    wait(qtbot, lambda: window.sort_button.text() == "Sorted by Received ▾")
    assert "Snoozed until" not in [action.text() for action in window.sort_menu.actions()]


def test_a_card_shows_when_a_snoozed_message_comes_back(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    window = world.window
    window.list_view.selectAll()
    window.mail_actions.snooze_until(in_hours(30))
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Snoozed")) is not None)  # noqa: SLF001
    snoozed = window.folders.find_item((world.account_id, "folder", "Snoozed"))  # noqa: SLF001
    assert snoozed is not None
    window.tree.setCurrentIndex(snoozed)
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and getattr(window.list_model.row_at(0), "snoozed_until", None) is not None)
    window.layout_actions["cards"].trigger()
    window.resize(1200, 700)
    window.show()
    qtbot.wait(100)
    image = window.list_view.viewport().grab().toImage()
    assert not image.isNull()  # the card painted with its "Until" text (the text itself is checked below)
    until = getattr(window.list_model.row_at(0), "snoozed_until")
    assert until is not None and coming_date(until)


def test_cancel_snooze_brings_a_message_back_now(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    window = world.window
    window.mail_actions.fill_snooze_menu(window.snooze_menu, window.act_cancel_snooze)  # noqa: SLF001
    assert "Cancel snooze" not in [action.text() for action in window.snooze_menu.actions()]  # only in the Snoozed folder
    window.list_view.selectAll()
    window.mail_actions.snooze_until(in_hours(30))
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Snoozed")) is not None)  # noqa: SLF001
    snoozed = window.folders.find_item((world.account_id, "folder", "Snoozed"))  # noqa: SLF001
    assert snoozed is not None
    window.tree.setCurrentIndex(snoozed)
    wait(qtbot, lambda: window.list_model.rowCount() == 3 and window.list_model.row_at(0) is not None)
    descriptions = world.engine.directories.data_directory.rglob("folder.json")
    assert sum(1 for path in descriptions if '"Snoozed"' in path.read_text("utf-8")) == 1  # three snoozes at once made one folder
    window.mail_actions.fill_snooze_menu(window.snooze_menu, window.act_cancel_snooze)  # noqa: SLF001
    assert window.snooze_menu.actions()[0] is window.act_cancel_snooze
    target = str(getattr(window.list_model.row_at(0), "message_id"))
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    window.act_cancel_snooze.trigger()
    wait(qtbot, lambda: window.list_model.rowCount() == 2)
    inbox = world.call("message.list", account=world.account_id, folder="Inbox").messages
    assert [row.message_id for row in inbox] == [target] and inbox[0].flags == ()
    waiting = [info.message_id for info in world.call("message.snoozed")]
    assert len(waiting) == 2 and target not in waiting


def test_a_new_scheduled_message_is_made_on_the_scheduled_page(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later one", body="x", send_at=in_hours(8))
    panel = ScheduledPanel(world.backend)
    qtbot.addWidget(panel)
    panel.show()
    wait(qtbot, lambda: panel.list.count() == 1)
    kept = panel.list.item(0)
    panel.new_requested.connect(lambda: world.window.composing.new_scheduled_message(panel))  # noqa: SLF001 - as Settings connects it
    panel.new_button.click()
    window = world.window.composing.composers[-1]  # noqa: SLF001
    assert window.windowTitle() == "New scheduled message" and window.send_button.text() == "Schedule…" and window.later_button.isHidden()
    assert window.windowModality() == Qt.WindowModality.ApplicationModal  # usable over the Settings window, which waits for it
    fill(window, subject="Sooner one")
    choose_time(monkeypatch, in_hours(3))
    window.send_button.click()
    wait(qtbot, lambda: panel.list.count() == 2)
    assert "Sooner one" in panel.list.item(0).text() and panel.list.currentRow() == 0  # at its place in time, selected
    assert panel.list.item(1) is kept  # the list was not made again
    assert world.smtp.received == [] and not panel.changed


def test_a_scheduling_window_never_sends_at_once(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    real_clock(world)
    window = world.window.composing.open_composer(world.account_id, None, None, schedule_only=True)  # noqa: SLF001
    fill(window)
    choose_time(monkeypatch, in_hours(3), accept=False)  # the person closes the time picker
    window.send_button.click()
    window._send_clicked()  # noqa: SLF001 - as Ctrl+Return does
    assert window.isVisible() and world.smtp.received == [] and world.call("mail.scheduled") == ()


@pytest.mark.parametrize("saved_before", [True, False])
def test_no_draft_is_saved_while_the_confirmation_waits(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, saved_before: bool) -> None:
    window = new_composer(world, qtbot)
    fill(window)
    real_clock(world)
    if saved_before:  # an autosave made a draft; scheduling removes it
        window.save_draft(quiet=True)
        wait(qtbot, lambda: window._stored is not None)  # noqa: SLF001
        window.body.setPlainText("Hi there, and a line typed after that save")  # changed since: the next autosave would save
    warnings: list[str] = []

    def information(*arguments: object, **keywords: object) -> QMessageBox.StandardButton:
        window._autosave()  # noqa: SLF001 - the 30-second timer fires while the box waits for the person
        qtbot.waitUntil(lambda: not window._saving, timeout=5000)  # noqa: SLF001 - the person still reads: any save ends meanwhile
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", information)
    monkeypatch.setattr(QMessageBox, "warning", lambda *arguments, **keywords: warnings.append(str(arguments[2])) or QMessageBox.StandardButton.Ok)
    choose_time(monkeypatch, in_hours(3))
    window.later_button.click()
    wait(qtbot, lambda: gone(window))
    qtbot.wait(300)  # time for a save that should not happen
    drafts = world.call("message.list", account=world.account_id, folder="Drafts").messages
    assert warnings == [] and list(drafts) == [] and len(world.call("mail.scheduled")) == 1


def test_the_scheduled_folder_follows_the_outbox_and_shows_when_each_message_is_sent(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.columns import SNOOZED_COLUMN
    from chrono_mail.ui.models import FOLDER_PATH_ROLE

    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(3))
    wait(qtbot, lambda: world.window.tree_model.find_item(world.account_id, "folder", "Scheduled") is not None)  # made when first used
    item = world.window.tree_model.find_item(world.account_id, "folder", "Scheduled")
    assert item is not None and item.parent() is not None
    parent = item.parent()
    paths = [str(parent.child(row).data(FOLDER_PATH_ROLE) or "") for row in range(parent.rowCount())]
    assert paths.index("Scheduled") == paths.index("Sent") + 1  # the owner's order: Outbox, Sent, Snoozed, Scheduled (no Snoozed folder here; D-232)
    world.window.tree.setCurrentIndex(item.index())
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    world.window.messages.set_sort("snoozed", True)  # noqa: SLF001
    assert world.window.sort_button.text() == "Sorted by Send at ▾"
    section = world.window.list_model.columns.index(SNOOZED_COLUMN)
    assert world.window.list_model.headerData(section, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole) == "Send at"


def test_favorites_scheduled_is_offered_like_snoozed_and_archive(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    window = world.window
    assert ("scheduled", "Scheduled") not in window.mail_state.favorites_in_use()  # noqa: SLF001 - no Scheduled folder yet
    result = world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(3))
    wait(qtbot, lambda: window.tree_model.find_item("", "favorite", "scheduled") is not None)
    assert ("scheduled", "Scheduled") in window.mail_state.favorites_in_use()  # noqa: SLF001 - its tick on Settings > Sidebar
    choices = window.folders.startup_choices()  # noqa: SLF001
    assert any(value.endswith("\tfavorite\tscheduled") and chosen for _, value, chosen, _, _ in choices)  # Show on startup
    item = window.tree_model.find_item("", "favorite", "scheduled")
    assert item is not None
    window.tree.setCurrentIndex(item.index())
    wait(qtbot, lambda: window.list_model.rowCount() == 1)
    world.call("mail.unschedule", account=world.account_id, folder="Scheduled", message=result.message_id)
    wait(qtbot, lambda: window.list_model.rowCount() == 0)  # moved back to drafts: it leaves the list at once


def test_favorites_archive_and_snoozed_appear_the_moment_their_folder_is_made(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    window = world.window
    first, second = world.call("message.list", account=world.account_id, folder="Inbox").messages[:2]
    assert window.tree_model.find_item("", "favorite", "archive") is None and window.tree_model.find_item("", "favorite", "snoozed") is None
    world.call("message.archive", account=world.account_id, folder="Inbox", message=first.message_id)
    wait(qtbot, lambda: window.tree_model.find_item("", "favorite", "archive") is not None)  # no restart needed
    world.call("message.snooze", account=world.account_id, folder="Inbox", message=second.message_id, until=in_hours(3))
    wait(qtbot, lambda: window.tree_model.find_item("", "favorite", "snoozed") is not None)


def test_the_first_button_says_the_one_action_that_applies(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(3))
    first = world.call("message.list", account=world.account_id, folder="Inbox").messages[0]
    world.call("message.snooze", account=world.account_id, folder="Inbox", message=first.message_id, until=in_hours(5))
    panel = ScheduledPanel(world.backend)
    qtbot.addWidget(panel)
    panel.show()
    wait(qtbot, lambda: panel.list.count() == 2)
    panel.list.setCurrentRow(-1)
    assert panel.now_button.text() == "Send now" and not panel.now_button.isEnabled()  # nothing selected: greyed, never hidden
    panel.list.setCurrentRow(0)  # the scheduled message (sooner)
    assert panel.now_button.text() == "Send now" and panel.now_button.isEnabled() and panel.edit_button.isEnabled()
    panel.list.setCurrentRow(1)  # the snoozed message
    assert panel.now_button.text() == "Cancel snooze" and "back to Inbox now, unread" in panel.now_button.toolTip()
    assert not panel.edit_button.isEnabled() and not panel.edit_button.isHidden()  # greyed, still there


def test_the_message_window_says_schedule_and_names_the_scheduled_folder(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = new_composer(world, qtbot)
    assert window.later_button.text() == "Schedule…"
    fill(window)
    real_clock(world)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *arguments, **keywords: told.append(str(arguments[2])) or QMessageBox.StandardButton.Ok)
    choose_time(monkeypatch, in_hours(3))
    window.later_button.click()
    wait(qtbot, lambda: bool(told))
    assert "waits in your Scheduled folder" in told[0] and "Outbox" not in told[0]



def scheduled_page(world: World, qtbot: QtBot, count: int) -> ScheduledPanel:
    panel = ScheduledPanel(world.backend)
    qtbot.addWidget(panel)
    panel.show()
    wait(qtbot, lambda: panel.list.count() == count)
    panel.edit_requested.connect(lambda info: world.window.composing.edit_scheduled(info.account, info, panel))  # noqa: SLF001 - as Settings connects it
    return panel


def test_the_page_has_new_and_edit_and_edit_is_greyed_for_a_snoozed_message(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(3))
    first = world.call("message.list", account=world.account_id, folder="Inbox").messages[0]
    world.call("message.snooze", account=world.account_id, folder="Inbox", message=first.message_id, until=in_hours(5))
    panel = scheduled_page(world, qtbot, 2)
    assert (panel.new_button.text(), panel.edit_button.text()) == ("New", "Edit")  # no dots (the owner)
    panel.list.setCurrentRow(0)
    assert panel.edit_button.isEnabled()
    panel.list.setCurrentRow(1)  # received mail is never changed
    assert not panel.edit_button.isEnabled() and not panel.edit_button.isHidden()


def test_edit_changes_a_scheduled_message_in_place_and_only_its_line_follows(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Sooner", body="x", send_at=in_hours(2))
    edited = world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(6)).message_id
    panel = scheduled_page(world, qtbot, 2)
    kept, line = panel.list.item(0), panel.list.item(1)
    time_before = line.data(256).send_at
    panel.list.setCurrentRow(1)
    panel.edit_button.click()
    wait(qtbot, lambda: any(window.windowTitle() == "Edit scheduled message" for window in world.window.composing.composers))  # noqa: SLF001
    window = world.window.composing.composers[-1]  # noqa: SLF001
    assert window.windowModality() == Qt.WindowModality.ApplicationModal and window.keep_button.text() == "Save" and window.later_button.text() == "Schedule…"
    assert not window.send_button.isEnabled() and not window.save_button.isEnabled() and not window.send_button.isHidden()  # greyed, never hidden
    window.subject.setText("Later, and changed")
    window._autosave()  # noqa: SLF001 - the 30-second timer: a scheduled message never becomes a draft by itself
    qtbot.wait(300)
    assert list(world.call("message.list", account=world.account_id, folder="Drafts").messages) == []
    window.keep_button.click()
    wait(qtbot, lambda: "Later, and changed" in line.text())
    assert panel.list.item(1) is line and panel.list.item(0) is kept and panel.list.currentRow() == 1 and not panel.changed  # nothing made again
    waiting = {item.message_id: item for item in world.call("mail.scheduled")}
    assert waiting[edited].subject == "Later, and changed" and waiting[edited].send_at == time_before  # the same id, its time kept
    assert list(world.call("message.list", account=world.account_id, folder="Drafts").messages) == [] and world.smtp.received == []


def test_schedule_while_editing_opens_on_the_current_time_and_moves_the_line(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Sooner", body="x", send_at=in_hours(2))
    moved = world.call(
        "mail.send", account=world.account_id, to="you@example.com", subject="Again", body="x", send_at=in_hours(6), repeat_weeks=2, repeat_until=in_hours(24 * 90),
    ).message_id
    panel = scheduled_page(world, qtbot, 2)
    panel.list.setCurrentRow(1)
    seen: list[tuple[str, bool, int, bool]] = []

    def execute(self: WhenDialog) -> QDialog.DialogCode:
        seen.append((self.edit.dateTime().toString("yyyy-MM-ddTHH:mm"), self.repeat_box.isChecked(), self.weeks.value(), self.until_box.isChecked()))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(WhenDialog, "exec", execute)
    monkeypatch.setattr(WhenDialog, "chosen", lambda self: in_hours(1))
    panel.edit_button.click()
    wait(qtbot, lambda: any(window.windowTitle() == "Edit scheduled message" for window in world.window.composing.composers))  # noqa: SLF001
    world.window.composing.composers[-1].later_button.click()  # noqa: SLF001
    wait(qtbot, lambda: str(getattr(panel.list.item(0).data(256), "message_id")) == moved)
    assert seen == [(in_hours(6), True, 2, True)]  # it opened on what the message had
    assert panel.list.currentRow() == 0 and "every 2 weeks" in panel.list.item(0).text()  # at its new place in time, selected


def test_a_message_in_the_scheduled_folder_opens_to_be_edited_never_as_a_draft(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(3))
    wait(qtbot, lambda: world.window.tree_model.find_item(world.account_id, "folder", "Scheduled") is not None)
    item = world.window.tree_model.find_item(world.account_id, "folder", "Scheduled")
    assert item is not None
    world.window.tree.setCurrentIndex(item.index())
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    world.window.open_selected()  # Enter or a double click
    wait(qtbot, lambda: any(window.windowTitle() == "Edit scheduled message" for window in world.window.composing.composers))  # noqa: SLF001
    window = world.window.composing.composers[-1]  # noqa: SLF001
    window.subject.setText("Later, edited in the folder")
    window._autosave()  # noqa: SLF001 - before 1.1.207 this made it a draft: no longer scheduled
    window.keep_button.click()
    wait(qtbot, lambda: "Later, edited in the folder" in str(getattr(world.window.list_model.row_at(world.window.list_model.display_row(0)), "subject", "")))  # the row follows its fact
    assert [item.subject for item in world.call("mail.scheduled")] == ["Later, edited in the folder"]
    assert list(world.call("message.list", account=world.account_id, folder="Drafts").messages) == []


def open_scheduled_folder(world: World, qtbot: QtBot, rows: int) -> None:
    wait(qtbot, lambda: world.window.tree_model.find_item(world.account_id, "folder", "Scheduled") is not None)
    item = world.window.tree_model.find_item(world.account_id, "folder", "Scheduled")
    assert item is not None
    world.window.tree.setCurrentIndex(item.index())
    wait(qtbot, lambda: world.window.list_model.rowCount() == rows)


def menu_texts(world: World, monkeypatch: pytest.MonkeyPatch, point: QPoint) -> list[str]:
    shown: list[str] = []

    class Recorded(QMenu):
        def exec(self, *arguments: object) -> None:  # type: ignore[override]  # the menu is read, never shown
            shown.extend(action.text() for action in self.actions() if action.text())

    monkeypatch.setattr("chrono_mail.ui.mainwindow.QMenu", Recorded)
    world.window._list_menu(point)  # noqa: SLF001 - as a right click does
    return shown


def test_edit_opens_again_after_the_window_was_closed(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(3))
    panel = scheduled_page(world, qtbot, 1)
    panel.list.setCurrentRow(0)
    for subject in ("First change", "Second change"):
        panel.edit_button.click()
        wait(qtbot, lambda: any(window.windowTitle() == "Edit scheduled message" and window.isVisible() for window in world.window.composing.composers))  # noqa: SLF001
        window = world.window.composing.composers[-1]  # noqa: SLF001
        window.subject.setText(subject)
        window.keep_button.click()
        wait(qtbot, lambda: world.window.composing.composers == [])  # noqa: SLF001 - a closed window is gone, not kept hidden
        assert subject in panel.list.item(0).text()


def test_the_page_buttons_are_new_edit_send_now_and_pause(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    panel = scheduled_page(world, qtbot, 0)
    assert [button.text() for button in panel.findChildren(QPushButton)] == ["New", "Edit", "Send now", "Pause"]  # back to drafts: a drag; stop repeating: Edit


def test_the_menu_of_a_scheduled_view_has_edit_on_a_message_and_new_on_the_empty_area(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(3))
    open_scheduled_folder(world, qtbot, 1)
    view = world.window.list_view
    view.setCurrentIndex(world.window.list_model.index(0, 0))
    on_message = menu_texts(world, monkeypatch, view.visualRect(world.window.list_model.index(0, 0)).center())
    assert on_message[0] == "Edit" and "Edit draft" not in on_message
    empty = menu_texts(world, monkeypatch, QPoint(10, view.viewport().height() - 5))
    assert empty == ["New"]
    world.window.act_new_scheduled.trigger()
    wait(qtbot, lambda: any(window.windowTitle() == "New scheduled message" for window in world.window.composing.composers))  # noqa: SLF001
    move_to = [action.text() for action in world.window.move_menu.actions()]
    assert sorted(move_to) == ["Drafts", "Trash"]  # a scheduled message goes back to Drafts or away, nowhere else


def test_favorites_scheduled_opens_a_message_to_edit_it(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(3))
    wait(qtbot, lambda: world.window.tree_model.find_item("", "favorite", "scheduled") is not None)
    item = world.window.tree_model.find_item("", "favorite", "scheduled")
    assert item is not None
    world.window.tree.setCurrentIndex(item.index())
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    world.window.open_selected()  # Enter or a double click
    wait(qtbot, lambda: any(window.windowTitle() == "Edit scheduled message" for window in world.window.composing.composers))  # noqa: SLF001


def test_a_scheduled_message_dropped_on_drafts_becomes_a_draft_and_other_folders_refuse_it(world: World, qtbot: QtBot) -> None:
    from tests.ui.test_ui_dragdrop import drop, folder_point, move_probe

    started(qtbot, world)
    real_clock(world)
    again = world.call("mail.send", account=world.account_id, to="you@example.com", subject="Again", body="x", send_at=in_hours(3), repeat_days=1).message_id
    open_scheduled_folder(world, qtbot, 1)
    data = world.window.list_model.mimeData([world.window.list_model.index(0, 0)])
    assert not move_probe(world, folder_point(world, "Inbox"), data) and move_probe(world, folder_point(world, "Trash"), data)
    assert drop(world, folder_point(world, "Drafts"), data)
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Drafts").total == 1)
    (draft,) = world.call("message.list", account=world.account_id, folder="Drafts").messages
    source = str(world.call("message.source", account=world.account_id, folder="Drafts", message=draft.message_id).text)
    assert "X-Chrono-Mail-Send-At" not in source and "X-Chrono-Mail-Repeat" not in source  # a plain draft: no schedule left
    assert world.call("mail.scheduled") == () and again


def lines(panel: ScheduledPanel) -> list[object]:
    """The page's line objects, in order: the same object after an action means its line was never made again."""
    return [panel.list.item(row) for row in range(panel.list.count())]


def three_scheduled(world: World) -> None:
    real_clock(world)
    for subject, hours in (("One", 2), ("Two", 4), ("Three", 6)):
        world.call("mail.send", account=world.account_id, to="you@example.com", subject=subject, body="x", send_at=in_hours(hours))


def test_send_now_takes_only_its_line_away_and_selects_the_next(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    three_scheduled(world)
    panel = scheduled_page(world, qtbot, 3)
    first, _, third = lines(panel)
    panel.list.setCurrentRow(1)
    panel.now_button.click()
    wait(qtbot, lambda: panel.status.text() == "Sent")
    assert lines(panel) == [first, third] and panel.list.currentRow() == 1  # the next line selected; nothing made again (R-21)
    assert len(world.smtp.received) == 1 and panel.changed


def test_send_now_of_a_repeating_message_keeps_its_line_selected(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Sooner", body="x", send_at=in_hours(2))
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Weekly", body="x", send_at=in_hours(4), repeat_days=7)
    panel = scheduled_page(world, qtbot, 2)
    before = lines(panel)
    panel.list.setCurrentRow(1)
    panel.now_button.click()
    wait(qtbot, lambda: panel.status.text() == "Sent")
    assert lines(panel) == before and panel.list.currentRow() == 1 and "sent 1 times" in before[1].text()  # type: ignore[attr-defined]


def test_a_failed_send_now_says_why(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    three_scheduled(world)
    panel = scheduled_page(world, qtbot, 3)
    first, second, _ = lines(panel)
    world.smtp._size_limit = 10  # noqa: SLF001 - the server refuses everything
    panel.list.setCurrentRow(2)
    panel.now_button.click()
    wait(qtbot, lambda: "it stays in the Outbox" in panel.status.text())
    assert lines(panel) == [first, second] and panel.list.currentRow() == 1  # it left the page for the Outbox; the line before is selected
    assert "Done" not in panel.status.text() and world.smtp.received == []


def test_cancel_snooze_takes_only_its_line_away(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Later", body="x", send_at=in_hours(2))
    for message, hours in zip(world.call("message.list", account=world.account_id, folder="Inbox").messages[:2], (4, 6), strict=True):
        world.call("message.snooze", account=world.account_id, folder="Inbox", message=message.message_id, until=in_hours(hours))
    panel = scheduled_page(world, qtbot, 3)
    first, _, third = lines(panel)
    panel.list.setCurrentRow(1)
    panel.now_button.click()
    wait(qtbot, lambda: panel.status.text() == "Back in Inbox, unread")
    assert lines(panel) == [first, third] and panel.list.currentRow() == 1


def test_pause_and_resume_change_only_their_line(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    real_clock(world)
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Sooner", body="x", send_at=in_hours(2))
    world.call("mail.send", account=world.account_id, to="you@example.com", subject="Weekly", body="x", send_at=in_hours(4), repeat_days=7)
    panel = scheduled_page(world, qtbot, 2)
    before = lines(panel)
    panel.list.setCurrentRow(1)
    panel.pause_button.click()
    wait(qtbot, lambda: panel.status.text() == "Paused")
    assert lines(panel) == before and panel.list.currentRow() == 1 and "paused" in before[1].text()  # type: ignore[attr-defined]
    assert panel.pause_button.text() == "Resume"
    panel.pause_button.click()
    wait(qtbot, lambda: panel.status.text() == "It is sent again from now on")
    assert lines(panel) == before and panel.list.currentRow() == 1 and panel.pause_button.text() == "Pause"


def test_showing_the_page_again_keeps_the_lines_and_the_selection(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    three_scheduled(world)
    panel = scheduled_page(world, qtbot, 3)
    _, second, third = lines(panel)
    panel.list.setCurrentRow(2)
    panel.hide()
    one = world.call("mail.scheduled")[0].message_id
    world.call("mail.send-due", account=world.account_id, message=one)  # sent while the page was not shown (the minute check)
    panel.show()
    wait(qtbot, lambda: panel.list.count() == 2)
    assert lines(panel) == [second, third] and panel.list.currentRow() == 1  # the message that left went; the rest stayed as they were
