import time

import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.engine.mailbox import Mailbox, MessageRef
from chrono_mail.ui import messageactions
from chrono_mail.ui.statusreport import StatusReport
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_progress.py 3.6.364"

# --- Version history ----------------------------------------------------
# v3.6.364: the recorded status line takes "running" (a line about work still running)
# v3.6.361: Initial: an action on many messages that takes longer than a moment says "Preparing to …" until the first message is done,
#           then how many of how many, then its last line; a quick one shows only its last line (D-266)


def shown_texts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    texts: list[str] = []
    show = StatusReport.show

    def recorded(self: StatusReport, text: str, error: bool = False, running: bool = False) -> None:
        texts.append(text)
        show(self, text, error, running)

    monkeypatch.setattr(StatusReport, "show", recorded)
    return texts


def test_a_slow_action_says_what_it_prepares_then_counts(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The server's work (here: slowed) shows "Preparing to delete 3 messages…"; then each message done counts up; then the last line."""
    monkeypatch.setattr(messageactions, "PROGRESS_AFTER_MS", 100, raising=False)
    prepare, delete = Mailbox.prepare_deletes, Mailbox.delete

    def slow_prepare(self: Mailbox, refs: list[MessageRef], permanent: bool) -> None:
        time.sleep(0.6)
        prepare(self, refs, permanent)

    def slow_delete(self: Mailbox, ref: MessageRef, permanent: bool = False) -> object:
        time.sleep(0.3)
        return delete(self, ref, permanent)

    monkeypatch.setattr(Mailbox, "prepare_deletes", slow_prepare)
    monkeypatch.setattr(Mailbox, "delete", slow_delete)
    world.window.start()
    settle(qtbot, world, 3)
    texts = shown_texts(monkeypatch)
    world.window.list_view.selectAll()
    world.window.mail_actions.delete_selected()
    wait(qtbot, lambda: bool(texts) and texts[-1] == "Moved 3 messages to Trash")
    preparing = texts.index("Preparing to delete 3 messages…")
    counts = [text for text in texts[preparing:] if text.startswith("Deleting: ")]
    assert counts and counts == sorted(counts) and counts[-1] == "Deleting: 3 of 3"
    assert texts.index(counts[0]) > preparing


def test_a_quick_action_shows_only_its_last_line(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Done before the moment passes: the last line stays, nothing about preparing comes after it."""
    world.window.start()
    settle(qtbot, world, 3)
    texts = shown_texts(monkeypatch)
    world.window.list_view.selectAll()
    world.window.mail_actions.delete_selected()
    wait(qtbot, lambda: "Moved 3 messages to Trash" in texts)
    qtbot.wait(messageactions.PROGRESS_AFTER_MS + 200)
    assert texts[-1] == "Moved 3 messages to Trash"
