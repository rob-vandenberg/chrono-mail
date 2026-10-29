from datetime import datetime

from chrono_mail.engine.changes import MessageRow
from chrono_mail.engine.commands_view import ViewOrder
from chrono_mail.index.model import GroupCount
from tests.test_engine_commands import Setup, call, setup  # noqa: F401 - setup is a fixture

__all__ = ["setup"]

# --- Version ------------------------------------------------------------
__version__ = "test_engine_favorites_timed.py 4.2.426"

# --- Version history ----------------------------------------------------
# v4.2.426: Initial: Favorites > Snoozed (and Scheduled) carries when each message comes back in its first rows too, and sorts and groups by it, as its folder

NOW: float = 1_800_000_000.0
GROUP_ORDER: list[str] = ["today", "tomorrow", "this_week", "next_week", "later"]


def _iso(seconds_from_now: float) -> str:
    return datetime.fromtimestamp(NOW + seconds_from_now).strftime("%Y-%m-%dT%H:%M")


def _snoozed_three(setup: Setup) -> list[str]:
    """Three messages snoozed for 72, 1 and 480 hours; their ids in that order."""
    setup.engine.clock = lambda: NOW
    call(setup.engine, "mail.fetch", account=setup.account_id)
    inbox: list[MessageRow] = list(call(setup.engine, "message.list", account=setup.account_id, folder="Inbox").messages)
    for row, hours in zip(inbox[:3], (72, 1, 480), strict=True):
        call(setup.engine, "message.snooze", account=setup.account_id, folder="Inbox", message=row.message_id, until=_iso(hours * 3600))
    return [row.message_id for row in inbox[:3]]


def test_favorites_snoozed_sorts_by_when_messages_come_back_as_its_folder(setup: Setup) -> None:
    first, second, third = _snoozed_three(setup)
    rows: tuple[MessageRow, ...] = call(setup.engine, "message.combined", view="snoozed", sort="snoozed", oldest_first=True)
    assert [row.message_id for row in rows] == [second, first, third]
    folder: list[MessageRow] = list(call(setup.engine, "message.list", account=setup.account_id, folder="Snoozed", sort="snoozed", oldest_first=True).messages)
    assert [row.snoozed_until for row in rows] == [row.snoozed_until for row in folder] and all(row.snoozed_until is not None for row in rows)
    latest: tuple[MessageRow, ...] = call(setup.engine, "message.combined", view="snoozed", sort="snoozed")
    assert [row.message_id for row in latest] == [third, first, second]


def test_favorites_snoozed_carries_the_time_in_its_first_rows_in_any_sort(setup: Setup) -> None:
    _snoozed_three(setup)
    rows: tuple[MessageRow, ...] = call(setup.engine, "message.combined", view="snoozed", sort="subject")
    assert len(rows) == 3 and all(row.snoozed_until is not None for row in rows)
    order: ViewOrder = call(setup.engine, "message.combined-order", view="snoozed", sort="date", whole=False, first=2)
    assert order.rows and all(row.snoozed_until is not None for row in order.rows)


def test_favorites_snoozed_groups_and_whole_order_by_when_messages_come_back(setup: Setup) -> None:
    first, second, third = _snoozed_three(setup)
    groups: tuple[GroupCount, ...] = call(setup.engine, "message.combined-groups", view="snoozed", sort="snoozed", oldest_first=True)
    folder: tuple[GroupCount, ...] = call(setup.engine, "message.groups", account=setup.account_id, folder="Snoozed", sort="snoozed", oldest_first=True)
    assert groups == folder and sum(group.count for group in groups) == 3
    keys = [group.key for group in groups]
    assert keys == sorted(keys, key=GROUP_ORDER.index)
    order: ViewOrder = call(setup.engine, "message.combined-order", view="snoozed", sort="snoozed", oldest_first=True, whole=False, first=1, headings=True)
    assert order.complete and list(order.messages) == [second, first, third] and order.groups == groups
    assert [row.message_id for row in order.rows] == [second]


def test_favorites_scheduled_sorts_by_send_at(setup: Setup) -> None:
    setup.engine.clock = lambda: NOW
    for hours in (5, 2, 9):
        call(setup.engine, "mail.send", account=setup.account_id, to="you@example.com", subject=f"In {hours} hours", body="b", send_at=_iso(hours * 3600))
    rows: tuple[MessageRow, ...] = call(setup.engine, "message.combined", view="scheduled", sort="snoozed", oldest_first=True)
    assert [row.subject for row in rows] == ["In 2 hours", "In 5 hours", "In 9 hours"] and all(row.snoozed_until is not None for row in rows)


def test_favorites_snoozed_places_a_message_and_orders_its_headings_latest_first(setup: Setup) -> None:
    first, second, third = _snoozed_three(setup)
    places = call(setup.engine, "message.combined-places", view="snoozed", sort="snoozed", oldest_first=True, messages=(first,), mailboxes=(setup.account_id,), headings=True)
    assert places.positions == (1,)
    soonest: tuple[GroupCount, ...] = call(setup.engine, "message.combined-groups", view="snoozed", sort="snoozed", oldest_first=True)
    latest: tuple[GroupCount, ...] = call(setup.engine, "message.combined-groups", view="snoozed", sort="snoozed")
    assert latest == tuple(reversed(soonest))
    assert [row.message_id for row in call(setup.engine, "message.combined", view="snoozed", sort="snoozed")] == [third, first, second]
