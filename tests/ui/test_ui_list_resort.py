from collections.abc import Callable

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import MESSAGE_PLACES
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.ui.backend import Failure
from chrono_mail.ui.messagelist import MessageListModel
from tests.ui.test_ui_schedule import in_hours, real_clock
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_list_resort.py 4.2.432"

# --- Version history ----------------------------------------------------
# v4.2.432: Initial: a message that changed where it is moves to its new place in the list's sort, and stays selected (D-146)


def _ids(model: MessageListModel) -> list[str]:
    """The messages of the list in their order, the headings left out."""
    found: list[str] = []
    for row in range(model.rowCount()):
        shown = None if model.is_header(row) else model.row_at(row)
        if shown is not None:
            found.append(shown.message_id)
    return found


def _selected(world: World) -> list[str]:
    model = world.window.list_model
    return [shown.message_id for index in world.window.list_view.selectionModel().selectedRows() if (shown := model.row_at(index.row())) is not None]


SUBJECTS: dict[str, str] = {}  # message id -> subject, for sorting by Subject first


def _scheduled(world: World, qtbot: QtBot, times: tuple[tuple[str, float], ...]) -> list[str]:
    """Repeating messages sent in these hours, shown in the Scheduled folder sorted by Send at, the soonest first: their ids in that order."""
    started(qtbot, world)
    real_clock(world)
    made: list[str] = []
    for subject, hours in times:
        made.append(world.call("mail.send", account=world.account_id, to="you@example.com", subject=subject, body="x", send_at=in_hours(hours), repeat_days=7).message_id)
        SUBJECTS[made[-1]] = subject
    wait(qtbot, lambda: world.window.tree_model.find_item(world.account_id, "folder", "Scheduled") is not None)
    item = world.window.tree_model.find_item(world.account_id, "folder", "Scheduled")
    assert item is not None
    world.window.tree.setCurrentIndex(item.index())
    wait(qtbot, lambda: len(_ids(world.window.list_model)) == len(made))
    _by_send_at(world, qtbot, made)
    return made


def _scheduled_folder_by_send_at(world: World, qtbot: QtBot) -> tuple[str, str, str]:
    """Three repeating messages sent in 2, 4 and 6 hours, shown in the Scheduled folder sorted by Send at, the soonest first."""
    made = _scheduled(world, qtbot, (("Two", 2), ("Four", 4), ("Six", 6)))
    return made[0], made[1], made[2]


def _by_send_at(world: World, qtbot: QtBot, made: list[str]) -> None:
    """Sorted by Send at, the soonest first: by Subject first, so the list is known to hold the Send at order's answer."""
    world.window.messages.set_sort("subject", True)
    wait(qtbot, lambda: _ids(world.window.list_model) == sorted(made, key=SUBJECTS.__getitem__))
    world.window.messages.set_sort("snoozed", True)
    wait(qtbot, lambda: _ids(world.window.list_model) == made)


def test_a_scheduled_message_whose_send_time_is_edited_moves_to_its_place_and_stays_selected(world: World, qtbot: QtBot) -> None:
    two, four, six = _scheduled_folder_by_send_at(world, qtbot)
    model = world.window.list_model
    world.window.list_view.setCurrentIndex(model.index(model.find(six), 0))
    assert _selected(world) == [six]
    world.call("mail.repeat-change", account=world.account_id, folder="Scheduled", message=six, start=in_hours(1))
    wait(qtbot, lambda: _ids(model) == [six, two, four])  # at its new place, without opening the folder again
    assert _selected(world) == [six] and world.window.list_view.currentIndex().row() >= 0


def test_a_message_moved_out_of_its_heading_lands_under_a_heading_of_its_new_time(world: World, qtbot: QtBot) -> None:
    two, four, six = _scheduled_folder_by_send_at(world, qtbot)
    model = world.window.list_model
    world.window.act_show_groups.setChecked(True)  # View > Show groups
    wait(qtbot, lambda: model.is_header(0) and _ids(model) == [two, four, six])
    world.call("mail.repeat-change", account=world.account_id, folder="Scheduled", message=two, start=in_hours(24 * 30))
    wait(qtbot, lambda: _ids(model) == [four, six, two])
    groups = world.call("message.groups", account=world.account_id, folder="Scheduled", sort="snoozed", oldest_first=True)
    headings = [row for row in range(model.rowCount()) if model.is_header(row)]
    assert len(headings) == len(groups) and groups[-1].count == 1 and headings[-1] == model.rowCount() - 2  # as the engine heads them: one of its own, above it


def test_in_favorites_scheduled_an_edited_send_time_moves_the_message_too(world: World, qtbot: QtBot) -> None:
    two, four, six = _scheduled_folder_by_send_at(world, qtbot)
    model = world.window.list_model
    item = world.window.tree_model.find_item("", "favorite", "scheduled")
    assert item is not None
    world.window.tree.setCurrentIndex(item.index())
    wait(qtbot, lambda: model.spec is not None and model.spec.combined == "scheduled" and len(_ids(model)) == 3)
    _by_send_at(world, qtbot, [two, four, six])
    world.window.list_view.setCurrentIndex(model.index(model.find(four), 0))
    world.call("mail.repeat-change", account=world.account_id, folder="Scheduled", message=four, start=in_hours(8))
    wait(qtbot, lambda: _ids(model) == [two, six, four])
    assert _selected(world) == [four]


def test_two_messages_that_now_come_before_the_same_one_stand_in_their_order(world: World, qtbot: QtBot) -> None:
    two, four, six = _scheduled_folder_by_send_at(world, qtbot)
    model = world.window.list_model
    world.call("mail.repeat-change", account=world.account_id, folder="Scheduled", message=four, start=in_hours(1.5))
    world.call("mail.repeat-change", account=world.account_id, folder="Scheduled", message=six, start=in_hours(1))
    wait(qtbot, lambda: _ids(model) == [six, four, two])
    qtbot.wait(100)
    assert _ids(model) == [six, four, two]


def _hold_the_first_places_question(world: World, monkeypatch: pytest.MonkeyPatch) -> list[Callable[[], None]]:
    """The list's first places question is held back until the test lets it through."""
    held: list[Callable[[], None]] = []
    real_ask = world.backend.ask

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_PLACES and not held:

            def later() -> None:
                real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

            held.append(later)
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(world.backend, "ask", ask)
    return held


def _heading_sizes(model: MessageListModel) -> list[int]:
    """How many messages stand under each heading of the list, in its order."""
    sizes: list[int] = []
    for row in range(model.rowCount()):
        if model.is_header(row):
            sizes.append(0)
        elif sizes:
            sizes[-1] += 1
    return sizes


def test_a_place_given_by_a_message_still_moving_is_asked_again(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second review: One changed and its question was on its way when Four changed too; the answer placed One before Four at Four's
    new place, while the list still held Four at its old one, and only Four moved after: One stayed wrong for good."""
    one, two, three, four = _scheduled(world, qtbot, (("One", 1), ("Two", 2), ("Three", 3), ("Four", 4)))
    model = world.window.list_model
    held = _hold_the_first_places_question(world, monkeypatch)
    world.call("mail.repeat-change", account=world.account_id, folder="Scheduled", message=one, start=in_hours(2.5))
    wait(qtbot, lambda: bool(held))
    world.call("mail.repeat-change", account=world.account_id, folder="Scheduled", message=four, start=in_hours(2.75))
    wait(qtbot, lambda: model._still_moving(four))  # noqa: SLF001 - its fact came while One's question is held
    held[0]()  # answered now: by the engine's order, One comes before Four
    wait(qtbot, lambda: _ids(model) == [two, one, four, three])


def test_the_headings_stay_right_when_a_message_is_placed_by_one_still_moving(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The third review: placed for now before a message whose own move was not done, a message was counted under the heading of its
    true place while it stood under another; the counts drifted, a message showed under the wrong heading and a heading went."""
    first, second, moving, follower, last = _scheduled(world, qtbot, (("A", 1), ("B", 30), ("C", 31), ("D", 24 * 10), ("E", 24 * 11)))
    model = world.window.list_model
    world.window.act_show_groups.setChecked(True)  # View > Show groups
    wait(qtbot, lambda: model.is_header(0) and _ids(model) == [first, second, moving, follower, last])
    held = _hold_the_first_places_question(world, monkeypatch)
    world.call("mail.repeat-change", account=world.account_id, folder="Scheduled", message=moving, start=in_hours(2))
    wait(qtbot, lambda: bool(held))
    world.call("mail.repeat-change", account=world.account_id, folder="Scheduled", message=follower, start=in_hours(3))
    wait(qtbot, lambda: model._still_moving(follower))  # noqa: SLF001
    held[0]()
    wait(qtbot, lambda: _ids(model) == [first, moving, follower, second, last])
    groups = world.call("message.groups", account=world.account_id, folder="Scheduled", sort="snoozed", oldest_first=True)
    wait(qtbot, lambda: _heading_sizes(model) == [group.count for group in groups])
