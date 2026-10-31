from collections.abc import Generator
from pathlib import Path

import pytest
import sys
from PySide6.QtGui import QGuiApplication
from pytestqt.qtbot import QtBot

from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.serverfields import IMAP_PORTS, OUTGOING_PORTS, POP3_PORTS as INCOMING_PORTS
from chrono_mail.ui.recoverydialog import RecoveryDialog
from tests.ui.world import Dialogs, World, answer_input, answer_save_file, build_world, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_dialogs.py 4.2.441"

# --- Version history ----------------------------------------------------
# v4.2.441: the Look at the server's certificate… button is gone (UR-05, D-299)
# v4.2.439: the RV-209 test on the calendar part of the Add window (D-298)
# v3.6.350: a code that could not be shown is asked again with Try again
# v3.6.349: while adding, nothing else in the window can be used (RV-207, RV-208, RV-209); the recovery code's tick needs the code (RV-210)
# v3.5.346: the window cannot close while the account is being added (RV-02); Done of the recovery code notes it as stored (RV-03)
# v2.0.287: the permission check of the recovery file not on Windows (the Windows computer's report)
# v1.9.271: The account window's values are typed (R-19)
# v1.1.243: a typed port stays when Security or the kind changes; the standard one follows (R-05)
# v1.1.213: a separate outgoing login typed when adding
# v1.1.211: an account name typed in Add account is saved, Your name apart from it
# v0.0.69: The same e-mail address may be used by several accounts
# v0.0.31: IMAP accounts in the engine and the interface
# v0.0.13: Initial dialog tests


@pytest.fixture
def blank(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs, with_account=False)


def fill(dialog: AccountDialog, world: World, password: str = "secret") -> None:
    dialog.email.setText("new@example.com")
    dialog.name.setText("New Person")
    dialog.username.setText("user")
    dialog.password.setText(password)
    dialog.incoming_host.setText("127.0.0.1")
    dialog.outgoing_host.setText("127.0.0.1")
    dialog.incoming_security.setCurrentIndex(dialog.incoming_security.findData("none"))
    dialog.outgoing_security.setCurrentIndex(dialog.outgoing_security.findData("none"))
    dialog.incoming_port.setValue(world.pop.port)
    dialog.outgoing_port.setValue(world.smtp.port)


def test_defaults_and_the_first_account_welcome(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend, first_account=True)
    qtbot.addWidget(dialog)
    assert dialog.incoming_security.currentData() == "tls" and dialog.incoming_port.value() == 995
    assert dialog.outgoing_security.currentData() == "starttls" and dialog.outgoing_port.value() == 587
    assert dialog.encrypt.isChecked() and dialog.policy.currentData() == "keep"
    assert dialog.password.echoMode() == dialog.password.EchoMode.Password


def test_the_port_follows_the_security_choice(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    for security in ("tls", "starttls", "none"):
        dialog.incoming_security.setCurrentIndex(dialog.incoming_security.findData(security))
        dialog.outgoing_security.setCurrentIndex(dialog.outgoing_security.findData(security))
        assert dialog.incoming_port.value() == INCOMING_PORTS[security] and dialog.outgoing_port.value() == OUTGOING_PORTS[security]


def test_a_typed_port_stays_when_the_security_or_the_kind_changes(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    dialog.kind.setCurrentIndex(dialog.kind.findData("imap"))
    assert dialog.incoming_port.value() == IMAP_PORTS["tls"]  # the standard port follows the kind
    dialog.incoming_port.setValue(10993)
    dialog.outgoing_port.setValue(2525)
    dialog.incoming_security.setCurrentIndex(dialog.incoming_security.findData("starttls"))
    dialog.outgoing_security.setCurrentIndex(dialog.outgoing_security.findData("tls"))
    dialog.kind.setCurrentIndex(dialog.kind.findData("pop3"))
    assert (dialog.incoming_port.value(), dialog.outgoing_port.value()) == (10993, 2525)  # typed: never overwritten
    assert dialog.incoming_group.title() == "Incoming mail (POP3)"


def test_the_address_suggests_the_user_name_and_the_servers(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    dialog.email.textEdited.emit("rob@example.org")
    assert dialog.username.text() == "rob@example.org" and dialog.incoming_host.text() == "mail.example.org" and dialog.outgoing_host.text() == "mail.example.org"
    dialog.incoming_host.setText("pop.provider.net")  # the person types their own
    dialog.email.textEdited.emit("rob@example.net")
    assert dialog.incoming_host.text() == "pop.provider.net" and dialog.outgoing_host.text() == "mail.example.net"


def test_missing_fields_are_pointed_out_and_nothing_happens(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    dialog.add_button.click()
    dialog.test_button.click()
    assert "Please fill in" in dialog.result_label.text()
    qtbot.wait(200)
    assert blank.call("account.list") == () and dialog.created_account_id is None


def test_the_connection_test_reports_both_servers(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.test_button.click()
    wait(qtbot, lambda: "✓ Incoming" in dialog.result_label.text() and "✓ Outgoing" in dialog.result_label.text())
    assert dialog.add_button.isEnabled() and blank.call("account.list") == ()  # a test creates nothing


def test_a_wrong_password_shows_a_cross_without_the_password(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    fill(dialog, blank, password="not-the-password-123")
    dialog.test_button.click()
    wait(qtbot, lambda: "✗ Incoming" in dialog.result_label.text() and "✗ Outgoing" in dialog.result_label.text())
    assert "not-the-password-123" not in dialog.result_label.text()


def test_an_unreachable_server_is_reported(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.incoming_port.setValue(1)
    dialog.test_button.click()
    wait(qtbot, lambda: "✗ Incoming" in dialog.result_label.text() and "✓ Outgoing" in dialog.result_label.text())


def test_adding_the_account(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend, first_account=True)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.policy.setCurrentIndex(dialog.policy.findData("delete_after_download"))
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.add_button.click()
    accounts = blank.call("account.list")
    assert len(accounts) == 1 and accounts[0].email_address == "new@example.com" and accounts[0].delete_policy == "delete_after_download"
    assert dialog.created_account_id == accounts[0].account_id and dialog.key_created is True and accounts[0].encrypted


def test_the_account_name_typed_when_adding_is_saved_apart_from_your_name(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend, first_account=True)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.account_name.setText("Kerio test")
    dialog.name.setText("Rob")
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.add_button.click()
    (account,) = blank.call("account.list")
    assert (account.account_name, account.display_name, account.shown_name) == ("Kerio test", "Rob", "Kerio test")


def test_a_separate_outgoing_login_typed_when_adding_is_saved(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend, first_account=True)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.same_login.setChecked(False)
    dialog.add_button.click()
    wait(qtbot, lambda: "outgoing server" in dialog.result_label.text())  # its own login must be filled in
    dialog.outgoing_username.setText("smtp-user")
    dialog.outgoing_password.setText("smtp-secret")
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.add_button.click()
    (account,) = blank.call("account.list")
    assert (account.outgoing_same_login, account.outgoing_username, account.incoming_username) == (False, "smtp-user", "user")


def test_without_encryption_no_key_is_created(blank: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog.encrypt.setChecked(False)
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.add_button.click()
    assert dialog.key_created is False and blank.call("key.status").key_id is None


def test_a_second_account_with_the_same_user_name_on_the_same_server_is_refused_in_the_dialog(world: World, qtbot: QtBot) -> None:
    dialog = AccountDialog(world.backend)
    qtbot.addWidget(dialog)
    fill(dialog, world)
    dialog.email.setText("other@example.com")
    dialog.add_button.click()
    wait(qtbot, lambda: "could not be added" in dialog.result_label.text())
    assert "exists already" in dialog.result_label.text() and dialog.add_button.isEnabled()
    assert dialog.created_account_id is None and len(world.call("account.list")) == 1


# --- the recovery dialog ------------------------------------------------------------------------------------------------------------


def test_the_recovery_code_is_shown_and_the_dialog_cannot_be_closed_lightly(world: World, qtbot: QtBot) -> None:
    dialog = RecoveryDialog(world.backend)
    qtbot.addWidget(dialog)
    expected = world.call("key.recovery-code").code
    wait(qtbot, lambda: dialog.code.toPlainText() == expected)
    assert not dialog.done_button.isEnabled()
    dialog.confirm.setChecked(True)
    assert dialog.done_button.isEnabled()
    dialog.confirm.setChecked(False)
    assert not dialog.done_button.isEnabled()


def test_the_code_can_be_copied(world: World, qtbot: QtBot) -> None:
    dialog = RecoveryDialog(world.backend)
    qtbot.addWidget(dialog)
    expected = world.call("key.recovery-code").code
    wait(qtbot, lambda: dialog.code.toPlainText() == expected)
    dialog.copy_button.click()
    assert QGuiApplication.clipboard().text() == expected


def test_a_recovery_file_can_be_saved_and_restores_the_key(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = RecoveryDialog(world.backend)
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: dialog.code.toPlainText() not in ("Loading…", ""))
    target = tmp_path / "backup.key"
    answer_save_file(monkeypatch, target)
    answer_input(monkeypatch, [("a long passphrase", True)])
    dialog.file_button.click()
    wait(qtbot, lambda: target.exists())
    wait(qtbot, lambda: any(title == "Recovery file" and "was saved" in text for _, title, text in world.dialogs.shown))
    assert sys.platform == "win32" or oct(target.stat().st_mode & 0o777) == "0o600"  # Windows has no such permissions
    assert world.call("key.restore-file", path=str(target), passphrase="a long passphrase").in_credential_store is True


def test_different_passphrases_or_cancelling_save_nothing(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = RecoveryDialog(world.backend)
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: dialog.code.toPlainText() not in ("Loading…", ""))
    target = tmp_path / "never.key"
    answer_save_file(monkeypatch, target)
    answer_input(monkeypatch, [("first", True), ("second", True)])
    dialog.file_button.click()
    assert any("not the same" in text for _, _, text in world.dialogs.shown) and not target.exists()
    answer_input(monkeypatch, [("", True)])
    dialog.file_button.click()
    answer_input(monkeypatch, [("x", False)])
    dialog.file_button.click()
    answer_save_file(monkeypatch, Path(""))
    dialog.file_button.click()
    qtbot.wait(200)
    assert not target.exists() and [p for p in tmp_path.iterdir() if p.suffix == ".key"] == []


def test_without_a_key_the_dialog_says_so(blank: World, qtbot: QtBot) -> None:
    dialog = RecoveryDialog(blank.backend)
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: dialog.code.toPlainText() == "(the code could not be shown)")
    assert any(kind == "warning" for kind, _, _ in blank.dialogs.shown)


# --- IMAP accounts (0.0.31) -------------------------------------------------------------------------------------------------------------


def test_choosing_imap_changes_the_ports_the_title_and_the_copies_option(qtbot: QtBot, world: World) -> None:
    dialog = AccountDialog(world.backend)
    qtbot.addWidget(dialog)
    assert dialog.kind.currentData() == "pop3" and dialog.incoming_port.value() == 995 and dialog.policy.isEnabled()
    dialog.kind.setCurrentIndex(dialog.kind.findData("imap"))
    assert dialog.incoming_port.value() == 993 and dialog.incoming_group.title() == "Incoming mail (IMAP)" and not dialog.policy.isEnabled()
    dialog.incoming_security.setCurrentIndex(dialog.incoming_security.findData("starttls"))
    assert dialog.incoming_port.value() == 143
    assert dialog._values().kind == "imap"  # noqa: SLF001
    dialog.kind.setCurrentIndex(dialog.kind.findData("pop3"))
    assert dialog.incoming_port.value() == 110 and dialog.policy.isEnabled() and dialog._values().kind == "pop3"  # noqa: SLF001


def test_the_window_cannot_close_while_the_account_is_being_added(blank: World, qtbot: QtBot) -> None:
    """RV-02: Cancel during Add account closed the window: the account and its key were made, the recovery code never shown."""
    dialog = AccountDialog(blank.backend, first_account=True)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, blank)
    closed: list[int] = []
    dialog.finished.connect(closed.append)
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.add_button.click()
        assert dialog.cancel_button is not None and not dialog.cancel_button.isEnabled()  # greyed, not hidden
        dialog.reject()  # Escape or the window's close button
        assert closed == []
    assert closed == [AccountDialog.DialogCode.Accepted] and dialog.show_recovery_code  # the new key's code is shown next


def test_done_notes_that_the_recovery_code_is_stored(blank: World, qtbot: QtBot) -> None:
    """RV-03: until Done with the tick, the code is shown again before a restore and after an account is added."""
    assert blank.call("key.ensure").show_code
    dialog = RecoveryDialog(blank.backend)
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: dialog.code.toPlainText() not in ("", "Loading…"))
    dialog.confirm.setChecked(True)
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.done_button.click()
    assert not blank.call("key.ensure").show_code


def test_while_adding_nothing_else_in_the_window_can_be_used(blank: World, qtbot: QtBot) -> None:
    """RV-207, RV-208: Restore a backup… and the certificate's test stayed usable while the account was being added; the test cleared the
    guard, so Cancel closed the window half-way and Add could start twice."""
    restores: list[str] = []
    dialog = AccountDialog(blank.backend, first_account=True, restore=lambda: restores.append("restore"))
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, blank)
    with qtbot.waitSignal(dialog.accepted, timeout=8000):
        dialog.add_button.click()
        assert not any(widget.isEnabled() for widget in (dialog.restore_button, dialog.test_button, dialog.email, dialog.password))
        dialog.restore_button.click()  # greyed: nothing happens
        dialog._restore_instead()  # noqa: SLF001 - and not even when reached another way
    qtbot.wait(50)
    assert restores == []


def test_while_a_calendar_account_is_added_find_cannot_start_a_second_add(blank: World, qtbot: QtBot) -> None:
    """RV-209: during Adding… Find and the fields stayed usable; a finished Find enabled Add again, and a second add could start."""
    from chrono_mail.ui.accountdialog import CALDAV

    dialog = AccountDialog(blank.backend, start_kind=CALDAV)
    qtbot.addWidget(dialog)
    fields = dialog.calendar
    dialog._set_adding(True)  # noqa: SLF001 - the state while Add runs
    assert not any(widget.isEnabled() for widget in (fields.find_button, fields.url, fields.username, fields.password, dialog.add_button, dialog.kind))
    assert dialog.cancel_button is not None and not dialog.cancel_button.isEnabled()
    dialog._set_adding(False)  # noqa: SLF001 - it failed: the person may try again
    assert fields.find_button.isEnabled() and dialog.cancel_button.isEnabled() and not dialog.add_button.isEnabled()  # Add needs a Find


def test_the_recovery_code_cannot_be_ticked_as_stored_before_it_is_shown(blank: World, qtbot: QtBot) -> None:
    """RV-210: the tick and Done worked while the code showed "Loading…" or could not be shown; Done then noted it as stored for good."""
    dialog = RecoveryDialog(blank.backend)
    qtbot.addWidget(dialog)
    assert not dialog.confirm.isEnabled() and not dialog.copy_button.isEnabled()  # "Loading…"
    wait(qtbot, lambda: dialog.code.toPlainText() not in ("", "Loading…"))
    shown = dialog.code.toPlainText() != "(the code could not be shown)"
    assert dialog.confirm.isEnabled() == shown and dialog.copy_button.isEnabled() == shown


def test_a_test_that_ends_during_an_add_never_brings_add_back(blank: World, qtbot: QtBot) -> None:
    """The review of RV-208: a test started from the certificate before Add answered during the add and made Add usable again."""
    dialog = AccountDialog(blank.backend)
    qtbot.addWidget(dialog)
    fill(dialog, blank)
    dialog._set_adding(True)  # noqa: SLF001 - the state while Add runs
    dialog._test()  # noqa: SLF001 - what an accepted certificate starts: nothing while adding
    dialog._busy(False)  # noqa: SLF001 - and a test that answers now
    assert not dialog.add_button.isEnabled() and not dialog.test_button.isEnabled() and dialog.result_label.text() != "Testing…"



def test_a_code_that_could_not_be_shown_is_asked_again_with_try_again(blank: World, qtbot: QtBot) -> None:
    """The open question of 3.6.349: a code that could not be read left only "Stop the restore"; Try again asks for it again."""
    dialog = RecoveryDialog(blank.backend, restoring=True)
    qtbot.addWidget(dialog)
    assert not dialog.retry_button.isEnabled()  # greyed while the code is on its way
    wait(qtbot, lambda: dialog.code.toPlainText() == "(the code could not be shown)")  # no key yet: the password store has nothing
    assert dialog.retry_button.isEnabled() and not dialog.confirm.isEnabled()
    blank.call("key.ensure")  # the password store answers now
    expected = blank.call("key.recovery-code").code
    dialog.retry_button.click()
    wait(qtbot, lambda: dialog.code.toPlainText() == expected)
    assert not dialog.retry_button.isEnabled() and dialog.confirm.isEnabled() and dialog.copy_button.isEnabled()
