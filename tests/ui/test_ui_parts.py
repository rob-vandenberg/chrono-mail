import ast
import re
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_folders import FolderInfo
from chrono_mail.store import MessageNotFoundError
from chrono_mail.ui.currentview import CurrentView, EntryKind
from chrono_mail.ui.mailstate import MailState
from chrono_mail.ui.statusreport import MISSING_WARNING
from tests.ui.world import Dialogs, World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_parts.py 2.0.306"

# --- Version history ----------------------------------------------------
# v2.0.306: in_archive is gone: Archive or Unarchive follows the selected messages (MailState.all_archived, D-101)
# v1.9.273: The window's own calls (the key, the accounts) are typed (R-19)
# v1.9.264: Initial tests of the parts of the main window (R-17): each part keeps its own data and works through what the others offer;
#           the window stays small and never takes mail work back

UI = Path(__file__).resolve().parents[2] / "src" / "chrono_mail" / "ui"
PARTS = (
    "folderpane.py", "messagelistpane.py", "messagecolumns.py", "searchfilters.py", "messageactions.py", "mailtags.py", "readingpane.py",
    "composing.py", "mailchecking.py", "statusreport.py", "mailstate.py",
)


class Account:
    def __init__(self, account_id: str, kind: str = "pop3", storage: str = "own", shown_name: str = "", email_address: str = "", dav_url: str = "") -> None:
        self.account_id, self.kind, self.storage = account_id, kind, storage
        self.shown_name, self.email_address, self.dav_url, self.label = shown_name, email_address, dav_url, shown_name


# --- the window stays the window ------------------------------------------------------------------------------

def _code_lines(path: Path) -> int:
    """The lines of a module that are code: no version history, no blank lines, no comments."""
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#"))


def test_the_window_stays_small_and_runs_no_mail_command_of_its_own() -> None:
    """R-17: the window builds the screen and puts the parts together. Mail work lives in the parts: the window asks the engine only
    about the key and the accounts (and the calendars a new account has)."""
    source = (UI / "mainwindow.py").read_text(encoding="utf-8")
    assert _code_lines(UI / "mainwindow.py") < 1100  # 3,444 before R-17: mostly the screen, the actions and the menus now
    asked = set(re.findall(r"backend\.ask\(\s*([A-Z_]+)", source))  # every call is typed (R-19, tests/test_typed_calls.py)
    assert asked <= {"KEY_STATUS", "ACCOUNT_LIST", "DAV_FIND", "DAV_LINK"}, asked


def test_no_part_reaches_back_into_the_window() -> None:
    """A part knows only the parts it is given: none imports the window, and none reads the window's insides through its parent."""
    for name in PARTS:
        tree = ast.parse((UI / name).read_text(encoding="utf-8"))
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        assert "chrono_mail.ui.mainwindow" not in imported, name
        reached = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "_parent"]
        assert not [attr for attr in reached if attr.startswith("act_") or attr in ("folders", "messages", "reading", "checking", "composing", "mail_actions", "tags")], name


def test_the_parts_are_small() -> None:
    for name in PARTS:
        assert _code_lines(UI / name) < 650, name


# --- what the parts share ------------------------------------------------------------------------------------------

def test_the_shared_record_answers_the_questions_every_part_asks() -> None:
    state = MailState()
    state.set_accounts([
        Account("local", kind="local"), Account("a", storage="local", shown_name="Shared POP"), Account("b", kind="imap", email_address="b@example.org"),
        Account("c", kind="dav", dav_url="https://dav.example.org"),
    ])
    assert [str(getattr(account, "account_id")) for account in state.accounts] == ["local", "a", "b"]  # calendars only: never about mail
    assert state.mail_account_ids() == ["a", "b"]  # Local Folders is a place to keep mail, not an account
    assert state.accounts_with_mail() == ["local", "b"]  # an account in Local Folders counts once, through Local Folders
    assert (state.account_name("a"), state.account_name("b"), state.account_name("x")) == ("Shared POP", "b@example.org", "x")
    assert state.local_id() == "local" and state.is_local("local") and not state.is_local("b")
    assert state.calendar_accounts() == ["c"] and state.calendar_accounts({"b"}) == []
    state.folders = {"local": [FolderInfo("Inbox", "inbox", 4, 2), FolderInfo("Sent", "sent", 1, 0)], "b": [FolderInfo("INBOX", "inbox", 9, 3)]}
    assert state.total_unread() == 5 and state.folder_role_of("local", "Sent") == "sent" and state.folder_role_of("local", "Nope") == "other"


def test_the_shared_record_knows_the_kind_of_view() -> None:
    state = MailState()
    state.view = CurrentView(account="a", kind=EntryKind.FOLDER, folder="Snoozed")
    assert state.in_snoozed() and state.in_timed() and not state.in_scheduled_view()
    state.view = CurrentView(account="", kind=EntryKind.FAVORITE, combined="scheduled")
    assert state.in_scheduled_view() and state.in_drafts() and not state.in_timed()
    state.view = CurrentView(account="a", kind=EntryKind.FOLDER, folder="Archive", folder_role="archive")
    assert state.can_make_folder()
    state.view = CurrentView(account="", kind=EntryKind.FAVORITE, combined="inboxes")
    assert not state.can_make_folder()


# --- each part on a real window ------------------------------------------------------------------------------------

def test_the_status_line_warns_once_for_many_missing_messages(world: World, qtbot: QtBot, dialogs: Dialogs) -> None:
    report = world.window.report
    for _ in range(3):
        report.report("Could not delete", MessageNotFoundError("gone"))
    wait(qtbot, lambda: any(text == MISSING_WARNING for _, _, text in dialogs.shown))
    assert sum(1 for _, _, text in dialogs.shown if text == MISSING_WARNING) == 1
    report.finished("Done", keep_error=True)
    assert world.window.status_label.text() == MISSING_WARNING  # an error of the action stays
    report.finished("Done")
    assert world.window.status_label.text() == "Done"


def test_the_message_list_asks_again_only_when_what_it_shows_changed(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    asked: list[object] = []
    shown: list[None] = []
    real_show = window.list_model.show
    window.list_model.show = lambda spec: (asked.append(spec), real_show(spec))[1]  # type: ignore[method-assign]
    window.messages.view_changed.connect(lambda: shown.append(None))
    view = window.mail_state.view
    window.messages.change_view(view)  # the same view: the controls show it, the list is not asked again
    assert len(shown) == 1 and asked == []
    window.messages.change_view(replace(view, unread=True))
    assert len(shown) == 2 and len(asked) == 1


def test_the_folder_list_opens_an_entry_as_one_view(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    chosen: list[object] = []
    window.folders.entry_chosen.connect(chosen.append)
    sent = window.folders.find_item((world.account_id, "folder", "Sent"))
    assert sent is not None
    window.tree.setCurrentIndex(sent)
    assert len(chosen) == 1 and isinstance(chosen[0], CurrentView) and chosen[0].folder == "Sent"
    assert window.mail_state.view.folder == "Sent" and window.folders.title().startswith("Sent")


def test_writing_needs_an_account_with_a_server(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.mail_state.set_accounts([Account("local", kind="local")])
    window.composing.compose_to("someone@example.org")
    assert window.composing.composers == []


def test_no_check_and_no_send_while_an_import_runs(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    asked: list[str] = []
    real_call = window.backend.call

    def call(command: str, *args: object, **kwargs: object) -> int:
        asked.append(command)
        return real_call(command, *args, **kwargs)  # type: ignore[arg-type]

    window.backend.call = call  # type: ignore[method-assign]
    window.checking.import_running(True)
    assert not window.act_refresh.isEnabled()
    window.checking.check_mail()
    window.checking.send_outbox(manual=True)
    assert "mail.fetch" not in asked and "mail.send-outbox" not in asked
    window.checking.import_running(False)
    assert window.act_refresh.isEnabled()


def test_the_reading_pane_switched_off_shows_nothing(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    wait(qtbot, lambda: window.reading.shown is not None)
    window.reading.set_place("off")
    assert window.reading.shown is None and window.reading_pane.isHidden()
    window.reading.set_place("right")
    wait(qtbot, lambda: window.reading.shown is not None)  # the selected message comes back


def test_an_action_on_no_messages_asks_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    asked: list[object] = []
    monkeypatch.setattr(window.backend, "ask", lambda *args, **kwargs: asked.append(args))
    window.mail_actions.set_flags([], {"seen": True}, "a")
    window.mail_actions.move_many("Trash", [])
    window.mail_actions.delete_rows([])
    assert asked == []


def test_a_failed_wallet_check_still_loads_the_accounts(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    real_call = window.backend.call

    def call(command: str, values: object, on_success: object, on_error: object = None, *rest: object, **kwargs: object) -> int:
        if command == "key.status" and callable(on_error):
            on_error(ChronoMailError("the wallet is locked"))
            return 0
        return real_call(command, values, on_success, on_error, *rest, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(window.backend, "call", call)
    window.start()
    wait(qtbot, lambda: bool(window.mail_state.accounts))  # the accounts load all the same: the error is said, nothing stops


def test_the_tab_key_goes_as_before_the_split(world: World, qtbot: QtBot) -> None:
    """The parts are made in another order than the old window made its widgets; the Tab key still goes folders, messages, Sorted by,
    Filter, Mark, Tag, the search field (checked against 1.8.263)."""
    window = world.window
    window.show()
    qtbot.waitExposed(window)
    seen: list[str] = []
    widget = window.nextInFocusChain()
    while widget is not window and len(seen) < 500:
        if widget.focusPolicy() & Qt.FocusPolicy.TabFocus and widget.isVisibleTo(window):
            seen.append(type(widget).__name__ + ":" + (widget.objectName() or str(getattr(widget, "text", lambda: "")())))
        widget = widget.nextInFocusChain()
    assert seen[:4] == ["FolderTree:folderTree", "MessageListView:messageList", "QToolButton:sortButton", "QToolButton:filterButton"]
    assert seen[4:7] == ["QToolButton:Mark", "QToolButton:Tag", "QLineEdit:"]
