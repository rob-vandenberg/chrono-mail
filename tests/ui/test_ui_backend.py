from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError, ConfigurationError
from chrono_mail.ui.backend import Backend
from tests.enginefactory import make_engine

# --- Version ------------------------------------------------------------
__version__ = "test_ui_backend.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial UI backend tests


@pytest.fixture
def backend(tmp_path: Path, qtbot: QtBot) -> Backend:
    return Backend(make_engine(tmp_path))


def test_a_command_runs_in_the_background_and_answers_on_the_gui_thread(backend: Backend, qtbot: QtBot) -> None:
    import threading

    answers: list[tuple[object, bool]] = []
    backend.call("account.list", {}, lambda result: answers.append((result, threading.current_thread() is threading.main_thread())))
    qtbot.waitUntil(lambda: bool(answers), timeout=5000)
    assert answers == [((), True)]


def test_errors_arrive_as_errors(backend: Backend, qtbot: QtBot) -> None:
    errors: list[ChronoMailError] = []
    backend.call("folder.list", {"account": "nobody@example.com"}, lambda result: None, errors.append)
    qtbot.waitUntil(lambda: bool(errors), timeout=5000)
    assert isinstance(errors[0], ConfigurationError) and str(errors[0]) == "Unknown account"


def test_usage_errors_and_unknown_commands_are_errors(backend: Backend, qtbot: QtBot) -> None:
    errors: list[ChronoMailError] = []
    backend.call("no.such.command", {}, lambda result: None, errors.append)
    backend.call("account.remove", {"bogus": 1}, lambda result: None, errors.append)
    qtbot.waitUntil(lambda: len(errors) == 2, timeout=5000)


def test_a_bug_in_a_command_becomes_an_error_message_not_a_dead_worker(backend: Backend, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    import chrono_mail.engine.commands_accounts as accounts

    def broken(engine: object, parameters: object) -> object:
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(accounts, "account_list", broken)
    broken_backend = Backend(backend.engine)  # builds a registry that uses the patched function
    errors: list[ChronoMailError] = []
    broken_backend.call("account.list", {}, lambda result: None, errors.append)
    qtbot.waitUntil(lambda: bool(errors), timeout=5000)
    assert "RuntimeError" in str(errors[0]) and "something unexpected" in str(errors[0])
    again: list[object] = []
    backend.call("account.list", {}, again.append)  # the pool still works
    qtbot.waitUntil(lambda: bool(again), timeout=5000)


def test_answers_for_a_deleted_owner_are_dropped(backend: Backend, qtbot: QtBot) -> None:
    owner = QObject()
    delivered: list[object] = []
    backend.call("account.list", {}, delivered.append, owner=owner)
    shiboken6.delete(owner)  # the C++ object is gone before the answer arrives
    other: list[object] = []
    backend.call("account.list", {}, other.append)
    qtbot.waitUntil(lambda: bool(other), timeout=5000)
    qtbot.waitUntil(lambda: backend._pending == {}, timeout=5000)  # noqa: SLF001
    assert delivered == []


def test_busy_signal_counts_running_commands(backend: Backend, qtbot: QtBot) -> None:
    counts: list[int] = []
    backend.busy_changed.connect(counts.append)
    done: list[object] = []
    backend.call("account.list", {}, done.append)
    qtbot.waitUntil(lambda: bool(done), timeout=5000)
    qtbot.waitUntil(lambda: counts[-1:] == [0], timeout=5000)
    assert counts[0] == 1 and counts[-1] == 0


def test_many_commands_at_once_all_answer(backend: Backend, qtbot: QtBot) -> None:
    answers: list[object] = []
    for _ in range(40):
        backend.call("account.list", {}, answers.append)
    qtbot.waitUntil(lambda: len(answers) == 40, timeout=8000)


def test_progress_reports_reach_the_gui(backend: Backend, qtbot: QtBot) -> None:
    seen: list[tuple[str, int, object]] = []

    def record(what: str, done: int, total: object) -> None:
        seen.append((what, done, total))

    backend.progress.connect(record)
    backend.engine.progress("Working", 3, 10)
    qtbot.waitUntil(lambda: bool(seen), timeout=3000)
    assert seen[0] == ("Working", 3, 10)


def test_run_now_is_synchronous(backend: Backend) -> None:
    assert backend.run_now("account.list", {}) == ()
