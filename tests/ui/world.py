from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QModelIndex, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.engine import Engine, build_registry
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import KIND_ROLE
from tests.enginefactory import make_engine
from tests.fakepop3 import FakePop3Server
from tests.fakesmtp import FakeSmtpServer
from tests.mailfactory import raw_message

# --- Version ------------------------------------------------------------
__version__ = "world.py 0.0.73"

# --- Version history ----------------------------------------------------
# v0.0.73: The menus are inside one Menu button
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.15: Key recovery
# v0.0.13: Initial ui test world

REGISTRY = build_registry()


def mail(number: int, **changes: Any) -> bytes:  # noqa: ANN401 - forwarded to raw_message
    fields: dict[str, Any] = {"subject": f"Message {number}", "body": f"Body of message number {number}\r\n", "message_id": f"<m{number}@x>"}
    fields.update(changes)
    return raw_message(**fields)


@dataclass
class Dialogs:
    """Records the message boxes a test provokes and answers questions with a chosen button."""

    answer: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes
    shown: list[tuple[str, str, str]] = field(default_factory=list)  # kind, title, text


@dataclass
class World:
    engine: Engine
    backend: Backend
    window: MainWindow
    pop: FakePop3Server
    smtp: FakeSmtpServer
    account_id: str
    settings_path: Path
    dialogs: Dialogs

    def call(self, command: str, **values: object) -> Any:  # noqa: ANN401 - command-specific result
        return REGISTRY.run(self.engine, command, values)


def menu_named(window: MainWindow, *path: str) -> QMenu:
    """A menu of the window by its names, from the Menu button down: menu_named(window, 'File')."""
    action = window.menuBar().actions()[0]
    for name in path:
        menu = action.menu()
        assert isinstance(menu, QMenu)
        action = next(item for item in menu.actions() if item.text().replace('&', '') == name)
    menu = action.menu()
    assert isinstance(menu, QMenu)
    return menu


def row_of(window: MainWindow, position: int) -> Any:  # noqa: ANN401 - a loaded message row: a dataclass of the engine's list result
    return window.list_model.row_at(position)


def answer_input(monkeypatch: pytest.MonkeyPatch, answers: list[tuple[str, bool]]) -> None:
    """QInputDialog.getText returns these (text, accepted) pairs in turn (the last one repeats)."""
    remaining = list(answers)

    def get_text(*args: object, **kwargs: object) -> tuple[str, bool]:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(get_text))


def answer_save_file(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    def get_save_file_name(*args: object, **kwargs: object) -> tuple[str, str]:
        return str(path), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(get_save_file_name))


def answer_open_file(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    def get_open_file_name(*args: object, **kwargs: object) -> tuple[str, str]:
        return str(path), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(get_open_file_name))


def reject_dialog(monkeypatch: pytest.MonkeyPatch, dialog_class: type) -> None:
    """A modal dialog that is closed at once without an answer."""

    def exec_rejected(self: object) -> int:
        return int(QDialog.DialogCode.Rejected)

    monkeypatch.setattr(dialog_class, "exec", exec_rejected)


def record_opened_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []

    def open_url(url: QUrl) -> bool:
        opened.append(url.toString())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(open_url))
    return opened


def wait(qtbot: QtBot, condition: Callable[[], bool], timeout: int = 8000) -> None:
    qtbot.waitUntil(condition, timeout=timeout)


def settle(qtbot: QtBot, world: World, rows: int | None = None) -> None:
    """Wait until the window has loaded the folder (and, if given, shows this many rows)."""
    wait(qtbot, lambda: world.window.list_model.rowCount() > 0 if rows is None else world.window.list_model.rowCount() == rows)
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001


@pytest.fixture
def dialogs(monkeypatch: pytest.MonkeyPatch) -> Dialogs:
    recorder = Dialogs()

    def question(parent: object, title: str, text: str, *args: object, **kwargs: object) -> QMessageBox.StandardButton:
        recorder.shown.append(("question", title, text))
        return recorder.answer

    def warning(parent: object, title: str, text: str, *args: object, **kwargs: object) -> QMessageBox.StandardButton:
        recorder.shown.append(("warning", title, text))
        return QMessageBox.StandardButton.Ok

    def information(parent: object, title: str, text: str, *args: object, **kwargs: object) -> QMessageBox.StandardButton:
        recorder.shown.append(("information", title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(warning))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(information))
    return recorder


def build_world(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs, messages: dict[str, bytes] | None = None, fetch: bool = True, with_account: bool = True) -> Generator[World]:
    engine = make_engine(tmp_path)
    with FakePop3Server(messages if messages is not None else {f"uid{n}": mail(n) for n in (1, 2, 3)}) as pop, FakeSmtpServer() as smtp:
        account_id = ""
        if with_account:
            added = REGISTRY.run(engine, "account.add", dict(
                display_name="Me", email_address="me@example.com", username="user", password="secret",
                incoming_host="127.0.0.1", incoming_port=pop.port, incoming_security="none",
                outgoing_host="127.0.0.1", outgoing_port=smtp.port, outgoing_security="none",
            ))
            account_id = str(getattr(added, "account_id"))
            if fetch:
                REGISTRY.run(engine, "mail.fetch", {"account": account_id})
        backend = Backend(engine)
        settings_path = tmp_path / "ui.ini"
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        settings.setValue("check_on_start", False)  # tests decide for themselves when mail is checked
        window = MainWindow(backend, settings)
        qtbot.addWidget(window)
        window.show()
        yield World(engine, backend, window, pop, smtp, account_id, settings_path, dialogs)
        backend.wait_for_done(5000)
        window.close()


@pytest.fixture
def world(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs)


def account_node(window: MainWindow) -> QModelIndex:
    """The first account in the folder tree (the Favorites come before it)."""
    for row in range(window.tree_model.rowCount()):
        index = window.tree_model.index(row, 0)
        if index.data(KIND_ROLE) == "account":
            return index
    raise AssertionError("no account in the tree")
