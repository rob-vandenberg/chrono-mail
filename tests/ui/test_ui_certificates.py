from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.core import CertificateTrouble
from chrono_mail.engine.commands_accounts import ACCOUNT_LIST, CertificateResult
from chrono_mail.protocols.tls import readable
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.certificatedialog import CHANGED_REASON, CertificateDialog, VerifyCertificateDialog
from chrono_mail.ui.certificatequestions import questions_for
from chrono_mail.ui.scale import wpx
from chrono_mail.ui.serverfields import SECURITY_CHOICES
from tests.certs import make_certificate
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.ui.world import REGISTRY, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_certificates.py 4.2.442"

# --- Version history ----------------------------------------------------
# v4.2.442: the heading sits next to the sign; a blank line above and below the certificate information
# v4.2.441: a certificate that could not be verified asks at once (Verify certificate): Connect with and without Don't ask me again, Do not
#           connect, a changed certificate, a Refresh asks only while the window is on screen; the weak Security choice (UR-05, D-299)
# v3.0.339: the texts of a certificate are shown as text, never as layout (RV-01)
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


def answer(monkeypatch: pytest.MonkeyPatch, shown: list[VerifyCertificateDialog], connect: bool = True, remember: bool = True) -> list[str]:
    """Every Verify certificate window that opens is answered: Connect (with or without Don't ask me again) or Do not connect. Gives the texts
    of each window as it opened (it is deleted after its answer)."""
    shown_texts: list[str] = []

    def opened(self: VerifyCertificateDialog) -> None:
        shown_texts.append(texts(self))  # read before the answer: the window is deleted after it
        shown.append(self)
        self.dont_ask.setChecked(remember)
        self.done(QDialog.DialogCode.Accepted if connect else QDialog.DialogCode.Rejected)

    monkeypatch.setattr(VerifyCertificateDialog, "open", opened)
    return shown_texts


def pins_of_the_account(backend: Backend) -> set[str]:
    listed = REGISTRY.run(backend.engine, ACCOUNT_LIST.id, {})
    assert isinstance(listed, tuple)
    return set(getattr(listed[0], "trusted_certificates"))


def test_an_untrusted_server_asks_at_once_and_connect_with_the_tick_keeps_it_with_the_account(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown)
    dialog = AccountDialog(secure.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, secure)
    dialog.test_button.click()
    wait(qtbot, lambda: len(shown) == 2 and len(dialog.trusted) == 2)  # the incoming and the outgoing server, one window after the other
    assert {item.host for item in shown} == {"127.0.0.1"}
    wait(qtbot, lambda: dialog.result_label.text().count("✓") == 2, timeout=15000)  # Connect tested again with what was accepted
    dialog.add_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    assert pins_of_the_account(secure.backend) == set(dialog.trusted) and all("=" in pin for pin in dialog.trusted)


def test_connect_without_the_tick_trusts_it_until_chrono_mail_closes(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown, remember=False)
    dialog = AccountDialog(secure.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, secure)
    dialog.test_button.click()
    wait(qtbot, lambda: dialog.result_label.text().count("✓") == 2, timeout=15000)
    assert len(shown) == 2 and dialog.trusted == [] and len(secure.backend.engine.pins()) == 2
    dialog.add_button.click()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)
    assert pins_of_the_account(secure.backend) == set()  # nothing kept: the next start asks again


def test_a_certificate_accepted_for_an_address_that_was_changed_is_dropped(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown)
    dialog = AccountDialog(secure.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, secure)
    dialog.test_button.click()
    wait(qtbot, lambda: len(shown) == 2 and len(dialog.trusted) == 2)
    incoming_pin, outgoing_pin = (item.info.fingerprint for item in shown)
    dialog.outgoing_host.setText("localhost")  # the outgoing server address was wrong and is corrected
    assert [pin.split("=")[1] for pin in dialog.trusted] == [incoming_pin] and outgoing_pin not in "".join(dialog.trusted)
    dialog.outgoing_host.setText("127.0.0.1")  # back to the address the certificate was accepted for
    assert {pin.split("=")[1] for pin in dialog.trusted} == {incoming_pin, outgoing_pin}
    dialog.incoming_port.setValue(dialog.incoming_port.value() + 1)
    assert [pin.split("=")[1] for pin in dialog.trusted] == [outgoing_pin]


def test_do_not_connect_accepts_nothing_and_a_new_test_asks_again(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown, connect=False)
    dialog = AccountDialog(secure.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, secure)
    dialog.test_button.click()
    wait(qtbot, lambda: len(shown) == 2)
    qtbot.wait(200)
    assert dialog.trusted == [] and secure.backend.engine.pins() == () and "certificate" in dialog.result_label.text().lower()
    dialog.test_button.click()  # a test the person starts always asks
    wait(qtbot, lambda: len(shown) == 4)


def test_a_refused_connection_asks_nothing(qtbot: QtBot, secure: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown, connect=False)
    dialog = AccountDialog(secure.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, secure)
    dialog.incoming_port.setValue(1)  # nothing listens
    dialog.test_button.click()
    wait(qtbot, lambda: "Incoming" in dialog.result_label.text() and len(shown) == 1)
    qtbot.wait(200)
    assert len(shown) == 1  # only the outgoing server's certificate is asked about


def test_the_edit_window_asks_after_a_test_and_keeps_and_forgets_certificates(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    pop_pin = str(getattr(REGISTRY.run(secure.backend.engine, "certificate.inspect", {"host": "127.0.0.1", "port": secure.pop.port, "security": "tls", "kind": "pop3"}), "fingerprint"))
    REGISTRY.run(
        secure.backend.engine, "account.add",
        {
            "display_name": "Me", "email_address": "me@example.com", "username": "user", "password": "secret", "incoming_host": "127.0.0.1", "incoming_port": secure.pop.port,
            "incoming_security": "tls", "outgoing_host": "127.0.0.1", "outgoing_port": secure.smtp.port, "outgoing_security": "tls", "trusted_certificate": [pop_pin],
        },
    )
    listed = REGISTRY.run(secure.backend.engine, ACCOUNT_LIST.id, {})
    assert isinstance(listed, tuple)
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown)
    edit = AccountEditDialog(secure.backend, listed[0])
    qtbot.addWidget(edit)
    edit.show()
    assert "1 server certificate" in edit.certificates_label.text() and edit.forget_certificates_button.isEnabled()
    edit.test_button.click()
    wait(qtbot, lambda: "2 server certificate" in edit.certificates_label.text())  # the outgoing server asked; kept with the account
    wait(qtbot, lambda: edit.result_label.text().count("✓") == 2, timeout=15000)  # tested again
    assert len(shown) == 1 and len(pins_of_the_account(secure.backend)) == 2

    def yes(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(yes))
    edit.forget_certificates_button.click()
    wait(qtbot, lambda: "No server certificate" in edit.certificates_label.text() and not edit.forget_certificates_button.isEnabled())


def result(problem: str | None = "self-signed certificate", expired: bool = False) -> CertificateResult:
    return CertificateResult(
        fingerprint="ab" * 32, subject="mail.example.org", issuer="Home CA", names=("mail.example.org", "imap.example.org"), not_before="2026-01-01",
        not_after="2027-12-31", expired=expired, accepted_by_default=problem is None, problem=problem,
    )


def texts(widget: QWidget) -> str:
    return " ".join(label.text() for label in widget.findChildren(QLabel))


def test_the_verify_window_says_who_why_and_what_and_offers_the_three_answers(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = VerifyCertificateDialog("mail.example.org", result(expired=True), changed=False)
    qtbot.addWidget(dialog)
    shown = texts(dialog)
    assert dialog.windowTitle() == "Verify certificate" and "can't verify the identity of “mail.example.org”" in shown
    assert "Self-signed certificate." in shown and "pretending to be “mail.example.org”" in shown and "connect to the server anyway?" in shown
    assert "Subject" in shown and "Issued by" in shown and "Home CA" in shown and "Expiration date" in shown and "2027-12-31 (expired)" in shown
    assert [dialog.connect_button.text(), dialog.refuse_button.text(), dialog.show_button.text()] == ["Connect", "Do not connect", "Show certificate"]
    assert dialog.dont_ask.text() == "Don't ask me again" and not dialog.dont_ask.isChecked() and not dialog.remember
    assert dialog.refuse_button.isDefault()  # Enter never connects by itself
    details: list[CertificateDialog] = []
    def opened(self: CertificateDialog) -> None:
        details.append(self)

    monkeypatch.setattr(CertificateDialog, "open", opened)
    dialog.show_button.click()
    assert len(details) == 1 and readable("ab" * 32) in texts(details[0]) and dialog.result() == 0  # the question stays open
    changed = VerifyCertificateDialog("mail.example.org", result(), changed=True)
    qtbot.addWidget(changed)
    assert CHANGED_REASON in texts(changed)


def test_the_certificate_window_only_shows_the_certificate(qtbot: QtBot) -> None:
    dialog = CertificateDialog("mail.example.org", result("the issuer is not known", expired=True))
    qtbot.addWidget(dialog)
    shown = texts(dialog)
    assert readable("ab" * 32) in shown and "Home CA" in shown and "expired" in shown and "the issuer is not known" in shown
    fine = CertificateDialog("mail.example.org", result(None))
    qtbot.addWidget(fine)
    assert "trusted by the usual checks" in texts(fine)


def test_the_texts_of_a_certificate_are_shown_as_text_never_as_layout(qtbot: QtBot) -> None:
    """A forged certificate whose names hold markup must not draw rows of its own, such as a fake fingerprint (RV-01)."""
    forged = "<tr><td>SHA-256</td><td>AA:BB</td></tr>"
    info = CertificateResult(
        fingerprint="ab" * 32, subject=forged, issuer=f"<b>{forged}</b>", names=(forged,), not_before="2026-01-01", not_after="2027-01-01",
        expired=False, accepted_by_default=False, problem="<i>self-signed</i>",
    )
    dialog = CertificateDialog("<img src=x>", info)
    qtbot.addWidget(dialog)
    shown = "".join(label.text() for label in dialog.findChildren(QLabel))
    assert "<tr><td>SHA-256</td><td>AA:BB" not in shown and "&lt;tr&gt;&lt;td&gt;SHA-256" in shown
    assert "<img" not in shown and "<i>self-signed" not in shown
    assert shown.count("<td>SHA-256</td>") == 1  # only the real fingerprint row
    question = VerifyCertificateDialog("<img src=x>", info, changed=False)
    qtbot.addWidget(question)
    asked = texts(question)
    assert "<img" not in asked and "<i>self-signed" not in asked and "<tr><td>SHA-256" not in asked


def test_a_refresh_asks_only_while_the_window_is_on_screen_and_not_again_after_do_not_connect(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown, connect=False)
    window = QWidget()
    qtbot.addWidget(window)
    on_screen = [False]
    questions = questions_for(secure.backend)
    questions.watch(window, lambda: on_screen[0])
    retried: list[str] = []
    trouble = CertificateTrouble("127.0.0.1", secure.pop.port, "tls", "pop3", changed=False)
    questions.ask(trouble, lambda: retried.append("again"))
    questions.ask(trouble, lambda: retried.append("again"))  # met twice: asked once
    qtbot.wait(300)
    assert shown == []  # the window is not on screen: it waits
    on_screen[0] = True
    window.show()  # the window comes back
    wait(qtbot, lambda: len(shown) == 1)
    qtbot.wait(200)
    assert len(shown) == 1 and retried == []
    questions.ask(trouble, lambda: retried.append("again"))  # Do not connect: a Refresh of this run asks no more
    qtbot.wait(300)
    assert len(shown) == 1


def test_connect_from_a_refresh_does_the_work_again(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    shown_texts = answer(monkeypatch, shown, remember=False)
    retried: list[str] = []
    questions_for(secure.backend).ask(CertificateTrouble("127.0.0.1", secure.pop.port, "tls", "pop3", changed=True), lambda: retried.append("again"))
    wait(qtbot, lambda: retried == ["again"])
    assert CHANGED_REASON in shown_texts[0] and len(secure.backend.engine.pins()) == 1


def test_the_security_box_offers_the_weak_choice_between_strong_and_none() -> None:
    assert [value for value, _ in SECURITY_CHOICES] == ["tls", "starttls", "tls-unchecked", "none"]
    assert dict(SECURITY_CHOICES)["tls-unchecked"] == "TLS, certificate not checked (weak)"


def test_a_question_whose_window_was_closed_is_dropped_and_the_next_one_still_asks(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown)
    closed = QWidget()
    closed.show()
    tested: list[str] = []
    questions = questions_for(secure.backend)
    questions.ask(CertificateTrouble("127.0.0.1", secure.pop.port, "tls", "pop3", changed=False), lambda: tested.append("closed window"), closed)
    closed.deleteLater()  # the Edit window was closed while its certificate was looked at
    QApplication.processEvents()
    retried: list[str] = []
    questions.ask(CertificateTrouble("127.0.0.1", secure.smtp.port, "tls", "smtp", changed=False), lambda: retried.append("again"))
    wait(qtbot, lambda: retried == ["again"])  # the queue never stalls on the closed window
    assert tested == [] and len(shown) <= 2


def test_dont_ask_me_again_keeps_the_certificate_with_every_account_that_met_the_server(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown)
    accounts: list[str] = []
    for address in ("one@example.com", "two@example.com"):
        created = REGISTRY.run(secure.backend.engine, "account.add", {
            "display_name": "Me", "email_address": address, "username": address, "password": "secret", "incoming_host": "127.0.0.1",
            "incoming_port": secure.pop.port, "incoming_security": "tls", "outgoing_host": "127.0.0.1", "outgoing_port": secure.smtp.port, "outgoing_security": "tls",
        })
        accounts.append(str(getattr(created, "account_id")))
    retried: list[str] = []
    questions = questions_for(secure.backend)
    for account in accounts:  # one Refresh meets the same server for both accounts
        questions.ask(CertificateTrouble("127.0.0.1", secure.pop.port, "tls", "pop3", changed=False, account=account), lambda account=account: retried.append(account))
    wait(qtbot, lambda: sorted(retried) == sorted(accounts))
    listed = REGISTRY.run(secure.backend.engine, ACCOUNT_LIST.id, {})
    assert isinstance(listed, tuple) and len(shown) == 1
    assert all(len(getattr(item, "trusted_certificates")) == 1 for item in listed)


def test_a_refresh_question_waits_when_another_question_opened_while_its_certificate_was_read(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown, connect=False)
    window = QWidget()
    qtbot.addWidget(window)
    window.show()
    on_screen = [True]
    questions = questions_for(secure.backend)
    questions.watch(window, lambda: on_screen[0])
    questions.ask(CertificateTrouble("127.0.0.1", secure.pop.port, "tls", "pop3", changed=False), lambda: None)
    on_screen[0] = False  # Settings opened while the certificate was read
    qtbot.wait(400)
    assert shown == []
    on_screen[0] = True
    window.hide()
    window.show()  # back
    wait(qtbot, lambda: len(shown) == 1)


def test_a_waiting_refresh_question_never_holds_back_the_one_the_person_waits_for(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown, remember=False)
    window = QWidget()
    qtbot.addWidget(window)
    questions = questions_for(secure.backend)
    questions.watch(window, lambda: False)  # the main window is not on screen: a Refresh's question waits
    retried: list[str] = []
    questions.ask(CertificateTrouble("127.0.0.1", secure.pop.port, "tls", "pop3", changed=False), lambda: retried.append("refresh"))
    tester = QWidget()
    qtbot.addWidget(tester)
    tester.show()
    questions.ask(CertificateTrouble("127.0.0.1", secure.smtp.port, "tls", "smtp", changed=False), lambda: retried.append("test"), tester)
    wait(qtbot, lambda: retried == ["test"])
    qtbot.wait(200)
    assert retried == ["test"] and len(shown) == 1  # the Refresh's still waits for the window


def test_keeps_of_a_closed_window_are_skipped_and_the_refresh_work_still_goes_on(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown)
    window = QWidget()
    qtbot.addWidget(window)
    on_screen = [False]
    questions = questions_for(secure.backend)
    questions.watch(window, lambda: on_screen[0])
    retried: list[str] = []
    kept: list[str] = []
    trouble = CertificateTrouble("127.0.0.1", secure.pop.port, "tls", "pop3", changed=False)
    questions.ask(trouble, lambda: retried.append("refresh"))
    edit = QWidget()
    edit.show()
    questions.ask(trouble, lambda: retried.append("edit"), edit, kept.append)  # merged with the Refresh's
    edit.deleteLater()  # closed before its certificate was read
    QApplication.processEvents()
    on_screen[0] = True
    window.show()
    wait(qtbot, lambda: retried == ["refresh"])
    assert kept == [] and len(shown) == 1


def test_an_account_gone_meanwhile_never_stops_the_others_keeping_the_certificate(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown)
    created = REGISTRY.run(secure.backend.engine, "account.add", {
        "display_name": "Me", "email_address": "one@example.com", "username": "one", "password": "secret", "incoming_host": "127.0.0.1",
        "incoming_port": secure.pop.port, "incoming_security": "tls", "outgoing_host": "127.0.0.1", "outgoing_port": secure.smtp.port, "outgoing_security": "tls",
    })
    account = str(getattr(created, "account_id"))
    retried: list[str] = []
    questions = questions_for(secure.backend)
    trouble = CertificateTrouble("127.0.0.1", secure.pop.port, "tls", "pop3", changed=False)
    questions.ask(replace(trouble, account="f" * 32), lambda: retried.append("gone"))  # an account removed meanwhile
    questions.ask(replace(trouble, account=account), lambda: retried.append("kept"))
    wait(qtbot, lambda: sorted(retried) == ["gone", "kept"])
    assert pins_of_the_account(secure.backend) and len(shown) == 1


def test_a_test_that_joins_a_refresh_question_being_read_is_asked_over_its_window(secure: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown, remember=False)
    window = QWidget()
    qtbot.addWidget(window)
    window.show()
    on_screen = [True]
    questions = questions_for(secure.backend)
    questions.watch(window, lambda: on_screen[0])
    retried: list[str] = []
    trouble = CertificateTrouble("127.0.0.1", secure.pop.port, "tls", "pop3", changed=False)
    questions.ask(trouble, lambda: retried.append("refresh"))  # its certificate is read now
    tester = QWidget()
    qtbot.addWidget(tester)
    tester.show()
    on_screen[0] = False  # the person's own window waits for an answer
    questions.ask(trouble, lambda: retried.append("test"), tester)
    wait(qtbot, lambda: sorted(retried) == ["refresh", "test"])
    assert len(shown) == 1


def test_the_heading_sits_next_to_the_sign_and_the_certificate_has_a_blank_line_above_and_below(qtbot: QtBot) -> None:
    dialog = VerifyCertificateDialog("mailserver.example.org", result(), changed=False)
    qtbot.addWidget(dialog)
    dialog.resize(700, dialog.sizeHint().height())
    dialog.show()
    qtbot.waitExposed(dialog)

    def labelled(name: str) -> QWidget:
        found = dialog.findChild(QWidget, name)
        assert found is not None
        return found

    sign, heading, question, details, tick = (labelled(name) for name in ("sign", "heading", "question", "details", "dontAsk"))
    gap = heading.mapTo(dialog, heading.rect().topLeft()).x() - sign.mapTo(dialog, sign.rect().topRight()).x()
    assert gap < 40  # the text column takes the room, not the sign (a huge gap before)
    line = wpx(16)
    below_question = details.mapTo(dialog, details.rect().topLeft()).y() - question.mapTo(dialog, question.rect().bottomLeft()).y()
    above_tick = tick.mapTo(dialog, tick.rect().topLeft()).y() - details.mapTo(dialog, details.rect().bottomLeft()).y()
    assert below_question >= line and above_tick >= line


@pytest.fixture
def one_machine(tmp_path: Path) -> Generator[Setup]:
    """Incoming and outgoing mail on one machine, with one certificate (two ports)."""
    (tmp_path / "c").mkdir()
    context, _ = make_certificate(tmp_path / "c")
    engine = make_engine(tmp_path / "e")
    with FakePop3Server({}, implicit_tls=context) as pop, FakeSmtpServer(implicit_tls=context) as smtp:
        backend = Backend(engine)
        yield Setup(backend, pop, smtp)
        backend.wait_for_done(5000)


@pytest.mark.parametrize("connect", [True, False], ids=["connect", "do-not-connect"])
def test_one_certificate_on_both_ports_of_a_server_asks_once(one_machine: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, connect: bool) -> None:
    shown: list[VerifyCertificateDialog] = []
    answer(monkeypatch, shown, connect=connect)
    dialog = AccountDialog(one_machine.backend)
    qtbot.addWidget(dialog)
    dialog.show()
    fill(dialog, one_machine)
    dialog.test_button.click()
    if connect:
        wait(qtbot, lambda: dialog.result_label.text().count("✓") == 2, timeout=15000)  # tested again once, with both ports accepted
        assert len(dialog.trusted) == 2 and len(one_machine.backend.engine.pins()) == 2
    else:
        wait(qtbot, lambda: len(shown) == 1)
        qtbot.wait(300)
        assert dialog.trusted == [] and one_machine.backend.engine.pins() == ()
    assert len(shown) == 1


def test_a_port_met_again_while_its_window_is_open_joins_that_window(one_machine: Setup, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    held: list[VerifyCertificateDialog] = []

    def opened(self: VerifyCertificateDialog) -> None:
        held.append(self)  # left open for now

    monkeypatch.setattr(VerifyCertificateDialog, "open", opened)
    tester = QWidget()
    qtbot.addWidget(tester)
    tester.show()
    questions = questions_for(one_machine.backend)
    retried: list[str] = []
    pop = CertificateTrouble("127.0.0.1", one_machine.pop.port, "tls", "pop3", changed=False)
    smtp = CertificateTrouble("127.0.0.1", one_machine.smtp.port, "tls", "smtp", changed=False)
    questions.ask(pop, lambda: retried.append("incoming"), tester)
    questions.ask(smtp, lambda: retried.append("outgoing"), tester)
    wait(qtbot, lambda: len(held) == 1)
    questions.ask(smtp, lambda: retried.append("outgoing again"), tester)  # met again while the window is open
    qtbot.wait(300)
    assert len(held) == 1
    held[0].done(QDialog.DialogCode.Rejected)  # Do not connect
    qtbot.wait(300)
    assert len(held) == 1 and retried == []
