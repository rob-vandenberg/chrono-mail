from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError, FolderRole, MessageFlag
from chrono_mail.engine.commands_view import MESSAGE_ORDER, MESSAGE_ROWS, OrderParameters
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.ui.backend import Failure
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.messageorder import MessageOrder
from tests.ui.world import Dialogs, World, build_world, mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_next_unread.py 4.1.379"

# --- Version history ----------------------------------------------------
# v4.1.379: Initial (CR-15): Next and Previous unread find unread messages the list has not loaded yet, also in the folder a step opens and
#           while a large folder's order is still on its way, past folded groups; a press made while a step waits is done after it; rows that cannot be read
#           end a step; a folder opened by hand meanwhile gets nothing, a press made there is done there; never the folder shown again, from a pin, into subfolders and in a group; the order walked


@pytest.fixture
def empty(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs, messages={}, fetch=False)


def note(number: int) -> str:
    return f"Note {number:05d}"


def fill(world: World, folder: str, size: int, unread: set[int], role: FolderRole = FolderRole.JUNK) -> None:
    """size messages in this folder (a subfolder after "/"; made with this role), Note 00000 and on, all read except the numbers in unread."""
    path = tuple(folder.split("/"))
    with world.engine.quietly(), open_mailbox(world.engine, world.account_id) as mailbox:
        if folder != "Inbox":
            for depth in range(1, len(path) + 1):
                mailbox.ensure_folder(path[:depth], role if depth == len(path) else FolderRole.OTHER)
        for number in range(size):
            flags = frozenset() if number in unread else frozenset({MessageFlag.SEEN})
            mailbox.deliver(path, mail(number, subject=note(number), message_id=f"<{folder}{number}@x>"), flags)


def shown_a_to_z(qtbot: QtBot, world: World, rows: int) -> MainWindow:
    """The window started, the Inbox sorted by subject A to Z without group headings, only its first rows loaded, the first one selected."""
    window = world.window
    window.start()
    window.reading.mark_read_timer.stop()  # noqa: SLF001
    window.act_show_groups.setChecked(False)
    model = window.list_model
    wait(qtbot, lambda: model.spec is not None and model.settled and empty_queue(world))  # the Inbox is shown: now its sort can change
    window.messages.set_sort("subject", True)
    wait(qtbot, lambda: model.spec is not None and model.spec.sort == "subject" and model.settled and model.rowCount() == rows and empty_queue(world))
    fresh(qtbot, world)
    window.list_view.setFocus()
    window.list_view.setCurrentIndex(model.index(0, 0))
    return window


def fresh(qtbot: QtBot, world: World) -> None:
    """The view shown again, as when it is opened: a sort keeps the rows loaded before, this has only the first page of the new order."""
    model = world.window.list_model
    model.show(model.spec)
    wait(qtbot, lambda: model.settled and model.complete and model.row_at(0) is not None and empty_queue(world))


def selected_subject(window: MainWindow) -> str | None:
    current = window.list_view.currentIndex()
    row = window.list_model.row_at(current.row()) if current.isValid() else None
    return None if row is None else row.subject


def test_next_unread_finds_an_unread_message_the_list_has_not_loaded(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 250, {200})
    window = shown_a_to_z(qtbot, empty, 250)
    assert window.list_model.row_at(200) is None  # not come into view: not loaded
    assert window.messages.go_to_unread(1)
    wait(qtbot, lambda: selected_subject(window) == note(200))  # its rows came first, then it is selected
    assert window.mail_state.view.folder == "Inbox" and window.list_view.currentIndex().row() == 200


def test_presses_made_while_a_step_waits_are_done_after_it_and_previous_unread_goes_up(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 400, {150, 330})
    window = shown_a_to_z(qtbot, empty, 400)
    assert window.messages.go_to_unread(1) and window.messages.go_to_unread(1)  # the second press while the first waits for its rows
    wait(qtbot, lambda: selected_subject(window) == note(330))
    assert window.messages.go_to_unread(-1)
    wait(qtbot, lambda: selected_subject(window) == note(150))


def show_folder_a_to_z(qtbot: QtBot, world: World, folder: str, rows: int) -> None:
    """Open this folder (each folder keeps its own sort, D-228), sorted by subject A to Z, its first message selected."""
    window = world.window
    found = window.folders.find_item((world.account_id, "folder", folder))  # noqa: SLF001
    assert found is not None
    window.tree.setCurrentIndex(found)
    model = window.list_model
    wait(qtbot, lambda: window.mail_state.view.folder == folder and model.spec is not None and model.spec.folder == folder and model.settled and empty_queue(world))
    window.messages.set_sort("subject", True)
    wait(qtbot, lambda: window.mail_state.view.folder == folder and model.spec is not None and model.spec.sort == "subject" and model.settled and model.rowCount() == rows)
    fresh(qtbot, world)
    window.list_view.setFocus()
    window.list_view.setCurrentIndex(model.index(0, 0))


def empty_queue(world: World) -> bool:
    return world.backend._pending == {}  # noqa: SLF001


def test_a_folder_a_step_opens_gets_its_first_unread_message_also_when_it_is_not_loaded(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 3, set())  # nothing unread in the Inbox
    fill(empty, "Junk", 250, {180})
    window = shown_a_to_z(qtbot, empty, 3)
    show_folder_a_to_z(qtbot, empty, "Junk", 250)
    assert window.list_model.row_at(180) is None
    show_folder_a_to_z(qtbot, empty, "Inbox", 3)
    assert window.messages.go_to_unread(1)  # nothing unread below: on to Junk, which is sorted A to Z
    wait(qtbot, lambda: window.mail_state.view.folder == "Junk" and selected_subject(window) == note(180))


def test_previous_unread_in_a_folder_a_step_opens_takes_its_last_unread_message(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 250, {20, 230})
    fill(empty, "Junk", 3, set())  # below the Inbox, nothing unread
    window = shown_a_to_z(qtbot, empty, 250)
    assert window.list_model.row_at(230) is None
    show_folder_a_to_z(qtbot, empty, "Junk", 3)
    assert window.messages.go_to_unread(-1)  # nothing unread above: up to the Inbox, its last unread message
    wait(qtbot, lambda: window.mail_state.view.folder == "Inbox" and selected_subject(window) == note(230))


def hold_the_rest_of_the_order(world: World, monkeypatch: pytest.MonkeyPatch) -> list[Callable[[], None]]:
    """The first question for a view's whole order is held back until the test lets it through: the list knows only its first 100."""
    held: list[Callable[[], None]] = []
    real_ask = world.backend.ask

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_ORDER and isinstance(parameters, OrderParameters) and parameters.whole and parameters.sort == "subject" and not held:

            def later() -> None:
                real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

            held.append(later)
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(world.backend, "ask", ask)
    return held


def test_next_unread_waits_for_the_rest_of_a_large_folder_s_order(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(empty, "Inbox", 250, {200})
    window = shown_a_to_z(qtbot, empty, 250)
    model = window.list_model
    held = hold_the_rest_of_the_order(empty, monkeypatch)
    model.show(model.spec)  # shown again: the start of its order first, the question for the rest is held back
    wait(qtbot, lambda: bool(held) and model.row_at(0) is not None)
    assert not model.complete  # only the first 100 places are known
    window.list_view.setCurrentIndex(model.index(0, 0))
    assert window.messages.go_to_unread(1)  # it waits: never on to another folder while the rest may hold an unread message
    assert window.mail_state.view.folder == "Inbox" and selected_subject(window) == note(0)
    held[0]()  # the rest of the order arrives
    wait(qtbot, lambda: selected_subject(window) == note(200))


def test_a_held_key_s_repeats_are_forgotten_while_a_step_waits(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 400, {150, 330})
    window = shown_a_to_z(qtbot, empty, 400)
    messages = window.messages
    assert messages.go_to_unread(1)  # it waits for the rows of Note 00150
    messages._repeats.repeat = True  # noqa: SLF001 - the key is held: the next press is its automatic repeat
    assert messages.go_to_unread(1)
    messages._repeats.repeat = False  # noqa: SLF001
    wait(qtbot, lambda: selected_subject(window) == note(150) and empty_queue(empty))  # a repeat kept for later would have gone on at once
    assert selected_subject(window) == note(150) and not messages._unread_presses  # noqa: SLF001


def test_the_order_is_walked_down_and_up_across_its_blocks() -> None:
    keys = [f"k{number}" for number in range(5000)]
    order = MessageOrder(keys, ["Inbox"] * len(keys))
    order.insert(10, "extra", "Inbox")  # blocks of different sizes
    every = list(order)
    assert list(order.walk(0, 1)) == list(enumerate(every))
    assert list(order.walk(len(every) - 1, -1)) == list(reversed(list(enumerate(every))))
    assert list(order.walk(2500, 1))[0] == (2500, every[2500]) and list(order.walk(2500, -1))[-1] == (0, every[0])
    assert list(order.walk(len(every), 1)) == [] and list(order.walk(-1, -1)) == []


def test_next_unread_passes_messages_in_a_folded_group(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 250, {200})
    window = shown_a_to_z(qtbot, empty, 250)
    model = window.list_model
    window.act_show_groups.setChecked(True)
    wait(qtbot, lambda: model.header_rows() != [] and model.settled and empty_queue(empty))
    for row in reversed(model.header_rows()):
        model.toggle_group(row)  # every group folded: no message is shown
    window.list_view.setCurrentIndex(model.index(0, 0))
    assert window.messages.go_to_unread(1) is False  # nothing unread is shown here, nor in another folder
    assert window.mail_state.view.folder == "Inbox" and not window.messages._unread_presses  # noqa: SLF001


def test_a_step_whose_rows_cannot_be_read_ends_there(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(empty, "Inbox", 400, {150, 160, 330})
    window = shown_a_to_z(qtbot, empty, 400)
    real_ask = empty.backend.ask
    refused: list[int] = []

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_ROWS and on_error is not None:
            refused.append(1)
            on_error(ChronoMailError("The index could not be read"))
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(empty.backend, "ask", ask)
    assert window.messages.go_to_unread(1)
    assert refused == [1] and selected_subject(window) == note(0)  # one question, no later message chosen without a word
    monkeypatch.setattr(empty.backend, "ask", real_ask)
    assert window.messages.go_to_unread(1)  # the next press works again
    wait(qtbot, lambda: selected_subject(window) == note(150))


def test_a_folder_opened_by_hand_before_the_one_a_step_opened_came_gets_nothing_selected(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(empty, "Inbox", 3, set())
    fill(empty, "Junk", 5, {2})
    fill(empty, "Projects", 5, {1}, FolderRole.OTHER)
    window = shown_a_to_z(qtbot, empty, 3)
    real_ask = empty.backend.ask
    held: list[int] = []

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_ORDER and isinstance(parameters, OrderParameters) and parameters.folder == "Junk":
            held.append(1)  # Junk's answer never comes
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(empty.backend, "ask", ask)
    assert window.messages.go_to_unread(1)  # nothing unread below: Junk opens
    assert window.mail_state.view.folder == "Junk" and held
    projects = window.folders.find_item((empty.account_id, "folder", "Projects"))  # noqa: SLF001
    assert projects is not None
    window.tree.setCurrentIndex(projects)  # opened by hand before Junk came
    model = window.list_model
    wait(qtbot, lambda: model.spec is not None and model.spec.folder == "Projects" and model.settled and model.rowCount() == 5 and empty_queue(empty))
    qtbot.waitUntil(lambda: not window.messages._unread_busy)  # noqa: SLF001
    assert not window.list_view.currentIndex().isValid()  # no unread message selected unasked


def test_a_press_made_in_a_folder_opened_by_hand_is_done_there(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(empty, "Inbox", 3, set())
    fill(empty, "Junk", 5, {2})
    fill(empty, "Projects", 5, {1}, FolderRole.OTHER)
    window = shown_a_to_z(qtbot, empty, 3)
    real_ask = empty.backend.ask
    held: list[int] = []

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_ORDER and isinstance(parameters, OrderParameters) and parameters.folder == "Junk":
            held.append(1)  # Junk's answer never comes
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(empty.backend, "ask", ask)
    assert window.messages.go_to_unread(1)  # nothing unread below: Junk opens
    assert window.mail_state.view.folder == "Junk" and held
    projects = window.folders.find_item((empty.account_id, "folder", "Projects"))  # noqa: SLF001
    assert projects is not None
    window.tree.setCurrentIndex(projects)  # opened by hand before Junk came
    assert window.messages.go_to_unread(1)  # pressed while Projects loads: done once it came
    model = window.list_model
    wait(qtbot, lambda: model.spec is not None and model.spec.folder == "Projects" and model.settled and model.rowCount() == 5 and empty_queue(empty))
    wait(qtbot, lambda: selected_subject(window) == note(1))


def test_next_unread_works_on_an_empty_view_opened_before_the_folder_a_step_opened_came(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(empty, "Inbox", 3, set())
    fill(empty, "Junk", 5, {2})
    fill(empty, "Projects", 5, {1}, FolderRole.OTHER)
    window = shown_a_to_z(qtbot, empty, 3)
    real_ask = empty.backend.ask
    held: list[int] = []

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_ORDER and isinstance(parameters, OrderParameters) and parameters.folder == "Junk":
            held.append(1)  # Junk's answer never comes
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(empty.backend, "ask", ask)
    assert window.messages.go_to_unread(1)  # nothing unread below: Junk opens
    assert window.mail_state.view.folder == "Junk" and held
    window.messages.show_empty()  # an account without an Inbox, clicked before Junk came
    model = window.list_model
    assert model.settled and model.spec is None
    assert window.messages.go_to_unread(1)  # done at once, never kept waiting for Junk: on from Junk, still chosen in the folder list, to Projects
    wait(qtbot, lambda: window.mail_state.view.folder == "Projects" and selected_subject(window) == note(1))



def test_a_step_from_an_inbox_shown_through_its_account_row_never_opens_that_inbox_again(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 5, {0})
    window = shown_a_to_z(qtbot, empty, 5)
    account = window.folders.find_item((empty.account_id, "account", ""))  # noqa: SLF001
    assert account is not None
    window.tree.setCurrentIndex(account)  # the account's row: its Inbox is shown
    model = window.list_model
    wait(qtbot, lambda: window.mail_state.view.folder == "Inbox" and model.settled and model.row_at(4) is not None and empty_queue(empty))
    window.list_view.setCurrentIndex(model.index(4, 0))
    assert window.messages.go_to_unread(1) is False  # nothing unread below, and no other folder with unread mail
    assert window.messages.go_to_unread(-1)  # Next and Previous unread keep working
    wait(qtbot, lambda: selected_subject(window) == note(0))


def last_selected(qtbot: QtBot, world: World, rows: int) -> None:
    """The folder shown has these rows, all loaded: its last one is selected."""
    model = world.window.list_model
    wait(qtbot, lambda: model.settled and model.rowCount() == rows and model.row_at(rows - 1) is not None and empty_queue(world))
    world.window.list_view.setCurrentIndex(model.index(rows - 1, 0))


def test_next_unread_goes_on_into_a_subfolder(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 3, set())
    fill(empty, "Projects", 3, set(), FolderRole.OTHER)
    fill(empty, "Projects/Lists", 3, {1}, FolderRole.OTHER)
    window = shown_a_to_z(qtbot, empty, 3)
    show_folder_a_to_z(qtbot, empty, "Projects", 3)
    last_selected(qtbot, empty, 3)
    assert window.messages.go_to_unread(1)  # nothing unread below in Projects: on to its subfolder
    wait(qtbot, lambda: window.mail_state.view.folder == "Projects/Lists" and selected_subject(window) == note(1))


def test_next_unread_goes_on_to_another_folder_in_an_account_inside_a_group(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 3, set())
    fill(empty, "Junk", 5, {2})
    window = shown_a_to_z(qtbot, empty, 3)
    empty.call("group.add", name="Work", account=(empty.account_id,))
    window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: any(window.tree_model.index(row, 0).data() == "Work" for row in range(window.tree_model.rowCount())))  # the account under it
    show_folder_a_to_z(qtbot, empty, "Inbox", 3)
    last_selected(qtbot, empty, 3)
    assert window.messages.go_to_unread(1)  # the account sits under its group: on to its Junk
    wait(qtbot, lambda: window.mail_state.view.folder == "Junk" and selected_subject(window) == note(2))


def test_next_unread_goes_on_from_a_folder_opened_through_its_pin(empty: World, qtbot: QtBot) -> None:
    fill(empty, "Inbox", 3, {0})
    fill(empty, "Projects", 3, set(), FolderRole.OTHER)
    fill(empty, "Zeta", 3, {1}, FolderRole.OTHER)
    window = shown_a_to_z(qtbot, empty, 3)
    empty.call("favorite.pin", account=empty.account_id, folder="Projects")
    window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: window.folders.find_item((empty.account_id, "pinlink", "Projects")) is not None)  # noqa: SLF001
    pin = window.folders.find_item((empty.account_id, "pinlink", "Projects"))  # noqa: SLF001
    assert pin is not None
    window.tree.setCurrentIndex(pin)
    wait(qtbot, lambda: window.mail_state.view.folder == "Projects")
    last_selected(qtbot, empty, 3)
    assert window.messages.go_to_unread(1)  # on from Projects to Zeta, never back to the Inbox above it
    wait(qtbot, lambda: window.mail_state.view.folder == "Zeta" and selected_subject(window) == note(1))

