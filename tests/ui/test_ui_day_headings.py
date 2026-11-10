from collections.abc import Mapping
from datetime import datetime

import pytest
from PySide6.QtCore import QObject, QPersistentModelIndex, QPoint, Qt
from PySide6.QtWidgets import QAbstractItemView
from pytestqt.qtbot import QtBot

from chrono_mail.core import ChronoMailError
from chrono_mail.engine import commands_view
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.engine.session import Engine
from chrono_mail.ui.backend import Failure, Success
from chrono_mail.ui.dayheadings import DayWatch
from chrono_mail.ui.messagelist import ViewSpec
from tests.mailfactory import raw_message
from tests.ui.test_ui_list_changes import Watched, ids
from tests.ui.test_ui_window import started
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_day_headings.py 4.4.563"

# --- Version history ----------------------------------------------------
# v4.4.563: Initial: the day headings follow the day: a new day asks them again in place, and mail of a new day never joins the old
#           day's Today (D-321)

SUNDAY_EVENING: datetime = datetime(2026, 11, 8, 20, 0).astimezone()  # a Sunday, local time
TUESDAY_MORNING: datetime = datetime(2026, 11, 10, 9, 0).astimezone()  # two days later


def _dated(world: World, folder: str, subject: str, day: int) -> None:
    """A message that carries noon of this day of November 2026 (local time) as its own date."""
    with open_mailbox(world.engine, world.account_id) as mailbox:
        message = raw_message(subject=subject, message_id=f"<{subject.replace(' ', '-')}@x>", date=datetime(2026, 11, day, 12, 0).astimezone())
        mailbox.deliver(mailbox.folder(folder), message, frozenset())


def _at(world: World, moment: datetime) -> None:
    world.engine.clock = moment.timestamp


@pytest.mark.parametrize("sort", ["date", "sent"])  # Received (a message no server received: its own date) and Sent
def test_a_new_day_asks_the_headings_again_in_place(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, sort: str) -> None:
    """The person's screenshot: the list opened on Sunday kept Sunday's headings on Tuesday."""
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject, day in (("Monday mail", 9), ("Sunday mail", 8), ("Saturday mail", 7)):
        _dated(world, "Kept", subject, day)
    _at(world, SUNDAY_EVENING)
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort=sort), groups=True)
    assert watched.headers() == ["Today", "Yesterday"]  # a Monday date is ahead of Sunday: it counts as Today
    scrolled: list[bool] = []
    watched.model.rearranged.connect(lambda: scrolled.append(True))
    _at(world, TUESDAY_MORNING)
    watched.model.new_day("2026-11-10")
    wait(qtbot, lambda: watched.headers() == ["Yesterday", "Last week"])
    watched.settle()
    assert watched.subjects() == ["Monday mail", "Sunday mail", "Saturday mail"] and watched.resets == 0  # in place: nothing thrown away
    assert scrolled == []  # the person did nothing: the list does not jump to the current message


def test_mail_of_a_new_day_never_joins_the_old_days_today_in_a_favorites_view(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    for message in ids(world).values():  # the test profile's own Inbox mail is stored at the real moment: only dated mail stays
        world.call("message.delete", account=world.account_id, folder="Inbox", message=message)
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject, day in (("Sunday mail", 8), ("Saturday mail", 7)):
        _dated(world, "Inbox", subject, day)
    _dated(world, "Kept", "Monday mail", 9)
    _at(world, SUNDAY_EVENING)
    watched = Watched(world, qtbot, monkeypatch, ViewSpec("", combined="inboxes", sort="sent"), groups=True)
    assert watched.headers() == ["Today", "Yesterday"]
    _at(world, TUESDAY_MORNING)
    world.call("message.move", account=world.account_id, folder="Kept", message=ids(world, "Kept")["Monday mail"], target="Inbox")  # into Favorites > Inboxes
    wait(qtbot, lambda: watched.headers() == ["Yesterday", "Last week"])
    watched.settle()
    assert watched.subjects() == ["Monday mail", "Sunday mail", "Saturday mail"] and watched.resets == 0


def test_a_new_day_whose_order_fails_keeps_the_list_without_a_word_and_the_next_look_asks_again(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject, day in (("Sunday mail", 8), ("Saturday mail", 7)):
        _dated(world, "Kept", subject, day)
    _at(world, SUNDAY_EVENING)
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort="sent"), groups=True)
    complaints: list[str] = []
    watched.model.load_failed.connect(complaints.append)
    watched.model.places_lost.connect(complaints.append)
    real_call = world.backend.call
    refused: list[str] = []

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if command == "message.order" and on_error is not None:
            refused.append(command)
            on_error(ChronoMailError("The index could not be read"))
            return 0
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    _at(world, TUESDAY_MORNING)  # an order that came would bring other headings
    watched.model.new_day("2026-11-10")
    watched.settle()
    assert refused == ["message.order"]  # it was asked, and refused
    assert watched.subjects() == ["Sunday mail", "Saturday mail"] and watched.headers() == ["Today", "Yesterday"] and watched.resets == 0
    assert complaints == []  # the person did nothing: no error line, nothing waiting dropped
    monkeypatch.setattr(world.backend, "call", real_call)
    watched.model.new_day("2026-11-10")  # the next look, the same day
    wait(qtbot, lambda: watched.headers() == ["Last week"])
    assert watched.subjects() == ["Sunday mail", "Saturday mail"] and watched.resets == 0


def test_mail_that_came_in_while_a_failing_new_day_order_was_on_its_way_is_placed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject, day in (("Sunday mail", 8), ("Saturday mail", 7)):
        _dated(world, "Kept", subject, day)
    _dated(world, "Inbox", "Monday mail", 9)
    _at(world, SUNDAY_EVENING)
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort="sent"), groups=True)
    real_call = world.backend.call
    held: list[Failure] = []

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        if command == "message.order" and on_error is not None:
            held.append(on_error)  # refused later, after the mail came in
            return 0
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    _at(world, TUESDAY_MORNING)
    watched.model.new_day("2026-11-10")
    assert len(held) == 1
    world.call("message.move", account=world.account_id, folder="Inbox", message=ids(world)["Monday mail"], target="Kept")
    qtbot.waitUntil(lambda: bool(watched.model._arriving) and not watched.model._arrival_planned)  # noqa: SLF001 - heard, and its step ran: it waits for the order
    assert "Monday mail" not in watched.subjects()
    held.pop()(ChronoMailError("The index could not be read"))
    wait(qtbot, lambda: "Monday mail" in watched.subjects())  # placed all the same


def test_a_sort_changed_after_midnight_keeps_its_own_scroll_to_the_current_message(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject, day in (("Monday mail", 9), ("Sunday mail", 8), ("Saturday mail", 7)):
        _dated(world, "Kept", subject, day)
    _at(world, SUNDAY_EVENING)
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort="subject"), groups=True)  # its headings name no day
    kept: list[bool] = []
    watched.model.day_order_coming.connect(lambda: kept.append(True))
    _at(world, TUESDAY_MORNING)
    watched.model.rearrange(ViewSpec(world.account_id, "Kept", sort="sent"))  # the person sorts by Sent the next morning
    wait(qtbot, lambda: watched.headers() == ["Yesterday", "Last week"])
    watched.settle()
    assert kept == []  # not held at the top: the sort brings the current message into view, as always


def test_after_midnight_no_day_heading_is_folded_as_after_a_restart(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject, day in (("Monday mail", 9), ("Sunday mail", 8), ("Saturday mail", 7)):
        _dated(world, "Kept", subject, day)
    _at(world, SUNDAY_EVENING)
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort="sent"), groups=True)
    model = watched.model
    model.new_day("2026-11-08")  # the first look
    model.toggle_group(model.header_rows()[1])  # Yesterday folded
    assert watched.headers() == ["Today", "Yesterday"] and model.rowCount() == 4  # Saturday's mail out of sight
    _at(world, TUESDAY_MORNING)
    model.new_day("2026-11-10")
    wait(qtbot, lambda: watched.headers() == ["Yesterday", "Last week"])
    watched.settle()
    assert model.rowCount() == 5 and watched.subjects() == ["Monday mail", "Sunday mail", "Saturday mail"]  # every heading open, every message in sight


def test_a_list_fetched_again_after_midnight_before_the_look_has_no_day_heading_folded(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """A mail check after waking fetches the list again before the day watch looks: its headings are of the new day, and none is folded."""
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject, day in (("Monday mail", 9), ("Sunday mail", 8), ("Saturday mail", 7)):
        _dated(world, "Kept", subject, day)
    _at(world, SUNDAY_EVENING)
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort="sent"), groups=True)
    model = watched.model
    model.toggle_group(model.header_rows()[1])  # Yesterday folded
    assert model.rowCount() == 4
    _at(world, TUESDAY_MORNING)
    model.reload()
    wait(qtbot, lambda: watched.headers() == ["Yesterday", "Last week"])
    watched.settle()
    assert model.rowCount() == 5  # Tuesday's Yesterday is Monday's mail: not folded
    model.new_day("2026-11-10")  # the look that follows asks nothing and changes nothing
    watched.settle()
    assert model.rowCount() == 5 and watched.asked.count("message.order") == 1


@pytest.mark.parametrize(("way", "at_top"), [("look", False), ("reload", False), ("look", True)])  # the day watch, or a mail check's refetch first
def test_after_midnight_the_message_at_the_top_and_the_selection_stay(world: World, qtbot: QtBot, way: str, at_top: bool) -> None:
    for number in range(60):  # a week of mail, so the list scrolls and the headings above the screen change at midnight
        _dated(world, "Inbox", f"Dated {number:02d}", 1 + number % 7)
    _at(world, SUNDAY_EVENING)
    started(qtbot, world, rows=63)
    model, view, pane = world.window.list_model, world.window.list_view, world.window.messages
    world.window.act_show_groups.setChecked(True)  # View > Show groups
    wait(qtbot, lambda: len(model.header_rows()) >= 3 and model.settled and world.backend._pending == {})  # noqa: SLF001

    def top() -> str:
        row = view.indexAt(QPoint(0, 0)).row()
        while model.is_header(row):
            row += 1
        return str(getattr(model.row_at(row), "subject"))

    middle = model.header_rows()[2] + 3
    bar = view.verticalScrollBar()
    if at_top:
        view.scrollToTop()
    else:
        view.scrollTo(model.index(middle, 0), QAbstractItemView.ScrollHint.PositionAtTop)
        bar.setValue(bar.value() + view.visualRect(model.index(middle, 0)).height() // 2)  # the top row half out of sight
    view.setCurrentIndex(model.index(middle + 2, 0))
    chosen, first = getattr(model.row_at(middle + 2), "message_id"), top()
    first_row = view.indexAt(QPoint(0, 0)).row()
    offset = view.visualRect(model.index(first_row, 0)).top()
    headings = [str(model.index(row, 0).data()) for row in model.header_rows()]
    _at(world, TUESDAY_MORNING)
    if way == "look":
        pane.day_watch.looked.emit("2026-11-10")
    else:
        model.reload()
    wait(qtbot, lambda: [str(model.index(row, 0).data()) for row in model.header_rows()] != headings and model.settled and world.backend._pending == {})  # noqa: SLF001
    selected = [getattr(model.row_at(index.row()), "message_id") for index in view.selectionModel().selectedRows()]
    assert selected == [chosen]
    if at_top:
        assert bar.value() == 0  # a list at its top stays at its top
    else:
        assert top() == first and view.visualRect(model.index(view.indexAt(QPoint(0, 0)).row(), 0)).top() == offset  # to the pixel


@pytest.mark.parametrize(("sort", "oldest_first"), [("date", False), ("sent", False), ("sent", True)])  # oldest first: the new mail comes at the end
def test_mail_of_a_new_day_never_joins_the_old_days_today(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, sort: str, oldest_first: bool) -> None:
    """Monday's mail came in under Sunday's Today: its heading was counted for Monday, the list's for Sunday."""
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject, day in (("Sunday mail", 8), ("Saturday mail", 7)):
        _dated(world, "Kept", subject, day)
    _dated(world, "Inbox", "Monday mail", 9)
    _at(world, SUNDAY_EVENING)
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort=sort, oldest_first=oldest_first), groups=True)

    def order(items: list[str]) -> list[str]:
        return list(reversed(items)) if oldest_first else items

    assert watched.headers() == order(["Today", "Yesterday"])
    sunday = next(row for row in range(watched.model.rowCount()) if not watched.model.is_header(row) and getattr(watched.model.row_at(row), "subject") == "Sunday mail")
    held = QPersistentModelIndex(watched.model.index(sunday, 0))  # Sunday's mail, as the selection holds it
    _at(world, TUESDAY_MORNING)  # no look at the day yet: the arrival itself says it
    world.call("message.move", account=world.account_id, folder="Inbox", message=ids(world)["Monday mail"], target="Kept")
    wait(qtbot, lambda: watched.headers() == order(["Yesterday", "Last week"]))
    watched.settle()
    assert watched.subjects() == order(["Monday mail", "Sunday mail", "Saturday mail"]) and watched.resets == 0
    assert held.isValid() and getattr(watched.model.row_at(held.row()), "subject") == "Sunday mail"  # the selection stays on its message


def test_a_new_day_while_an_order_is_on_its_way_lets_it_land_and_then_asks_again(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sort changed to Sent just before midnight: its order, counted on Sunday, lands after the new day was told; it is asked once more."""
    world.call("folder.create", account=world.account_id, folder="Kept")
    for subject, day in (("Monday mail", 9), ("Sunday mail", 8), ("Saturday mail", 7)):
        _dated(world, "Kept", subject, day)
    _at(world, SUNDAY_EVENING)
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort="subject"), groups=True)
    counted = [SUNDAY_EVENING]  # the order on its way is counted before midnight, every later one after it

    def moment(engine: Engine) -> datetime:
        return counted.pop(0) if counted else TUESDAY_MORNING

    monkeypatch.setattr(commands_view, "heading_moment", moment)
    _at(world, TUESDAY_MORNING)
    landed: list[list[str]] = []
    watched.model.rearranged.connect(lambda: landed.append(watched.headers()))  # only the sort's own order says so: the day's order does not
    watched.model.rearrange(ViewSpec(world.account_id, "Kept", sort="sent"))  # on its way
    watched.model.new_day("2026-11-10")
    wait(qtbot, lambda: watched.headers() == ["Yesterday", "Last week"])
    watched.settle()
    assert landed == [["Today", "Yesterday"]]  # the order on its way landed with Sunday's headings, it was not replaced
    assert watched.asked.count("message.order") == 2 and watched.resets == 0  # then one more, for the new day


def test_the_day_watch_tells_a_new_day_once_and_looks_just_after_midnight(qtbot: QtBot) -> None:
    owner = QObject()  # deleted at the end of the test, with the watch, its timer and its connection, also when an assert fails
    try:
        moment = [datetime(2026, 11, 9, 23, 59).astimezone()]
        watch = DayWatch(owner, now=lambda: moment[0])
        assert watch._timer.interval() == 61_000  # noqa: SLF001 - one second past midnight
        told: list[str] = []
        watch.day_changed.connect(told.append)
        watch.look()
        assert told == []  # the same day
        moment[0] = datetime(2026, 11, 10, 0, 0, 1).astimezone()
        watch._state_changed(Qt.ApplicationState.ApplicationInactive)  # noqa: SLF001 - the program goes to the background: no look
        assert told == []
        watch._state_changed(Qt.ApplicationState.ApplicationActive)  # noqa: SLF001 - the program becomes active again after a sleep
        assert told == ["2026-11-10"] and watch.day == "2026-11-10"
        watch.look()
        assert told == ["2026-11-10"]  # told once
        moment[0] = datetime(2026, 11, 10, 12, 0).astimezone()
        watch.look()
        assert watch._timer.interval() == 300_000  # noqa: SLF001 - at most five minutes: a timer does not count the time the computer slept
    finally:
        owner.deleteLater()


def test_the_window_asks_its_headings_again_when_the_day_watch_tells_a_new_day(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    model, pane = world.window.list_model, world.window.messages
    world.window.act_show_groups.setChecked(True)  # View > Show groups
    wait(qtbot, lambda: model.header_rows() != [] and model.settled and world.backend._pending == {})  # noqa: SLF001
    asked: list[str] = []
    real_call = world.backend.call

    def call(
        command: str, values: Mapping[str, object], on_success: Success, on_error: Failure | None = None, owner: QObject | None = None, waiting: bool = False,
        in_order: bool = False, checking: bool = False,
    ) -> int:
        asked.append(command)
        return real_call(command, values, on_success, on_error, owner, waiting, in_order, checking)

    monkeypatch.setattr(world.backend, "call", call)
    row = next(row for row in range(model.rowCount()) if not model.is_header(row))
    world.window.list_view.setCurrentIndex(model.index(row, 0))
    chosen = getattr(model.row_at(row), "message_id")
    viewport = world.window.list_view.viewport()
    drawn: list[bool] = []
    monkeypatch.setattr(viewport, "update", lambda: drawn.append(True))
    pane.day_watch.looked.emit("2026-11-10")  # a look on another day than the test engine's clock (2027)
    wait(qtbot, lambda: "message.order" in asked and model.settled and world.backend._pending == {})  # noqa: SLF001
    selected = [getattr(model.row_at(index.row()), "message_id") for index in world.window.list_view.selectionModel().selectedRows()]
    assert selected == [chosen] and drawn == [True]  # in place, the selection kept; what is on screen drawn again ("14:03" becomes "Yesterday")


def test_an_empty_list_is_left_alone_on_a_new_day(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("folder.create", account=world.account_id, folder="Kept")
    watched = Watched(world, qtbot, monkeypatch, ViewSpec(world.account_id, "Kept", sort="sent"), groups=True)
    watched.model.new_day("2026-11-10")  # another day than the test engine's clock (2027)
    watched.settle()
    assert watched.asked == [] and watched.resets == 0  # no heading to put right: nothing asked, nothing reset
