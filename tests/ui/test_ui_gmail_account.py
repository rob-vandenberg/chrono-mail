from pytestqt.qtbot import QtBot

from chrono_mail.core import AccountKind
from chrono_mail.ui.accountdialog import GMAIL, AccountDialog
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_gmail_account.py 4.4.592"

# --- Version history ----------------------------------------------------
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

