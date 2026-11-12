from pytestqt.qtbot import QtBot

from chrono_mail.core import AccountKind
import pytest

from chrono_mail.ui import accountdialog
from chrono_mail.ui.accountdialog import GMAIL, GOOGLE, AccountDialog
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_gmail_account.py 4.4.594"

# --- Version history ----------------------------------------------------
# v4.4.594: Gmail hides Outgoing mail and Options: the window is shorter than with IMAP (D-335)
# v4.4.593: Google Calendar and Contacts: Sign in with Google, greyed with the reason without the client file (D-335)
# v4.4.592: Initial tests: Gmail in Add account, its servers filled in and greyed, the address as the user name (D-335)


def test_gmail_fills_in_its_servers_greyed_and_takes_the_address_as_user_name(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend, mail_accounts=world.window.mail_state.mail_accounts())
    qtbot.addWidget(dialog)
    servers = dialog.servers
    assert not dialog.gmail_help.isVisibleTo(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData(GMAIL))
    dialog.email.setText("ann@gmail.com")
    dialog.email.textEdited.emit("ann@gmail.com")
    assert dialog.gmail_help.isVisibleTo(dialog) and "app password" in dialog.gmail_help.text()
    assert servers.endpoint("incoming") == ("imap.gmail.com", 993, "tls") and servers.endpoint("outgoing") == ("smtp.gmail.com", 465, "tls")
    assert servers.username.text() == "ann@gmail.com" and servers.same_login.isChecked()
    assert not any(field.isEnabled() for field in (servers.incoming_host, servers.incoming_port, servers.incoming_security, servers.outgoing_host,
                                                   servers.outgoing_port, servers.outgoing_security, servers.username))
    assert servers.password.isEnabled() and servers.kind is AccountKind.IMAP
    assert servers.check_parameters().kind == "imap"  # stored as an IMAP account


def test_leaving_gmail_gives_the_fields_back_without_gmails_servers(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend, mail_accounts=world.window.mail_state.mail_accounts())
    qtbot.addWidget(dialog)
    servers = dialog.servers
    dialog.email.setText("ann@example.com")
    dialog.email.textEdited.emit("ann@example.com")
    dialog.kind.setCurrentIndex(dialog.kind.findData(GMAIL))
    dialog.kind.setCurrentIndex(dialog.kind.findData(AccountKind.IMAP.value))
    assert servers.incoming_host.isEnabled() and servers.username.isEnabled() and servers.outgoing_host.isEnabled()
    assert servers.incoming_host.text() == "mail.example.com" and servers.outgoing_host.text() == "mail.example.com"
    assert not dialog.gmail_help.isVisibleTo(dialog)


def test_an_address_typed_while_on_gmail_gives_the_usual_guess_when_leaving_gmail(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend, mail_accounts=world.window.mail_state.mail_accounts())
    qtbot.addWidget(dialog)
    dialog.email.setText("a@one.org")
    dialog.email.textEdited.emit("a@one.org")
    dialog.kind.setCurrentIndex(dialog.kind.findData(GMAIL))
    dialog.email.setText("a@two.org")
    dialog.email.textEdited.emit("a@two.org")
    dialog.kind.setCurrentIndex(dialog.kind.findData(AccountKind.POP3.value))
    assert dialog.servers.incoming_host.text() == "mail.two.org" and dialog.servers.outgoing_host.text() == "mail.two.org"


def test_google_calendar_and_contacts_is_a_sign_in_greyed_with_its_reason_without_the_client_file(
    world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(accountdialog, "google_client_codes", lambda: ("id", "secret"))
    dialog = AccountDialog(world.backend, mail_accounts=world.window.mail_state.mail_accounts())
    qtbot.addWidget(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData(GOOGLE))
    assert dialog.add_button.text() == "Sign in with Google" and dialog.add_button.isEnabled()
    assert dialog.gmail_help.isVisibleTo(dialog) and "web browser" in dialog.gmail_help.text()
    assert not dialog.servers.widget.isVisibleTo(dialog) and not dialog.calendar.isVisibleTo(dialog) and not dialog.test_button.isVisibleTo(dialog)
    monkeypatch.setattr(accountdialog, "google_client_codes", lambda: None)
    dialog.kind.setCurrentIndex(dialog.kind.findData(GMAIL))
    dialog.kind.setCurrentIndex(dialog.kind.findData(GOOGLE))
    assert not dialog.add_button.isEnabled() and "not set up on this computer" in dialog.add_button.toolTip()
    dialog.kind.setCurrentIndex(dialog.kind.findData(GMAIL))
    assert dialog.add_button.text() == "Add account" and dialog.add_button.isEnabled() and "app password" in dialog.gmail_help.text()



def test_edit_account_of_google_offers_sign_in_with_google_and_greys_the_login(world: World, qtbot: QtBot) -> None:
    import dataclasses

    from chrono_mail.ui.accountedit import AccountEditDialog

    account = dataclasses.replace(world.call("account.list")[0], google=True)  # an account of Google's calendars (D-335); it has no server here
    dialog = AccountEditDialog(world.backend, account)
    qtbot.addWidget(dialog)
    assert dialog.dav_check_button.text() == "Sign in with Google" and dialog.dav_check_button.isEnabled()
    assert not any(field.isEnabled() for field in (dialog.dav_url, dialog.dav_username, dialog.dav_password))


def test_cancel_stays_usable_while_waiting_for_google_and_stops_the_sign_in(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from chrono_mail.engine import commands_google, google_access
    from tests.ui.world import wait

    def opened(url: str) -> bool:
        return True

    monkeypatch.setattr(accountdialog, "google_client_codes", lambda: ("id", "secret"))
    monkeypatch.setattr(google_access, "google_client_codes", lambda: ("id", "secret"))
    monkeypatch.setattr(commands_google.webbrowser, "open", opened)  # no real browser in a test
    dialog = AccountDialog(world.backend, mail_accounts=world.window.mail_state.mail_accounts())
    qtbot.addWidget(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData(GOOGLE))
    dialog.add_button.click()
    wait(qtbot, lambda: bool(commands_google._waiting))  # pyright: ignore[reportPrivateUsage]
    assert dialog.cancel_button is not None and dialog.cancel_button.isEnabled() and not dialog.add_button.isEnabled()
    dialog.reject()
    assert dialog.result() == AccountDialog.DialogCode.Rejected
    wait(qtbot, lambda: not commands_google._waiting)  # pyright: ignore[reportPrivateUsage]


def test_edit_accounts_sign_in_can_be_stopped_with_its_button(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    from chrono_mail.engine import commands_google, google_access
    from chrono_mail.ui.accountedit import AccountEditDialog
    from tests.ui.world import wait

    def opened(url: str) -> bool:
        return True

    monkeypatch.setattr(google_access, "google_client_codes", lambda: ("id", "secret"))
    monkeypatch.setattr(commands_google.webbrowser, "open", opened)  # no real browser in a test
    account = world.call("account.list")[0]
    world.engine.change_config(lambda config: config.with_account(dataclasses.replace(config.find(account.account_id), google=True)))  # a Google account
    dialog = AccountEditDialog(world.backend, dataclasses.replace(account, google=True))
    qtbot.addWidget(dialog)
    dialog.dav_check_button.click()
    wait(qtbot, lambda: bool(commands_google._waiting))  # pyright: ignore[reportPrivateUsage]
    assert dialog.dav_check_button.text() == "Stop waiting for Google" and dialog.dav_check_button.isEnabled()
    dialog.dav_check_button.click()
    wait(qtbot, lambda: dialog.dav_check_button.text() == "Sign in with Google")
    assert not commands_google._waiting  # pyright: ignore[reportPrivateUsage]


def test_gmail_hides_outgoing_mail_and_options_so_the_window_is_shorter(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend, mail_accounts=world.window.mail_state.mail_accounts())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.kind.setCurrentIndex(dialog.kind.findData(AccountKind.IMAP.value))
    qtbot.wait(50)  # the window lays itself out again after a change of type
    imap_height = dialog.minimumSize().height()
    dialog.kind.setCurrentIndex(dialog.kind.findData(GMAIL))
    qtbot.wait(50)
    assert not dialog.servers.outgoing_group.isVisibleTo(dialog) and not dialog._options.isVisibleTo(dialog)  # pyright: ignore[reportPrivateUsage]
    assert dialog.servers.incoming_group.isVisibleTo(dialog) and dialog.servers.password.isVisibleTo(dialog)
    assert dialog.add_button.isVisibleTo(dialog) and dialog.test_button.isVisibleTo(dialog)
    assert dialog.minimumSize().height() < imap_height - 150  # measured: 546 against 800
    dialog.kind.setCurrentIndex(dialog.kind.findData(AccountKind.IMAP.value))
    assert dialog.servers.outgoing_group.isVisibleTo(dialog) and dialog._options.isVisibleTo(dialog)  # pyright: ignore[reportPrivateUsage]


def test_gmail_always_encrypts_even_after_the_tick_was_taken_off_under_imap(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend, mail_accounts=world.window.mail_state.mail_accounts())
    qtbot.addWidget(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData(AccountKind.IMAP.value))
    dialog.encrypt.setChecked(False)
    dialog.kind.setCurrentIndex(dialog.kind.findData(GMAIL))
    assert dialog.encrypt.isChecked()

