import dataclasses

from PySide6.QtWidgets import QAbstractButton, QFormLayout, QLabel, QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.calendaraccount import CalendarFields
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_account_names.py 4.4.603"

# --- Version history ----------------------------------------------------
# v4.4.603: Initial tests: one name per meaning in the account windows; a calendar account shows what it signs in with, read-only (D-342)

OLD = ("account name", "user name", "login name")


def texts(window: QWidget) -> list[str]:
    """Every label, button, tick box and hover text of a window."""
    found = [label.text() for label in window.findChildren(QLabel)] + [button.text() for button in window.findChildren(QAbstractButton)]
    return found + [widget.toolTip() for widget in window.findChildren(QWidget)] + [edit.placeholderText() for edit in window.findChildren(QLineEdit)]


def test_no_account_window_says_account_name_or_user_name(world: World, qtbot: QtBot) -> None:
    """D-342: Shown as, Your name, E-mail address and Sign-in name, and no other words for them."""
    windows: list[QWidget] = [AccountDialog(world.backend), AccountEditDialog(world.backend, world.call("account.list")[0])]
    host = QWidget()
    windows.append(host)
    CalendarFields(world.backend, list(world.call("account.list")), host)
    for window in windows:
        qtbot.addWidget(window)
        said = [text.casefold() for text in texts(window)]
        assert not [text for text in said if any(old in text for old in OLD)]
    assert "Shown as" in texts(windows[0]) and "Sign-in name" in texts(windows[0])


def google_account(world: World) -> AccountEditDialog:
    summary = dataclasses.replace(world.call("account.list")[0], kind="dav", google=True, incoming_username="ann@gmail.com", dav_username="",
                                  dav_url="https://apidata.googleusercontent.com/caldav/v2/ann%40gmail.com/user", account_name="Google Calendar (ann@gmail.com)")
    return AccountEditDialog(world.backend, summary)


def test_a_google_account_shows_whom_it_signed_in_as_and_asks_no_sign_in_name(world: World, qtbot: QtBot) -> None:
    dialog = google_account(world)
    qtbot.addWidget(dialog)
    assert dialog.sign_in_name is not None and dialog.sign_in_name.text() == "ann@gmail.com" and dialog.sign_in_name.isReadOnly()
    general = next(form for form in dialog.findChildren(QFormLayout) if form.indexOf(dialog.sign_in_name) >= 0)
    label = general.labelForField(dialog.sign_in_name)
    assert isinstance(label, QLabel) and label.text() == "Google account"
    assert dialog.signed_in.text() == "Signed in with Google as ann@gmail.com"
    calendars = next(form for form in dialog.findChildren(QFormLayout) if form.indexOf(dialog.dav_username) >= 0)
    assert not calendars.isRowVisible(dialog.dav_username) and not calendars.isRowVisible(dialog.dav_password)  # Google's sign-in replaces them


def test_another_calendar_account_shows_its_sign_in_name_read_only(world: World, qtbot: QtBot) -> None:
    summary = dataclasses.replace(world.call("account.list")[0], kind="dav", google=False, incoming_username="rob@example.com", dav_username="",
                                  dav_url="https://dav.example.com/", account_name="Work calendars")
    dialog = AccountEditDialog(world.backend, summary)
    qtbot.addWidget(dialog)
    assert dialog.sign_in_name is not None and dialog.sign_in_name.text() == "rob@example.com" and dialog.sign_in_name.isReadOnly()
    general = next(form for form in dialog.findChildren(QFormLayout) if form.indexOf(dialog.sign_in_name) >= 0)
    label = general.labelForField(dialog.sign_in_name)
    assert isinstance(label, QLabel) and label.text() == "Sign-in name"
    assert dialog.dav_username.placeholderText() == "(rob@example.com)"  # the sign-in name it has, never "the same as for incoming mail"
