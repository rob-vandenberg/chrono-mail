from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui.attachmentopening import open_attachment
from tests.test_engine_attachments import PROGRAM_MAIL
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_attachment_opening.py 2.0.302"

# --- Version history ----------------------------------------------------
# v2.0.302: Initial tests: Open attachment opens the private copy; a program only after the person said Open (D-236)


def program_message(world: World) -> str:
    with open_mailbox(world.engine, world.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), PROGRAM_MAIL, frozenset())
    return next(row.message_id for row in world.call("message.list", account=world.account_id, folder="Inbox").messages if row.subject == "A program")


def test_a_program_is_opened_only_after_the_person_said_open(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mail is hostile input (D-236): one double click must never run a stranger's program."""
    opened: list[str] = []
    problems: list[str] = []

    def record_open(url: QUrl) -> bool:
        opened.append(url.toLocalFile())
        return True

    def record_problem(text: str, error: ChronoMailError | None) -> None:
        problems.append(text)

    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(record_open))
    message = program_message(world)
    where = (world.account_id, "Inbox", message)
    open_attachment(world.window, world.backend, where, 0, "Setup.EXE", record_problem, world.window)
    wait(qtbot, lambda: any(title == "Open a program?" for _, title, _ in world.dialogs.shown))
    assert opened == [] and problems == []  # the question was answered with Ok, not Open: nothing runs

    def say_open(parent: QWidget, title: str, text: str, *args: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Open

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(say_open))
    open_attachment(world.window, world.backend, where, 0, "Setup.EXE", record_problem, world.window)
    wait(qtbot, lambda: bool(opened))
    assert Path(opened[0]).name == "Setup.EXE" and Path(opened[0]).read_bytes() == b"MZ"


def test_an_ordinary_file_opens_at_once_and_a_missing_program_is_told(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_mime_parse import PICTURE_IN_A_PLAIN_TEXT_MESSAGE

    with open_mailbox(world.engine, world.account_id) as mailbox:
        mailbox.deliver(mailbox.folder("Inbox"), PICTURE_IN_A_PLAIN_TEXT_MESSAGE, frozenset())
    message = next(row.message_id for row in world.call("message.list", account=world.account_id, folder="Inbox").messages if row.subject == "A picture")
    answers: list[bool] = [False]
    opened: list[str] = []
    problems: list[str] = []

    def record_open(url: QUrl) -> bool:
        opened.append(url.toLocalFile())
        return answers[0]

    def record_problem(text: str, error: ChronoMailError | None) -> None:
        problems.append(text)

    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(record_open))
    open_attachment(world.window, world.backend, (world.account_id, "Inbox", message), 0, "photo.png", record_problem, world.window)
    wait(qtbot, lambda: bool(problems))
    assert Path(opened[0]).name == "photo.png" and problems == ["No program on this computer opens photo.png"] and world.dialogs.shown == []
