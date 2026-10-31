from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.core import CertificateTrouble
from chrono_mail.engine.commands_mail import SendResult
from chrono_mail.ui.certificatedialog import VerifyCertificateDialog
from tests.certs import make_certificate
from tests.fakesmtp import FakeSmtpServer
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_send_certificate.py 4.2.442"

# --- Version history ----------------------------------------------------
# v4.2.442: Initial version: a Send stopped by the outgoing server's certificate asks once the message window has closed (D-299)


@pytest.fixture
def untrusted_smtp(tmp_path: Path) -> Generator[FakeSmtpServer]:
    (tmp_path / "smtp-certificate").mkdir()
    context, _ = make_certificate(tmp_path / "smtp-certificate")
    with FakeSmtpServer(implicit_tls=context) as server:
        yield server


def answer(monkeypatch: pytest.MonkeyPatch, connect: bool) -> list[str]:
    """Each Verify certificate window is answered at once; gives the windows that were open over it when each opened."""
    seen: list[str] = []

    def opened(self: VerifyCertificateDialog) -> None:
        parent = self.parentWidget()
        seen.append(type(parent).__name__ if parent is not None else "")
        self.done(QDialog.DialogCode.Accepted if connect else QDialog.DialogCode.Rejected)

    monkeypatch.setattr(VerifyCertificateDialog, "open", opened)
    return seen


def stopped_send(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, server: FakeSmtpServer) -> tuple[list[tuple[str | None, str | None]], list[str]]:
    """A message window whose Send was stopped by the outgoing server's certificate; gives the Outbox sends asked for and the closed windows."""
    sends: list[tuple[str | None, str | None]] = []
    checking = world.window.checking

    def send_outbox(then: object = None, manual: bool = False, account: str | None = None, message: str | None = None, now: bool = False) -> None:
        sends.append((account, message))

    monkeypatch.setattr(checking, "send_outbox", send_outbox)  # a stand-in that records what Connect asks for
    window = world.window.composing.open_composer(world.account_id, None, None)
    closed: list[str] = []
    window.destroyed.connect(lambda: closed.append("closed"))
    trouble = CertificateTrouble("127.0.0.1", server.port, "tls", "smtp", changed=False, account=world.account_id)
    window._sent(SendResult(False, "message-1", "Outbox", (), "The server's certificate could not be verified", None, certificate=trouble))  # noqa: SLF001 - the engine's answer to Send
    wait(qtbot, lambda: closed == ["closed"])
    return sends, closed


def test_a_send_stopped_by_a_certificate_closes_without_its_box_and_connect_sends_that_message(
    world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, untrusted_smtp: FakeSmtpServer,
) -> None:
    seen = answer(monkeypatch, connect=True)
    sends, _ = stopped_send(world, qtbot, monkeypatch, untrusted_smtp)
    wait(qtbot, lambda: sends == [(world.account_id, "message-1")])
    assert seen == ["MainWindow"] and not any(kind == "warning" for kind, _, _ in world.dialogs.shown)  # over the main window, no box


def test_do_not_connect_leaves_the_message_in_the_outbox_and_says_so(
    world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, untrusted_smtp: FakeSmtpServer,
) -> None:
    seen = answer(monkeypatch, connect=False)
    sends, _ = stopped_send(world, qtbot, monkeypatch, untrusted_smtp)
    wait(qtbot, lambda: len(seen) == 1)
    qtbot.wait(200)
    assert sends == [] and world.window.status_label.text() == "The message waits in your Outbox: the certificate of 127.0.0.1 could not be verified"


def test_any_other_failed_send_keeps_its_box(world: World, qtbot: QtBot) -> None:
    window = world.window.composing.open_composer(world.account_id, None, None)
    window._sent(SendResult(False, "message-2", "Outbox", (), "The server refused the message", None))  # noqa: SLF001 - the engine's answer to Send
    assert any(kind == "warning" and title == "The message was not sent" for kind, title, _ in world.dialogs.shown)


def test_a_send_asks_again_after_do_not_connect_earlier_in_this_run(
    world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, untrusted_smtp: FakeSmtpServer,
) -> None:
    seen = answer(monkeypatch, connect=False)
    trouble = CertificateTrouble("127.0.0.1", untrusted_smtp.port, "tls", "smtp", changed=False, account=world.account_id)
    world.window.checking._ask_about(trouble, lambda: None)  # noqa: SLF001 - a Refresh met it first, and the person chose Do not connect
    wait(qtbot, lambda: len(seen) == 1)
    stopped_send(world, qtbot, monkeypatch, untrusted_smtp)  # then the person's own Send
    wait(qtbot, lambda: len(seen) == 2)
