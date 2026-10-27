import dataclasses
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QObject, QPersistentModelIndex
from pytestqt.qtbot import QtBot

from chrono_mail.engine.changes import Change
from chrono_mail.engine.registry import TypedCommand
from chrono_mail.engine.commands_mail import ListParameters, message_list
from chrono_mail.engine.commands_view import MESSAGE_ORDER, OrderParameters
from chrono_mail.ui.backend import Failure
from chrono_mail.ui.mailstate import MailState, Placed
from tests.ui.test_ui_rapid_actions import fill
from tests.ui.world import Dialogs, World, build_world, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_large_selection.py 4.1.380"

# --- Version history ----------------------------------------------------
# v4.1.380: the key press test counts the selected messages the Archive check reads, not how often it runs: since 3.6.362 it runs again
#           inside the action, on the selection left (D-266)
# v3.6.350: a drag made before the list knew every selected message moves them all; a selected range keeps its places when the rest of
#           the order comes (RV-206); an Undo pressed after a waiting Shift+Delete or unschedule acts as it would at once (B-18); the
#           Delete icon goes after a held Undo and never deletes another mailbox's message or acts in another view; an empty Mark or Tag takes no number; Redo
#           while busy
# v3.5.346: a drag takes every selected message, also those not loaded, and its badge counts them (RV-77)
# v3.0.339: the Delete test waits for the list to empty (the rows leave in bursts, D-218) instead of reading it at one moment
# v2.4.334: the messages that leave in one moment go in a few removals, not one each
# v2.4.334: an action right after a large folder opened waits for the rest of the order and takes every selected message (W12b)
# v2.4.329: Initial (the Windows computer's W12 and W13): an action on a selection larger than what came into view takes every selected
#           message; Toggle read counts them all; the key press does its work once, not once per selected message


@pytest.fixture
def empty(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs, messages={}, fetch=False)


def opened(qtbot: QtBot, world: World, size: int) -> None:
    """size unread messages in the Inbox, shown, only the first page of rows loaded, all of them selected (Ctrl+A)."""
    fill(world, size)
    window = world.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == size and world.backend._pending == {})  # noqa: SLF001
    assert model.row_at(size - 1) is None  # not come into view: not loaded
    window.list_view.setFocus()
    window.list_view.selectAll()


def held_in(world: World, folder: str) -> int:
    return message_list(world.engine, ListParameters(world.account_id, folder)).total


def test_delete_takes_every_selected_message_also_those_not_loaded(empty: World, qtbot: QtBot) -> None:
    """W12: Ctrl+A on 1,142 messages and Delete moved 100 to Trash, the rows loaded so far."""
    opened(qtbot, empty, 250)
    assert len(empty.window.messages.selected_rows()) == 250
    empty.window.mail_actions.delete_selected()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {} and empty.window.list_model.rowCount() == 0)  # noqa: SLF001 - the rows leave in bursts (D-218): waited for, not assumed
    assert held_in(empty, "Inbox") == 0


def hold_the_rest_of_the_order(empty: World, monkeypatch: pytest.MonkeyPatch) -> list[Callable[[], None]]:
    """The first question for a view's whole order is held back until the test lets it through: the list knows only its first 100."""
    held: list[Callable[[], None]] = []
    real_ask = empty.backend.ask

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_ORDER and isinstance(parameters, OrderParameters) and parameters.whole and not held:

            def later() -> None:
                real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

            held.append(later)
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(empty.backend, "ask", ask)
    return held


def test_a_drag_before_the_list_knows_every_selected_message_moves_them_all(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-206: Ctrl+A right after a large folder opened and a drag to Junk moved the 100 messages the list knew, without a word; a click
    after the drop must not change what moves."""
    from chrono_mail.ui import messagelistview
    from chrono_mail.ui.dragdata import read_messages_mime_data, read_messages_rest

    counts: list[int] = []
    real_badge = messagelistview.drag_badge

    def badge(count: int) -> object:
        counts.append(count)
        return real_badge(count)

    monkeypatch.setattr(messagelistview, "drag_badge", badge)
    held = hold_the_rest_of_the_order(empty, monkeypatch)
    fill(empty, 250)
    window = empty.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == 250 and bool(held))
    assert not model.complete
    window.list_view.setFocus()
    window.list_view.selectAll()
    drag = window.list_view.make_drag()
    assert drag is not None and counts == [250]  # the badge counts every selected message
    dragged = read_messages_mime_data(drag.mimeData())
    rest = read_messages_rest(drag.mimeData())
    assert dragged is not None and len(dragged[1]) == 100 and rest is not None
    window.mail_actions.messages_dropped(dragged[0], "Junk", dragged[1], rest)
    assert held_in(empty, "Junk") == 0  # it waits for the rest of the order
    window.list_view.clearSelection()  # a click after the drop changes nothing
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Junk") == 250 and empty.backend._pending == {})  # noqa: SLF001
    assert held_in(empty, "Inbox") == 0


def test_a_drag_during_which_the_list_learns_every_message_moves_them_all(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: the rest of the order came in while the pointer was held (a drag runs its own event loop); the drop then
    moved only the messages named when the drag started."""
    from chrono_mail.ui.dragdata import read_messages_mime_data, read_messages_rest

    held = hold_the_rest_of_the_order(empty, monkeypatch)
    fill(empty, 250)
    window = empty.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == 250 and bool(held))
    window.list_view.setFocus()
    window.list_view.selectAll()
    drag = window.list_view.make_drag()
    assert drag is not None
    held[0]()  # during the drag
    wait(qtbot, lambda: model.complete)
    dragged, rest = read_messages_mime_data(drag.mimeData()), read_messages_rest(drag.mimeData())
    assert dragged is not None and rest is not None
    window.mail_actions.messages_dropped(dragged[0], "Junk", dragged[1], rest)
    wait(qtbot, lambda: held_in(empty, "Junk") == 250 and empty.backend._pending == {})  # noqa: SLF001


def test_mail_that_comes_in_meanwhile_stops_an_action_on_places_not_known_yet(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: new mail that came in while the rest of the order was on its way moved the places not known yet, so a waiting
    Delete took a message nobody selected. Which messages those places held can no longer be told: nothing is done, and that is said."""
    from PySide6.QtCore import QItemSelection, QItemSelectionModel
    from chrono_mail.engine.mailbox import open_mailbox
    from tests.ui.test_ui_rapid_actions import mail

    held = hold_the_rest_of_the_order(empty, monkeypatch)
    fill(empty, 250)
    window = empty.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == 250 and bool(held))
    window.list_view.setFocus()
    selection = QItemSelection(model.index(50, 0), model.index(199, model.columnCount() - 1))
    window.list_view.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    window.mail_actions.delete_selected()  # waits for the rest of the order
    with open_mailbox(empty.engine, empty.account_id) as mailbox:  # new mail meanwhile, told to the list
        mailbox.deliver(("Inbox",), mail(9999, subject="Note 00120x", message_id="<just-in@x>"), frozenset())
    qtbot.wait(100)
    held[0]()
    wait(qtbot, lambda: "Nothing was done" in window.status_label.text() and empty.backend._pending == {})  # noqa: SLF001
    qtbot.wait(200)
    assert held_in(empty, "Trash") == 0


def test_a_drop_whose_places_were_given_up_during_the_drag_moves_nothing_and_says_so(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: the list gave up the places not known yet while the pointer was held; the drop then moved only the known
    messages, without a word (D-260: nothing moves halfway)."""
    from chrono_mail.engine.mailbox import open_mailbox
    from chrono_mail.ui.dragdata import read_messages_mime_data, read_messages_rest
    from tests.ui.test_ui_rapid_actions import mail

    held = hold_the_rest_of_the_order(empty, monkeypatch)
    fill(empty, 250)
    window = empty.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == 250 and bool(held))
    window.list_view.setFocus()
    window.list_view.selectAll()
    drag = window.list_view.make_drag()
    assert drag is not None
    with open_mailbox(empty.engine, empty.account_id) as mailbox:
        mailbox.deliver(("Inbox",), mail(9999, subject="Note 00120x", message_id="<just-in@x>"), frozenset())
    qtbot.wait(100)
    held[0]()  # during the drag: the selection's places not known yet cannot be told any more
    wait(qtbot, lambda: model.complete)
    dragged, rest = read_messages_mime_data(drag.mimeData()), read_messages_rest(drag.mimeData())
    assert dragged is not None and rest is not None
    window.mail_actions.messages_dropped(dragged[0], "Junk", dragged[1], rest)
    wait(qtbot, lambda: "Nothing was done" in window.status_label.text())
    qtbot.wait(200)
    assert held_in(empty, "Junk") == 0


def _opened_and_held(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> list[Callable[[], None]]:
    held = hold_the_rest_of_the_order(empty, monkeypatch)
    fill(empty, 250)
    empty.window.start()
    wait(qtbot, lambda: empty.window.list_model.rowCount() == 250 and bool(held))
    empty.window.list_view.setFocus()
    return held


def test_a_waiting_delete_takes_what_was_selected_at_the_key_press(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206 (D-146): Ctrl+A and Delete waited for the rest of the order; a click on one message meanwhile made the Delete
    take only that message."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))  # a click on one message to read it
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {})  # noqa: SLF001


def test_a_write_told_later_while_the_view_loads_stops_a_waiting_action_and_says_so(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: a change the list hears no fact of (a big job, an import) could shift the places not known yet without
    changing their number. Such a write between the two answers counts: nothing is done then, and that is said; never an action on messages
    not selected."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    empty.call("folder.mark-read", account=empty.account_id, folder="Inbox")  # a big job: told as one fact at its end
    qtbot.wait(100)
    held[0]()
    wait(qtbot, lambda: "Nothing was done" in window.status_label.text())
    qtbot.wait(200)
    assert held_in(empty, "Trash") == 0


def test_a_message_read_while_the_view_loads_keeps_the_selection(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (RV-206): every write of the index counted, so the opened message being marked read while a large folder
    loaded cleared a Ctrl+A selection and stopped the Delete made on it. A change told at once is a fact the list judges itself."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    row = window.list_model.row_at(0)
    assert row is not None
    empty.call("message.flag", account=empty.account_id, folder="Inbox", message=row.message_id, seen=True)
    qtbot.wait(100)
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {})  # noqa: SLF001


def test_a_queued_drop_is_not_done_when_the_list_changes_although_nothing_on_screen_waits(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: after a drop that waited, a click left no selection on the places not known yet, and a new sort then made the
    drop move other messages than those dragged."""
    from chrono_mail.ui.dragdata import read_messages_mime_data, read_messages_rest

    held = _opened_and_held(empty, qtbot, monkeypatch)
    window, model = empty.window, empty.window.list_model
    window.list_view.selectAll()
    drag = window.list_view.make_drag()
    assert drag is not None
    dragged, rest = read_messages_mime_data(drag.mimeData()), read_messages_rest(drag.mimeData())
    assert dragged is not None and rest is not None
    window.mail_actions.messages_dropped(dragged[0], "Junk", dragged[1], rest)
    window.list_view.setCurrentIndex(model.index(3, 0))  # a click: nothing selected stays on a place not known yet
    spec = model.spec
    assert spec is not None
    model.rearrange(dataclasses.replace(spec, sort="subject"))
    wait(qtbot, lambda: "Nothing was done" in window.status_label.text())
    held[0]()
    qtbot.wait(300)
    assert held_in(empty, "Junk") == 0


def test_a_big_job_that_tells_nothing_while_the_view_loads_stops_a_waiting_action(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: a quiet job (an import, rules) changed the folder without a fact while the rest of the order was on its way;
    the waiting Delete then took the messages at the old numbers."""
    from chrono_mail.engine.mailbox import open_mailbox
    from tests.ui.test_ui_rapid_actions import mail

    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    with empty.engine.quietly(), open_mailbox(empty.engine, empty.account_id) as mailbox:  # tells nothing until it ends
        mailbox.deliver(("Inbox",), mail(9999, subject="Note 00120x", message_id="<quiet@x>"), frozenset())
    held[0]()
    wait(qtbot, lambda: "Nothing was done" in window.status_label.text())
    qtbot.wait(200)
    assert held_in(empty, "Trash") == 0


def test_a_waiting_drop_never_acts_again_on_what_a_waiting_delete_took(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206 (D-146): Delete and then a drag of the same selection both waited; the drop then moved messages the Delete had
    under way."""
    from chrono_mail.ui.dragdata import read_messages_mime_data, read_messages_rest

    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    drag = window.list_view.make_drag()
    assert drag is not None
    dragged, rest = read_messages_mime_data(drag.mimeData()), read_messages_rest(drag.mimeData())
    assert dragged is not None and rest is not None
    window.mail_actions.delete_selected()
    window.mail_actions.messages_dropped(dragged[0], "Junk", dragged[1], rest)
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {})  # noqa: SLF001
    assert held_in(empty, "Junk") == 0 and "Could not" not in window.status_label.text()


def test_undo_waits_for_an_action_that_waits_for_the_rest_of_the_order(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206 (B-18): Ctrl+Z right after a Delete that waited for the rest of the order undid the action before it."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    assert window.undoing.busy
    window.undoing.undo()  # pressed at once
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Inbox") == 250 and held_in(empty, "Trash") == 0 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001


def test_undo_is_ready_while_an_action_waits_and_waits_for_its_rows(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206 (B-18): Undo stayed greyed while the first action of a session waited; and Undo pressed after a Toggle read
    that still fetched its rows undid nothing of it (it ran before the toggle gave its command)."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.toggle_read()  # waits for the rest of the order, then for the rows it needs
    assert window.act_undo.isEnabled()
    window.undoing.undo()
    held[0]()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    wait(qtbot, lambda: "Undone: Mark as read (250 messages)" in window.status_label.text())  # the toggle it waited for, not an older one
    assert window.undoing.redo_label != ""


def test_an_action_on_known_messages_waits_its_turn_behind_a_waiting_one(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: an action on messages the list knew ran at once, before an earlier action that waited for the rest of the
    order; Undo then undid the earlier one."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()  # waits
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))  # one message the list knows
    window.mail_actions.toggle_flag()  # waits its turn
    qtbot.wait(200)
    assert empty.backend._pending == {} and held_in(empty, "Trash") == 0  # noqa: SLF001 - nothing ran yet
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001


def test_an_undo_pressed_for_an_action_that_was_not_done_undoes_nothing_older(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206 (B-18): Ctrl+A, Delete (waiting), Ctrl+Z; new mail then made the Delete impossible, and the Undo undid the
    action before it, which the person never asked about. Also: an action on messages known by key that only waited behind it is done."""
    from chrono_mail.engine.mailbox import open_mailbox
    from tests.ui.test_ui_rapid_actions import mail

    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.setCurrentIndex(window.list_model.index(5, 0))
    window.mail_actions.move_selected("Junk")  # an older action of the person, one Undo could undo
    wait(qtbot, lambda: held_in(empty, "Junk") == 1 and not window.undoing.busy)
    window.list_view.selectAll()
    window.mail_actions.delete_selected()  # waits
    window.undoing.undo()  # meant for the Delete
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))
    window.mail_actions.toggle_flag()  # known by key: waits only behind the Delete
    with open_mailbox(empty.engine, empty.account_id) as mailbox:
        mailbox.deliver(("Inbox",), mail(9999, subject="Note 00120x", message_id="<late@x>"), frozenset())
    qtbot.wait(100)
    held[0]()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    assert held_in(empty, "Trash") == 0 and held_in(empty, "Junk") == 1  # nothing deleted, the older move not undone
    flagged = [row for row in message_list(empty.engine, ListParameters(empty.account_id, "Inbox", limit=300)).messages if "flagged" in row.flags]
    assert len(flagged) == 1


def test_a_dropped_action_takes_only_the_undo_presses_made_after_it(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206 (B-18): one dropped waiting action also threw away an Undo pressed for another action; and presses were told
    apart by their place in a list, which shifted when several were dropped at once. Each press waits in the queue right after the action
    it is meant for, and goes only with that one."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    undoing, pane = window.undoing, window.messages
    window.list_view.selectAll()
    window.mail_actions.delete_selected()  # waits
    undoing.undo()  # meant for the Delete
    pane.after_waiting(lambda: None)  # another action, in turn behind
    undoing.undo()  # meant for that one
    assert [item.undo for item in pane._queue] == [False, True, False, True]  # noqa: SLF001
    first = pane._queue[0]  # noqa: SLF001
    pane._drop(lambda item: item is first)  # noqa: SLF001 - the Delete is not done
    assert [item.undo for item in pane._queue] == [False, True]  # noqa: SLF001 - the other action keeps its Undo
    pane._queue.clear()  # noqa: SLF001
    held[0]()
    wait(qtbot, lambda: empty.backend._pending == {})  # noqa: SLF001


def test_a_selection_cut_by_a_change_while_loading_is_cleared_and_said(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: Ctrl+A right after a large folder opened, then new mail; the places past the start were lost from the selection
    without a word, and a Delete a moment later took only the first 100."""
    from chrono_mail.engine.mailbox import open_mailbox
    from tests.ui.test_ui_rapid_actions import mail

    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    with open_mailbox(empty.engine, empty.account_id) as mailbox:
        mailbox.deliver(("Inbox",), mail(9999, subject="Note 00120x", message_id="<new@x>"), frozenset())
    qtbot.wait(100)
    held[0]()
    wait(qtbot, lambda: "select the messages again" in window.status_label.text())
    assert window.messages.selected_rows() == []  # nothing half selected is left to act on


def test_a_selection_kept_whole_gives_no_word_when_another_view_opens(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: after Ctrl+A on a loading folder whose order then came whole (nothing lost), opening another view said
    "select the messages again"."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    held[0]()
    wait(qtbot, lambda: window.list_model.complete)
    assert len(window.messages.selected_rows()) == 250
    window.list_model.show(None)
    assert "select the messages again" not in window.status_label.text()


def test_undo_meant_for_an_action_that_runs_stays_when_an_earlier_one_is_dropped(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206 (B-18): a dropped Delete took away the Undo pressed for the Flag queued after it, which did run."""
    from chrono_mail.engine.mailbox import open_mailbox
    from tests.ui.test_ui_rapid_actions import mail

    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()  # waits for places: dropped below
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))
    window.mail_actions.toggle_flag()  # known by key: runs
    window.undoing.undo()  # meant for the Flag
    with open_mailbox(empty.engine, empty.account_id) as mailbox:
        mailbox.deliver(("Inbox",), mail(9999, subject="Note 00120x", message_id="<new2@x>"), frozenset())
    qtbot.wait(100)
    held[0]()
    wait(qtbot, lambda: "Undone" in window.status_label.text() and empty.backend._pending == {})  # noqa: SLF001 - the Flag was undone
    assert held_in(empty, "Trash") == 0


def test_a_failed_order_question_ends_what_waits_for_it(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: the rest of the order could not be read; the waiting Delete stayed queued and ran much later on another
    selection."""
    from chrono_mail.core import ChronoMailError

    held: list[Callable[[], None]] = []
    real_ask = empty.backend.ask

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_ORDER and isinstance(parameters, OrderParameters) and parameters.whole and not held and on_error is not None:
            failing = on_error
            held.append(lambda: failing(ChronoMailError("The disk could not be read")))
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(empty.backend, "ask", ask)
    fill(empty, 250)
    window = empty.window
    window.start()
    wait(qtbot, lambda: window.list_model.rowCount() == 250 and bool(held))
    window.list_view.setFocus()
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    held[0]()
    wait(qtbot, lambda: "Nothing was done" in window.status_label.text())
    qtbot.wait(200)
    assert held_in(empty, "Trash") == 0


def test_an_action_waiting_for_places_a_new_sort_cannot_follow_is_not_done_and_says_so(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of RV-206: a new sort while the order was on its way dropped the selected places not known yet, and the waiting Delete
    then deleted only the known ones, without a word."""
    from PySide6.QtCore import QItemSelection, QItemSelectionModel

    held = hold_the_rest_of_the_order(empty, monkeypatch)
    fill(empty, 250)
    window = empty.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == 250 and bool(held))
    window.list_view.setFocus()
    selection = QItemSelection(model.index(50, 0), model.index(199, model.columnCount() - 1))
    window.list_view.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    window.mail_actions.delete_selected()
    spec = model.spec
    assert spec is not None
    model.rearrange(dataclasses.replace(spec, sort="subject"))  # another sort while it waits
    wait(qtbot, lambda: "Nothing was done" in window.status_label.text())
    assert window.messages.selected_rows() == []  # the known part of the cut selection is not left to a second key press either
    held[0]()
    qtbot.wait(300)
    assert held_in(empty, "Trash") == 0


def test_a_selected_range_keeps_its_places_when_the_rest_of_the_order_comes(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-206: a Shift range reaching past the first 100 lost the rows not known yet when the rest of the order came, so an action
    waiting for it took only the known ones."""
    from PySide6.QtCore import QItemSelection, QItemSelectionModel

    held = hold_the_rest_of_the_order(empty, monkeypatch)
    fill(empty, 250)
    window = empty.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == 250 and bool(held))
    window.list_view.setFocus()
    selection = QItemSelection(model.index(50, 0), model.index(199, model.columnCount() - 1))
    window.list_view.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    window.mail_actions.delete_selected()  # waits for the rest of the order
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 150 and empty.backend._pending == {})  # noqa: SLF001
    assert held_in(empty, "Inbox") == 100


def test_an_action_right_after_opening_waits_for_the_rest_of_the_order(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """W12b: for a moment after a large folder opens the list knows only its first 100 messages; Ctrl+A and Delete then acted on 100."""
    held: list[Callable[[], None]] = []
    real_ask = empty.backend.ask

    def ask[P, R](
        command: TypedCommand[P, R], parameters: P, on_success: Callable[[R], None], on_error: Failure | None = None, owner: QObject | None = None,
        in_order: bool = False, waiting: bool = False, checking: bool = False,
    ) -> int:
        if command is MESSAGE_ORDER and isinstance(parameters, OrderParameters) and parameters.whole and not held:

            def later() -> None:
                real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

            held.append(later)  # the rest of the order is held back until the test lets it through
            return 0
        return real_ask(command, parameters, on_success, on_error, owner, in_order, waiting, checking)

    monkeypatch.setattr(empty.backend, "ask", ask)
    fill(empty, 250)
    window = empty.window
    window.start()
    model = window.list_model
    wait(qtbot, lambda: model.rowCount() == 250 and bool(held))
    assert not model.complete
    window.list_view.setFocus()
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    assert held_in(empty, "Trash") == 0  # it waits: nothing done yet on 100 of them
    held[0]()  # the rest of the order arrives
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {})  # noqa: SLF001
    assert held_in(empty, "Inbox") == 0


def test_messages_that_leave_in_one_moment_go_in_one_step(empty: World, qtbot: QtBot) -> None:
    """The Windows computer's note at 15,927 messages: the rows left one by one, each laying the list out and painting it again, and the
    window answered slowly for minutes. The facts of one moment now go together: one removal for a run of neighbouring rows."""
    opened(qtbot, empty, 300)
    model = empty.window.list_model
    wait(qtbot, lambda: model.complete)
    keys = [place[0] for place in (model.place_at(row) for row in range(model.rowCount())) if place is not None]
    removals: list[int] = []

    def removed(parent: QModelIndex, first: int, last: int) -> None:
        removals.append(last - first + 1)

    model.rowsRemoved.connect(removed)
    for number, key in enumerate(keys):
        model.apply(Change("removed", empty.account_id, "Inbox", key, sequence=1_000_000 + number))
    assert model.rowCount() > 0  # nothing went one by one
    wait(qtbot, lambda: model.rowCount() == 0)
    assert len(removals) <= 3 and sum(removals) >= 300  # the rows and their group headings, in a few runs


def test_toggle_read_counts_every_selected_message(empty: World, qtbot: QtBot) -> None:
    opened(qtbot, empty, 250)
    model = empty.window.list_model
    empty.window.mail_actions.mark_messages([item for item in empty.window.messages.selected_rows() if item.row is not None], seen=True)
    wait(qtbot, lambda: empty.backend._pending == {})  # noqa: SLF001
    empty.window.mail_actions.toggle_read()  # the loaded ones are read, the others not: all become read
    wait(qtbot, lambda: empty.backend._pending == {} and all("seen" in getattr(model.row_at(row), "flags", ()) for row in range(250)))  # noqa: SLF001
    assert message_list(empty.engine, ListParameters(empty.account_id, "Inbox")).unread == 0


def test_the_key_press_works_once_not_once_per_selected_message(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """W13: every row greyed at the key press was redrawn alone, and every redraw looked at the whole selection again: 1,042 selected
    messages froze the window for about 139 s."""
    opened(qtbot, empty, 300)
    window = empty.window
    model = window.list_model
    for row in range(0, 300, 50):  # every row loaded, as after scrolling to the end
        model.row_at(row, fetch=True)
    wait(qtbot, lambda: all(model.row_at(row) is not None for row in range(300)) and empty.backend._pending == {})  # noqa: SLF001
    window.list_view.selectAll()
    read: list[Placed] = []  # every selected message the Archive button's check reads, over all its runs
    redraws: list[int] = []
    real_all_archived = MailState.all_archived

    def all_archived(state: MailState, rows: Iterable[Placed]) -> bool:
        def counted() -> Iterator[Placed]:
            for row in rows:
                read.append(row)
                yield row

        return real_all_archived(state, counted())

    def redrawn(first: QModelIndex | QPersistentModelIndex, last: QModelIndex | QPersistentModelIndex) -> None:
        redraws.append(last.row() - first.row() + 1)

    monkeypatch.setattr(MailState, "all_archived", all_archived)
    model.dataChanged.connect(redrawn)
    window.mail_actions.delete_selected()
    assert redraws == [300]  # the greyed rows: one redraw for the run of rows, not 300
    assert len(read) <= 300  # the selection is read once at most, however often the check runs (it runs again as the rows turn grey, D-266)
    wait(qtbot, lambda: held_in(empty, "Trash") == 300 and empty.backend._pending == {})  # noqa: SLF001
    model.dataChanged.disconnect(redrawn)


def test_a_drag_takes_every_selected_message_also_those_not_loaded(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """RV-77: Ctrl+A on 250 and a drag to Junk moved the 100 rows loaded so far, while the badge said 250."""
    from chrono_mail.ui import messagelistview
    from chrono_mail.ui.dragdata import read_messages_mime_data

    counts: list[int] = []
    real_badge = messagelistview.drag_badge

    def badge(count: int) -> object:
        counts.append(count)
        return real_badge(count)

    monkeypatch.setattr(messagelistview, "drag_badge", badge)
    opened(qtbot, empty, 250)
    drag = empty.window.list_view.make_drag()
    assert drag is not None
    dragged = read_messages_mime_data(drag.mimeData())
    assert dragged is not None and dragged[0] == empty.account_id and len(dragged[1]) == 250 and counts == [250]
    empty.window.mail_actions.messages_dropped(dragged[0], "Junk", dragged[1])
    wait(qtbot, lambda: held_in(empty, "Junk") == 250 and empty.backend._pending == {})  # noqa: SLF001
    assert held_in(empty, "Inbox") == 0



def test_mark_all_as_read_waiting_in_turn_acts_on_the_folder_it_was_made_in(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (D-146): Mark all as read made while a Delete waited ran when another folder was opened, and marked that
    folder's messages read, which the person never chose."""
    from chrono_mail.engine.mailbox import open_mailbox
    from tests.ui.test_ui_rapid_actions import mail

    with open_mailbox(empty.engine, empty.account_id) as mailbox:
        mailbox.deliver(("Junk",), mail(900, subject="Unread in Junk", message_id="<junk900@x>"), frozenset())
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()  # waits for the rest of the order
    window.mail_actions.mark_folder_read()  # in its turn behind it: for the Inbox
    found = window.folders.find_item((empty.account_id, "folder", "Junk"))
    assert found is not None
    window.tree.setCurrentIndex(found)  # another folder opens before the order came
    wait(qtbot, lambda: window.mail_state.view.folder == "Junk" and empty.backend._pending == {})  # noqa: SLF001
    junk = message_list(empty.engine, ListParameters(empty.account_id, "Junk")).messages
    assert [("seen" in row.flags) for row in junk] == [False]
    assert all("seen" in row.flags for row in message_list(empty.engine, ListParameters(empty.account_id, "Inbox", limit=1000)).messages)
    del held


def test_a_drop_waiting_in_turn_leaves_out_what_the_action_before_it_took(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (D-146): a drag of one message the list knew waited behind a Delete of everything; the Delete took it, and the
    drop then gave it a second action ("Could not move", or the message in Junk instead of Trash)."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    row = window.list_model.row_at(5)
    assert row is not None
    window.mail_actions.messages_dropped(empty.account_id, "Junk", [("Inbox", row.message_id)])
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {})  # noqa: SLF001
    assert held_in(empty, "Junk") == 0 and "Could not" not in window.status_label.text()


def test_an_undo_meant_for_a_waiting_action_that_did_nothing_undoes_nothing_older(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (B-18): an action waiting in turn that ended up giving no command (its messages were taken by the action
    before it) kept the Undo pressed for it, which then undid the person's older action."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()  # waits; it runs
    row = window.list_model.row_at(5)
    assert row is not None
    window.mail_actions.messages_dropped(empty.account_id, "Junk", [("Inbox", row.message_id)])  # waits in turn; it will find nothing left
    window.undoing.undo()  # meant for the drop
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    qtbot.wait(200)
    assert held_in(empty, "Inbox") == 0 and held_in(empty, "Trash") == 250  # the Delete was not undone


def test_a_message_changed_while_the_view_loads_stops_a_waiting_action(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (RV-206): a message whose content changed (a repeating message that wrote itself again: another subject or
    date) was not counted as a change of the view, unless the view filtered by marks; it may move to another place in the order, so the
    places not known yet cannot be trusted. Nothing is done then, and that is said."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()
    row = window.list_model.row_at(0)
    assert row is not None
    empty.engine.report(Change("changed", empty.account_id, "Inbox", row.message_id))
    qtbot.wait(100)
    held[0]()
    wait(qtbot, lambda: "Nothing was done" in window.status_label.text())
    qtbot.wait(200)
    assert held_in(empty, "Trash") == 0


def test_an_action_waiting_behind_one_that_fetches_its_rows_goes_after_it(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (D-146, B-18): Ctrl+A and Mark read waited, then a Delete of one message waited behind it. The mark first
    fetched its rows, and the Delete went first meanwhile: the message went to Trash unread (the mark left it out, as under way), and Undo
    undid the mark instead of the Delete."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.toggle_read()  # waits for the rest of the order, then for the rows it needs
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))  # a click on one message
    window.mail_actions.delete_selected()  # waits behind the mark
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 1 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    trashed = message_list(empty.engine, ListParameters(empty.account_id, "Trash")).messages
    assert ["seen" in row.flags for row in trashed] == [True]  # marked first, as pressed first
    window.undoing.undo()  # undoes the last action: the Delete
    wait(qtbot, lambda: held_in(empty, "Trash") == 0 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    assert all("seen" in row.flags for row in message_list(empty.engine, ListParameters(empty.account_id, "Inbox", limit=1000)).messages)


def test_an_undo_meant_for_an_action_that_did_nothing_goes_also_after_the_opened_message_was_marked(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (B-18): an Undo pressed for an action that then made nothing stayed when any command came between (the opened
    message marked read: a command, no action of the person), and undid an older action. It goes with the action that made nothing."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    undoing, pane = window.undoing, window.messages
    window.list_view.selectAll()
    window.mail_actions.delete_selected()  # waits; it runs

    def only_a_command() -> None:  # as the opened message marked read: a command, but no action of the person
        undoing.started()
        undoing.ended()

    pane.after_waiting(only_a_command)
    undoing.undo()  # meant for that one, which makes no action
    held[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    qtbot.wait(200)
    assert held_in(empty, "Trash") == 250 and held_in(empty, "Inbox") == 0  # the Delete was not undone


def test_an_action_made_while_one_fetches_its_rows_goes_after_it(empty: World, qtbot: QtBot) -> None:
    """The review of 3.6.350 (D-146, B-18): Ctrl+A and Mark read on a large view whose order was in fetched its rows first; a Delete of one
    message made meanwhile went at once, before it: the message went to Trash unread, and Undo then undid the mark, not the Delete."""
    opened(qtbot, empty, 250)  # every message selected, only the first rows loaded
    window = empty.window
    window.mail_actions.toggle_read()  # fetches the rows it needs first
    assert window.messages.queued
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))  # a click on one message
    window.mail_actions.delete_selected()  # made after the mark: goes after it
    wait(qtbot, lambda: held_in(empty, "Trash") == 1 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    trashed = message_list(empty.engine, ListParameters(empty.account_id, "Trash")).messages
    assert ["seen" in row.flags for row in trashed] == [True]
    window.undoing.undo()  # the last action: the Delete
    wait(qtbot, lambda: held_in(empty, "Trash") == 0 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    assert all("seen" in row.flags for row in message_list(empty.engine, ListParameters(empty.account_id, "Inbox", limit=1000)).messages)


def test_a_waiting_action_whose_messages_left_with_the_view_says_it_did_nothing(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (RV-206): a Delete waiting behind an action that fetched its rows ran after another folder was opened; none of its
    messages was in the list any more, and it did nothing without a word (or ran at once, ahead of the action before it)."""
    opened(qtbot, empty, 250)  # every message selected, the whole order in
    window = empty.window
    real_load = window.list_model.load_rows
    loads: list[Callable[[], None]] = []

    def load_later(keys: list[str], then: Callable[[], None]) -> None:  # the rows of the mark come only when the test lets them
        loads.append(lambda: real_load(keys, then))

    monkeypatch.setattr(window.list_model, "load_rows", load_later)
    window.mail_actions.toggle_read()  # fetches its rows first
    window.mail_actions.delete_selected()  # waits behind it
    found = window.folders.find_item((empty.account_id, "folder", "Junk"))
    assert found is not None
    window.tree.setCurrentIndex(found)  # another folder before the rows came
    wait(qtbot, lambda: window.mail_state.view.folder == "Junk")
    assert held_in(empty, "Trash") == 0  # not ahead of the mark
    loads[0]()
    wait(qtbot, lambda: "no longer in the list" in window.status_label.text())
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    assert held_in(empty, "Trash") == 0


def test_waiting_actions_run_in_the_order_made_also_when_one_fetches_its_rows(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (D-146): Mark read, Flag and Delete waited for the order; while the mark fetched its rows a drop was made. The
    flag found its rows at once, and the queue started over inside it: the drop ran before the Delete, which then found every message under
    way and was never done."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    real_load = window.list_model.load_rows
    first: list[Callable[[], None]] = []

    def first_load_later(keys: list[str], then: Callable[[], None]) -> None:
        if first:
            real_load(keys, then)
        else:
            first.append(lambda: real_load(keys, then))

    monkeypatch.setattr(window.list_model, "load_rows", first_load_later)
    window.list_view.selectAll()
    window.mail_actions.toggle_read()  # A: fetches its rows
    window.mail_actions.toggle_flag()  # B
    window.mail_actions.delete_selected()  # C
    held[0]()
    wait(qtbot, lambda: bool(first))
    row = window.list_model.row_at(5)
    assert row is not None
    window.mail_actions.messages_dropped(empty.account_id, "Junk", [("Inbox", row.message_id)])  # D, made last
    first[0]()
    wait(qtbot, lambda: held_in(empty, "Trash") == 250 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    assert held_in(empty, "Junk") == 0


def test_an_undo_meant_for_a_later_action_stays_when_an_earlier_one_did_nothing(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (B-18): an action fetching its rows found nothing left; every Undo pressed after it went too, also one meant for
    a Delete made after it, which then stayed done."""
    opened(qtbot, empty, 250)
    window = empty.window
    later: list[Callable[[], None]] = []
    def held_back(keys: Sequence[str], then: Callable[[], None]) -> None:
        later.append(then)

    monkeypatch.setattr(window.list_model, "load_rows", held_back)
    window.messages.with_rows([], lambda still, rows: None)  # an action fetching its rows; it will find nothing
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))
    window.mail_actions.delete_selected()  # waits behind it
    window.undoing.undo()  # meant for the Delete
    monkeypatch.undo()
    later[0]()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    assert held_in(empty, "Trash") == 0 and held_in(empty, "Inbox") == 250  # deleted, then undone


def test_an_undo_pressed_while_actions_wait_goes_between_them(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review of 3.6.350 (B-18): Ctrl+Z pressed for a Delete that waited, then a Flag: the Undo waited until nothing waited and undid
    the Flag; the messages stayed deleted."""
    held = _opened_and_held(empty, qtbot, monkeypatch)
    window = empty.window
    window.list_view.selectAll()
    window.mail_actions.delete_selected()  # waits
    window.undoing.undo()  # meant for the Delete
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))
    row = window.list_model.row_at(3)
    assert row is not None
    window.mail_actions.mark_selected(flagged=True)  # made after the Undo
    held[0]()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy and held_in(empty, "Inbox") == 250)  # noqa: SLF001
    flagged = [item for item in message_list(empty.engine, ListParameters(empty.account_id, "Inbox", limit=1000)).messages if "flagged" in item.flags]
    assert held_in(empty, "Trash") == 0 and [item.message_id for item in flagged] == [row.message_id]


def test_an_action_made_after_an_undo_that_waits_for_a_command_goes_after_it(empty: World, qtbot: QtBot) -> None:
    """The review of 3.6.350 (B-18): Ctrl+Z pressed while a command was under way waited for it, but a Delete made after the press went at
    once; the Undo then undid that Delete, not the action it was pressed for."""
    opened(qtbot, empty, 250)
    window = empty.window
    first = window.list_model.row_at(0)
    assert first is not None
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    window.mail_actions.delete_selected()
    wait(qtbot, lambda: held_in(empty, "Trash") == 1 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    window.undoing.started()  # a command still under way
    window.undoing.undo()  # meant for the Delete of the first message
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))
    second = window.list_model.row_at(3)
    assert second is not None
    window.mail_actions.delete_selected()  # made after the press: goes after it
    window.undoing.ended()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy and held_in(empty, "Trash") == 1)  # noqa: SLF001
    trashed = message_list(empty.engine, ListParameters(empty.account_id, "Trash")).messages
    assert [row.message_id for row in trashed] == [second.message_id]  # the first came back, the second went


def _held_behind_an_action(world: World, monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """An action fetching its rows holds the queue (it will find nothing) until the test calls what it returns (the rows load as before;
    the other patches, the answers to message boxes among them, stay)."""
    model = world.window.list_model
    real = model.load_rows
    later: list[Callable[[], None]] = []

    def held_back(keys: Sequence[str], then: Callable[[], None]) -> None:
        later.append(then)

    monkeypatch.setattr(model, "load_rows", held_back)
    world.window.messages.with_rows([], lambda still, rows: None)

    def release() -> None:
        monkeypatch.setattr(model, "load_rows", real)
        later[0]()

    return release


def _deleted_first(empty: World, qtbot: QtBot) -> None:
    """The first message is moved to Trash (an action Undo can undo), and that is done."""
    window = empty.window
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    window.mail_actions.delete_selected()
    wait(qtbot, lambda: held_in(empty, "Trash") == 1 and empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001


def test_an_undo_pressed_after_a_waiting_shift_delete_acts_as_it_would_at_once(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 17 of the review of 3.6.350 (B-18): Shift+Delete took no action number, so an Undo pressed after it while it waited was taken
    for one meant for an action that did nothing, and went without a word; at once it undoes the action before (Shift+Delete is never undone)."""
    opened(qtbot, empty, 250)
    window = empty.window
    _deleted_first(empty, qtbot)
    release = _held_behind_an_action(empty, monkeypatch)
    window.list_view.setCurrentIndex(window.list_model.index(3, 0))
    window.mail_actions.delete_permanently()  # waits behind it
    window.undoing.undo()
    release()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy and held_in(empty, "Trash") == 0)  # noqa: SLF001
    assert held_in(empty, "Inbox") == 249 and held_in(empty, "Trash") == 0  # the fourth deleted for good, and the first came back


def test_an_undo_pressed_after_a_waiting_unschedule_acts_as_it_would_at_once(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 17 of the review of 3.6.350 (B-18): a scheduled message moved to Drafts took no action number; an Undo pressed after it while
    it waited went without a word."""
    opened(qtbot, empty, 250)
    window = empty.window
    empty.call("mail.send", account=empty.account_id, to="you@example.com", subject="Later", body="x", send_at=in_days(empty, 30))
    (scheduled,) = message_list(empty.engine, ListParameters(empty.account_id, "Scheduled")).messages
    _deleted_first(empty, qtbot)
    release = _held_behind_an_action(empty, monkeypatch)
    window.messages.after_waiting(lambda: window.mail_actions.move_many("Drafts", [(empty.account_id, "Scheduled", scheduled.message_id)]))
    window.undoing.undo()
    release()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy and held_in(empty, "Drafts") == 1 and held_in(empty, "Trash") == 0)  # noqa: SLF001
    assert held_in(empty, "Inbox") == 250 and held_in(empty, "Trash") == 0  # the first came back


def in_days(world: World, days: int) -> str:
    """A local time this many days after the world's clock."""
    import datetime

    return datetime.datetime.fromtimestamp(world.engine.clock() + days * 86400).strftime("%Y-%m-%dT%H:%M")


def test_a_click_on_the_delete_icon_made_after_an_undo_goes_after_it(empty: World, qtbot: QtBot) -> None:
    """The final review of 3.6.350 (B-18): the Delete icon skipped the queue; clicked after a held Ctrl+Z it went first, and the Undo undid
    it instead of the Delete it was pressed for."""
    opened(qtbot, empty, 250)
    window = empty.window
    _deleted_first(empty, qtbot)
    window.undoing.started()  # a command still under way
    window.undoing.undo()  # meant for the Delete of the first message
    second = window.list_model.row_at(3)
    assert second is not None
    window._icon_clicked(3, "trash")  # noqa: SLF001 - made after the press: goes after it
    window.undoing.ended()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy and held_in(empty, "Trash") == 1 and held_in(empty, "Inbox") == 249)  # noqa: SLF001
    trashed = message_list(empty.engine, ListParameters(empty.account_id, "Trash")).messages
    assert [row.message_id for row in trashed] == [second.message_id]  # the first came back, the clicked one went


def test_a_mark_or_tag_with_no_message_left_takes_no_action_number(empty: World, qtbot: QtBot) -> None:
    """The final review of 3.6.350 (B-18): Mark as read and a tag with nothing left to act on took a number, so the Undo pressed after
    them undid an older action."""
    opened(qtbot, empty, 250)
    window = empty.window
    before = window.undoing.new_action()
    window.mail_actions.mark_messages([], seen=True)
    window.tags.toggle_tag("Family", True, chosen=[])
    assert window.undoing.new_action() == before + 1


def test_redo_can_be_pressed_while_an_action_is_under_way(empty: World, qtbot: QtBot) -> None:
    """The final review of 3.6.350 (B-18): Ctrl+Y right after a Ctrl+Z still under way did nothing (Redo was greyed until the Undo was
    answered); pressed while busy it waits in turn, as Undo does."""
    opened(qtbot, empty, 250)
    window = empty.window
    window.undoing.started()
    window.undoing.labels_changed.emit()
    assert window.act_redo.isEnabled() and window.act_undo.isEnabled()
    window.undoing.ended()
    wait(qtbot, lambda: not window.undoing.busy and empty.backend._pending == {})  # noqa: SLF001


def test_a_click_on_the_delete_icon_never_deletes_another_mailbox_s_message_of_the_same_id(empty: World, qtbot: QtBot) -> None:
    """The last review of 3.6.350: the icon named its message by id only, while ids are unique only within one mailbox (Favorites): the
    message found could be another mailbox's; nothing is done then, and that is said."""
    opened(qtbot, empty, 250)
    window = empty.window
    clicked = window.list_model.row_at(3)
    assert clicked is not None
    window.mail_actions.delete_by_icon(dataclasses.replace(clicked, account="another mailbox"))
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    assert held_in(empty, "Trash") == 0 and "no longer in the list" in window.report.label.text()


def test_a_click_on_the_delete_icon_that_waited_does_nothing_in_another_view(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The last review of 3.6.350: a click that waited its turn acted in the view shown by then, where another mailbox's message can
    have the same id; it acts only in the view it was clicked in, and says so otherwise."""
    opened(qtbot, empty, 250)
    window = empty.window
    clicked = window.list_model.row_at(3)
    assert clicked is not None
    release = _held_behind_an_action(empty, monkeypatch)
    window.mail_actions.delete_by_icon(clicked)  # waits its turn
    shown = window.list_model.spec
    assert shown is not None
    window.list_model.show(dataclasses.replace(shown, unread=True))  # another view, holding the same message (a filter clicked)
    wait(qtbot, lambda: window.list_model.find(clicked.message_id) >= 0)
    release()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy)  # noqa: SLF001
    assert held_in(empty, "Trash") == 0 and "no longer in the list" in window.report.label.text()


def test_a_click_on_the_delete_icon_that_waited_still_counts_after_a_sort(empty: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """The last review of 3.6.350: a sort while the click waited made it refused, though the message was still in the list."""
    opened(qtbot, empty, 250)
    window = empty.window
    clicked = window.list_model.row_at(3)
    assert clicked is not None
    release = _held_behind_an_action(empty, monkeypatch)
    window.mail_actions.delete_by_icon(clicked)  # waits its turn
    window.messages.set_sort("subject", True)
    wait(qtbot, lambda: (shown := window.list_model.spec) is not None and shown.sort == "subject")  # the sort is in
    release()
    wait(qtbot, lambda: empty.backend._pending == {} and not window.undoing.busy and held_in(empty, "Trash") == 1)  # noqa: SLF001
    trashed = message_list(empty.engine, ListParameters(empty.account_id, "Trash")).messages
    assert [row.message_id for row in trashed] == [clicked.message_id]
