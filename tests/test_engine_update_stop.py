import sys
import threading
from pathlib import Path

import pytest

from chrono_mail.core import ChronoMailError
from chrono_mail.engine import Engine
from chrono_mail.engine.commands_updates import UPDATE_DOWNLOAD, UPDATE_STOP_DOWNLOAD, DownloadParameters, StopParameters, UpdateDownloaded, interrupt_downloads
from tests.fakegithub import FakeGitHub
from tests.test_engine_updates import DEB, REGISTRY, github, installed  # noqa: F401 - github is a fixture

__all__ = ["github"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_update_stop.py 4.2.427"

# --- Version history ----------------------------------------------------
# v4.2.427: Initial: Cancel and quitting stop a download at once, also while the server sends nothing or keeps its answer back (its
#           connection is shut), also while the list of releases is read; a new download right after a Cancel is never touched by it

SIZE: int = 2 * 1024 * 1024


def _wait_until_reading(thread: threading.Thread) -> None:
    """Until the download's thread waits inside its read of the file (a fact read from its stack; the loop only guards against a hang)."""
    for _ in range(1000):
        frame = sys._current_frames().get(thread.ident or 0)  # noqa: SLF001 - the thread's stack, the one way to see where it waits
        names: set[str] = set()
        while frame is not None:
            names.add(frame.f_code.co_name)
            frame = frame.f_back
        if "read1" in names and "download" in names:
            return
        threading.Event().wait(0.01)
    raise AssertionError("the download never waited for the server")


def _stalled_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub, how: str = "stall",
) -> tuple[threading.Thread, list[UpdateDownloaded | ChronoMailError], Engine]:
    """A download whose server sends half the file and then nothing (stall), keeps the file's answer back (file) or the list's (list):
    running in a thread, waiting."""
    engine = installed(monkeypatch, tmp_path, github)
    name = f"chrono-mail_4.2.419{DEB}"
    github.publish("4.2.419", {name: b"x" * SIZE})
    if how == "stall":
        github.stall.add(name)
    else:
        github.hold.add("/releases" if how == "list" else f"/files/{name}")
    outcome: list[UpdateDownloaded | ChronoMailError] = []

    def run() -> None:
        try:
            outcome.append(REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads"), "t1")))
        except ChronoMailError as error:
            outcome.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    if how == "stall":
        _wait_until_reading(thread)  # the half is taken in and the download waits inside its read for the rest
    else:
        github.held.wait(10)  # the server's own fact: an answer is kept back
    assert "t1" in engine.download_interrupts  # connected; the server keeps the rest back
    return thread, outcome, engine


def test_cancel_stops_a_download_at_once_while_the_server_sends_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    thread, outcome, engine = _stalled_download(monkeypatch, tmp_path, github)
    REGISTRY.ask(engine, UPDATE_STOP_DOWNLOAD, StopParameters("t1"))
    thread.join(5)
    assert not thread.is_alive()  # never waits for the server's silence (30 s)
    assert len(outcome) == 1 and type(outcome[0]).__name__ == "DownloadStopped"
    assert list((tmp_path / "Downloads").iterdir()) == []  # what it saved is removed


def test_quitting_stops_a_download_at_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    thread, outcome, engine = _stalled_download(monkeypatch, tmp_path, github)
    engine.stop_event.set()
    interrupt_downloads(engine)
    thread.join(5)
    assert not thread.is_alive()
    assert len(outcome) == 1 and type(outcome[0]).__name__ == "DownloadStopped"
    assert list((tmp_path / "Downloads").iterdir()) == []


@pytest.mark.parametrize("how", ["list", "file"])
def test_cancel_stops_a_download_whose_server_keeps_its_answer_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub, how: str) -> None:
    thread, outcome, engine = _stalled_download(monkeypatch, tmp_path, github, how)
    REGISTRY.ask(engine, UPDATE_STOP_DOWNLOAD, StopParameters("t1"))
    thread.join(5)
    assert not thread.is_alive()
    assert len(outcome) == 1 and type(outcome[0]).__name__ == "DownloadStopped"
    assert not (tmp_path / "Downloads").exists() or list((tmp_path / "Downloads").iterdir()) == []


def test_a_new_download_right_after_a_cancel_is_never_touched_by_the_one_stopped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, github: FakeGitHub) -> None:
    thread, outcome, engine = _stalled_download(monkeypatch, tmp_path, github)
    name = f"chrono-mail_4.2.419{DEB}"
    github.stall.discard(name)  # the next one gets the whole file
    REGISTRY.ask(engine, UPDATE_STOP_DOWNLOAD, StopParameters("t1"))
    second = REGISTRY.ask(engine, UPDATE_DOWNLOAD, DownloadParameters("4.2.419", str(tmp_path / "Downloads"), "t2"))
    thread.join(5)
    assert not thread.is_alive() and type(outcome[0]).__name__ == "DownloadStopped"
    assert Path(second.path).read_bytes() == b"x" * SIZE and sorted(path.name for path in (tmp_path / "Downloads").iterdir()) == [name]
