from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.protocols.tls import readable
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatedialog import CertificateDialog
from tests.certs import make_certificate
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.ui.world import REGISTRY, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_certificates.py 0.0.72"

# --- Version history ----------------------------------------------------
# v0.0.72: Accepted certificates belong to one server (host and port)
# v0.0.71: A certificate accepted for an address that was changed afterwards is not used
# v0.0.43: Initial certificate exception window tests


class Setup:
    def __init__(self, backend: Backend, pop: FakePop3Server, smtp: FakeSmtpServer) -> None:
        self.backend, self.pop, self.smtp = backend, pop, smtp


@pytest.fixture
def secure(tmp_path: Path) -> Generator[Setup]:
    (tmp_path / "c").mkdir()
    (tmp_path / "d").mkdir()
    server_context, _ = make_certificate(tmp_path / "c")
    smtp_context, _ = make_certificate(tmp_path / "d")  # a certificate of its own
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({}, implicit_tls=server_context) as pop, FakeSmtpServer(implicit_tls=smtp_context) as smtp:
        backend = Backend(engine)
        yield Setup(backend, pop, smtp)
        backend.wait_for_done(5000)


def fill(dialog: AccountDialog, setup: Setup) -> None:
    dialog.name.setText("Me")
    dialog.email.setText("me@example.com")
    dialog.username.setText("user")
    dialog.password.setText("secret")
    dialog.incoming_host.setText("127.0.0.1")
    dialog.outgoing_host.setText("127.0.0.1")
    dialog.incoming_security.setCurrentIndex(dialog.incoming_security.findData("tls"))
    dialog.incoming_port.setValue(setup.pop.port)
    dialog.outgoing_security.setCurrentIndex(dialog.outgoing_security.findData("tls"))
    dialog.outgoing_port.setValue(setup.smtp.port)


def accept_certificates(monkeypatch: pytest.MonkeyPatch, shown: list[CertificateDialog], accept: bool = True) -> None:
    def fake_exec(self: CertificateDialog) -> QDialog.DialogCode:
        shown.append(self)
        return QDialog.DialogCode.Accepted if accept and self.accept_button.isEnabled() else QDialog.DialogCode.Rejected

    monkeypatch.setattr(CertificateDialog, "exec", fake_exec)


def test_an_untrusted_server_leads_to_the_certificate_and_then_to_a_working_account(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = AccountDialog(secure.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, secure)
    assert dialog.certificate_button.isHidden()
    dialog.test_button.click()
    wait(qtbot, lambda: "certificate" in dialog.result_label.text().lower() and not dialog.certificate_button.isHidden())
    shown: list[CertificateDialog] = []
    accept_certificates(monkeypatch, shown)
    dialog.certificate_button.click()
    wait(qtbot, lambda: len(shown) == 2 and len(dialog.trusted) == 2)  # the incoming and the outgoing server
    assert all(item.accept_button.isEnabled() for item in shown) and "not trust" in shown[0].findChildren(QLabel)[0].text()
    wait(qtbot, lambda: dialog.result_label.text().count("✓") == 2, timeout=15000)  # the test was repeated with what was accepted
    assert dialog.certificate_button.isHidden()
    dialog.add_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    created = REGISTRY.run(secure.backend.engine, "account.list", {})
    assert isinstance(created, tuple) and set(getattr(created[0], "trusted_certificates")) == set(dialog.trusted) and all("=" in pin for pin in dialog.trusted)


def test_a_certificate_accepted_for_an_address_that_was_changed_is_dropped(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = AccountDialog(secure.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, secure)
    dialog.test_button.click()
    wait(qtbot, lambda: not dialog.certificate_button.isHidden())
    shown: list[CertificateDialog] = []
    accept_certificates(monkeypatch, shown)
    dialog.certificate_button.click()
    wait(qtbot, lambda: len(shown) == 2 and len(dialog.trusted) == 2)
    incoming_pin, outgoing_pin = (item.fingerprint for item in shown)
    dialog.outgoing_host.setText("localhost")  # the outgoing server address was wrong and is corrected
    assert [pin.split("=")[1] for pin in dialog.trusted] == [incoming_pin] and outgoing_pin not in "".join(dialog.trusted)
    dialog.outgoing_host.setText("127.0.0.1")  # back to the address the certificate was accepted for
    assert {pin.split("=")[1] for pin in dialog.trusted} == {incoming_pin, outgoing_pin}
    dialog.incoming_port.setValue(dialog.incoming_port.value() + 1)
    assert [pin.split("=")[1] for pin in dialog.trusted] == [outgoing_pin]
    dialog.outgoing_security.setCurrentIndex(dialog.outgoing_security.findData("starttls"))
    assert dialog.trusted == []


def test_cancelling_the_certificate_dialog_accepts_nothing(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = AccountDialog(secure.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, secure)
    dialog.test_button.click()
    wait(qtbot, lambda: not dialog.certificate_button.isHidden())
    shown: list[CertificateDialog] = []
    accept_certificates(monkeypatch, shown, accept=False)
    dialog.certificate_button.click()
    wait(qtbot, lambda: len(shown) >= 1)
    qtbot.wait(300)
    assert dialog.trusted == [] and "certificate" in dialog.result_label.text().lower()


def test_the_certificate_window_shows_the_facts_and_a_trusted_certificate_has_nothing_to_accept(qtbot: QtBot) -> None:
    class Info:
        fingerprint = "ab" * 32
        subject = "mail.example.org"
        issuer = "Home CA"
        names = ("mail.example.org", "imap.example.org")
        not_before = "2026-01-01"
        not_after = "2026-12-31"
        expired = True
        problem: str | None = "the issuer is not known"

    dialog = CertificateDialog("mail.example.org", Info())
    qtbot.addWidget(dialog)
    text = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert readable("ab" * 32) in text and "Home CA" in text and "expired" in text and "the issuer is not known" in text and dialog.accept_button.isEnabled()
    Info.problem = None
    fine = CertificateDialog("mail.example.org", Info())
    qtbot.addWidget(fine)
    assert not fine.accept_button.isEnabled() and "nothing to accept" in " ".join(label.text() for label in fine.findChildren(QLabel))


def test_a_certificate_that_cannot_be_read_is_reported(qtbot: QtBot, secure: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = AccountDialog(secure.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, secure)
    dialog.incoming_port.setValue(1)  # nothing listens
    dialog.test_button.click()
    wait(qtbot, lambda: "Incoming" in dialog.result_label.text())
    assert dialog._cert_servers == ['outgoing']  # a refused connection is not a certificate problem  # noqa: SLF001


def test_the_edit_window_accepts_and_forgets_certificates(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    pop_pin = str(getattr(REGISTRY.run(secure.backend.engine, "certificate.inspect", {"host": "127.0.0.1", "port": secure.pop.port, "security": "tls", "kind": "pop3"}), "fingerprint"))
    REGISTRY.run(
        secure.backend.engine, "account.add",
        {
            "display_name": "Me", "email_address": "me@example.com", "username": "user", "password": "secret", "incoming_host": "127.0.0.1", "incoming_port": secure.pop.port,
            "incoming_security": "tls", "outgoing_host": "127.0.0.1", "outgoing_port": secure.smtp.port, "outgoing_security": "tls", "trusted_certificate": [pop_pin],
        },
    )
    listed = REGISTRY.run(secure.backend.engine, "account.list", {})
    assert isinstance(listed, tuple)
    edit = AccountEditDialog(secure.backend, listed[0])
    qtbot.addWidget(edit)
    edit.show()
    assert "1 server certificate" in edit.certificates_label.text() and edit.forget_certificates_button.isEnabled()
    shown: list[CertificateDialog] = []
    accept_certificates(monkeypatch, shown)
    edit.outgoing_certificate_button.click()
    wait(qtbot, lambda: "2 server certificate" in edit.certificates_label.text())
    assert shown[0].accept_button.isEnabled()
    edit.password.setText("secret")
    edit.test_button.click()
    wait(qtbot, lambda: edit.result_label.text().count("✓") == 2, timeout=15000)  # the test uses the accepted certificates

    def yes(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(yes))
    edit.forget_certificates_button.click()
    wait(qtbot, lambda: "No server certificate" in edit.certificates_label.text() and not edit.forget_certificates_button.isEnabled())
