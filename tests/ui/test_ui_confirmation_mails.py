from dataclasses import replace

from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.ui.accountedit import AccountEditDialog
from tests.ui.world import REGISTRY, World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_confirmation_mails.py 4.4.536"

# --- Version history ----------------------------------------------------
# v4.4.536: Send reminder emails via (D-313)
# v4.4.514: not certain: everything free (D-310)
# v4.4.510: Initial tests: Edit account > Calendars and contacts: "Send confirmation emails via" with Chrono Mail or the server (D-310)

URL: str = "https://dav.example.com/"


def window(world: World, qtbot: QtBot, **values: object) -> AccountEditDialog:
    listed = REGISTRY.run(world.engine, "account.list", {})
    assert isinstance(listed, tuple) and isinstance(listed[0], AccountSummary)
    dialog = AccountEditDialog(world.backend, replace(listed[0], **values), world.window)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    return dialog


def test_a_server_that_announces_it_keeps_the_free_choice(world: World, qtbot: QtBot) -> None:
    dialog = window(world, qtbot, dav_url=URL, dav_auto_schedule=True)
    assert dialog.answers_send.isChecked() and dialog.answers_send.isEnabled() and dialog.answers_by.isEnabled()
    assert dialog.answers_by.currentData() == "server" and dialog.answers_info.isHidden()
    assert dialog._answers_value() is None  # noqa: SLF001 - nothing changed
    dialog.answers_by.setCurrentIndex(dialog.answers_by.findData("chrono"))
    assert dialog._answers_value() == "chrono"  # noqa: SLF001
    dialog.answers_send.setChecked(False)
    assert not dialog.answers_by.isEnabled() and dialog._answers_value() == "none"  # noqa: SLF001


def test_a_server_that_cannot_be_silenced_is_greyed_with_its_reason(world: World, qtbot: QtBot) -> None:
    dialog = window(world, qtbot, dav_url=URL, dav_auto_schedule=True, dav_always_answers=True, invitation_answers="chrono")
    assert dialog.answers_send.isChecked() and not dialog.answers_send.isEnabled()
    assert not dialog.answers_by.isEnabled() and dialog.answers_by.currentData() == "server"
    assert dialog.answers_info.reason == "This server always sends these mails itself; that cannot be turned off."
    assert dialog._answers_value() is None  # noqa: SLF001


def test_without_a_server_that_sends_them_only_the_box_can_be_chosen(world: World, qtbot: QtBot) -> None:
    dialog = window(world, qtbot, invitation_answers="none")
    assert not dialog.answers_send.isChecked() and dialog.answers_send.isEnabled()
    assert dialog.answers_by.currentData() == "chrono" and not dialog.answers_by.isEnabled() and "no calendar server" in dialog.answers_info.reason
    assert dialog._answers_value() is None  # noqa: SLF001
    dialog.answers_send.setChecked(True)
    assert dialog._answers_value() == ""  # noqa: SLF001 - sent again, by whoever the server's word gives
    other = window(world, qtbot, dav_url=URL)
    assert other.answers_by.isEnabled() and other.answers_by.currentData() == "chrono" and other.answers_info.isHidden()  # not certain: everything free
    other.answers_by.setCurrentIndex(other.answers_by.findData("server"))
    assert other._answers_value() == "server"  # noqa: SLF001 - the person may know better than the server's silence


def test_a_server_typed_in_this_window_changes_the_reason_at_once(world: World, qtbot: QtBot) -> None:
    dialog = window(world, qtbot, dav_auto_schedule=True, dav_always_answers=True)
    assert dialog.answers_send.isEnabled()  # no server here now: its word from earlier does not count
    dialog.dav_url.setText(URL)
    assert not dialog.answers_send.isEnabled() and dialog.answers_by.currentData() == "server"


def test_reminder_emails_default_to_chrono_mail_and_server_can_be_chosen_with_a_calendar_server(world: World, qtbot: QtBot) -> None:
    dialog = window(world, qtbot, dav_url=URL)
    assert dialog.reminders_send.isChecked() and dialog.reminders_by.currentData() == "chrono" and dialog.reminders_by.isEnabled()
    assert dialog._reminders_value() is None  # noqa: SLF001 - nothing changed
    dialog.reminders_by.setCurrentIndex(dialog.reminders_by.findData("server"))
    assert dialog._reminders_value() == "server"  # noqa: SLF001
    dialog.reminders_send.setChecked(False)
    assert not dialog.reminders_by.isEnabled() and dialog._reminders_value() == "none"  # noqa: SLF001


def test_without_a_calendar_server_only_the_reminder_dropdown_is_greyed_on_chrono_mail(world: World, qtbot: QtBot) -> None:
    dialog = window(world, qtbot, dav_url="", reminder_emails="server")
    assert dialog.reminders_by.currentData() == "chrono" and not dialog.reminders_by.isEnabled() and dialog.reminders_send.isEnabled()
