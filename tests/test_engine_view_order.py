from datetime import UTC, datetime

from chrono_mail.core import MessageFlag
from chrono_mail.engine.mailbox import open_mailbox
from tests.mailfactory import raw_message
from tests.test_engine_commands import Setup, call, mail, setup  # noqa: F401 - setup is the fixture

# --- Version ------------------------------------------------------------
__version__ = "test_engine_view_order.py 2.0.280"

# --- Version history ----------------------------------------------------
# v2.0.280: the sort order sent (the message's own Date) in a folder and in a Favorites view, with its date headings (B-16)
# v1.6.260: Initial: the order of a folder, a search and a Favorites view in one answer, its start first for a large view; rows asked by the
#           message; each message that comes in is told the message it comes before (R-15)


def fill(setup: Setup, count: int) -> list[str]:
    """count more messages in the Inbox, read and unread by turns; their ids newest first."""
    call(setup.engine, "mail.fetch", account=setup.account_id)
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        for number in range(10, 10 + count):
            mailbox.deliver(mailbox.folder("Inbox"), mail(number), frozenset() if number % 2 else frozenset({MessageFlag.SEEN}))
    return [row.message_id for row in call(setup.engine, "message.list", account=setup.account_id, folder="Inbox", limit=1000).messages]


def test_the_order_of_a_folder_is_every_message_in_the_order_of_the_list(setup: Setup) -> None:
    listed = fill(setup, 7)
    order = call(setup.engine, "message.order", account=setup.account_id, folder="Inbox", first=3, rows_of=(listed[-1],))
    assert order.complete and order.messages == tuple(listed) and order.total == 10 and order.folders == ("Inbox",) * 10
    unread = {row.message_id for row in call(setup.engine, "message.list", account=setup.account_id, folder="Inbox", limit=1000).messages if "seen" not in row.flags}
    assert order.unread_total == len(unread) and {message for message, yes in zip(order.messages, order.unread, strict=True) if yes} == unread
    assert {row.message_id for row in order.rows} == {*listed[:3], listed[-1]}  # the start, and the message named
    by_subject = call(setup.engine, "message.order", account=setup.account_id, folder="Inbox", sort="subject", headings=True)
    assert by_subject.messages == tuple(row.message_id for row in call(setup.engine, "message.list", account=setup.account_id, folder="Inbox", sort="subject", limit=1000).messages)
    assert sum(group.count for group in by_subject.groups) == 10


def test_a_large_view_gives_its_start_first_with_the_numbers_of_all(setup: Setup) -> None:
    listed = fill(setup, 7)
    start = call(setup.engine, "message.order", account=setup.account_id, folder="Inbox", whole=False, first=4)
    assert not start.complete and start.messages == tuple(listed[:4]) and start.total == 10 and len(start.rows) == 4
    assert start.unread_total == call(setup.engine, "message.order", account=setup.account_id, folder="Inbox").unread_total
    small = call(setup.engine, "message.order", account=setup.account_id, folder="Inbox", whole=False, first=100)
    assert small.complete and small.messages == tuple(listed)  # it fits: one answer is the whole order


def test_a_search_and_a_favorites_view_give_their_whole_order_as_they_list_it(setup: Setup) -> None:
    fill(setup, 7)
    search = call(setup.engine, "message.order", account=setup.account_id, query="number", unread=True)
    found = call(setup.engine, "message.search", account=setup.account_id, query="number", unread=True, limit=1000)
    assert search.messages == tuple(row.message_id for row in found) and search.total == len(found) and all(search.unread)
    favorites = call(setup.engine, "message.combined-order", view="inboxes", sort="subject", first=2)
    combined = call(setup.engine, "message.combined", view="inboxes", sort="subject", limit=1000)
    assert favorites.messages == tuple(row.message_id for row in combined) and set(favorites.mailboxes) == {setup.account_id}
    assert [row.account for row in favorites.rows] == [setup.account_id] * 2
    start = call(setup.engine, "message.combined-order", view="inboxes", whole=False, first=4)
    assert not start.complete and start.total == 10 and len(start.messages) == 4


def test_rows_are_asked_by_the_message_and_a_message_gone_is_left_out(setup: Setup) -> None:
    listed = fill(setup, 3)
    call(setup.engine, "message.delete", account=setup.account_id, folder="Inbox", message=listed[1], permanent=True)
    answer = call(setup.engine, "message.rows", account=setup.account_id, messages=(listed[0], listed[1], listed[2]))
    assert {row.message_id for row in answer.rows} == {listed[0], listed[2]} and all(row.account == "" for row in answer.rows)
    combined = call(setup.engine, "message.rows", account=setup.account_id, messages=(listed[0],), combined=True)
    assert combined.rows[0].account != ""


def test_a_message_that_came_in_is_told_the_message_it_comes_before(setup: Setup) -> None:
    listed = fill(setup, 3)
    newest, second, third = listed[0], listed[1], listed[2]
    values = {"account": setup.account_id, "folder": "Inbox"}
    found = call(setup.engine, "message.places", messages=(second,), **values)
    assert found.before == (third,)
    together = call(setup.engine, "message.places", messages=(newest, second), **values)  # asked together: neither is held yet, so each comes before the next held one
    assert together.before == (third, third) and together.positions == (0, 1)
    last = call(setup.engine, "message.places", messages=(listed[-1],), **values)
    assert last.before == ("",)  # at the end
    favorites = call(setup.engine, "message.combined-places", view="inboxes", messages=(second,), mailboxes=(setup.account_id,))
    assert favorites.before == (third,) and favorites.before_mailboxes == (setup.account_id,)


def test_a_folder_and_a_favorites_view_sort_by_the_date_the_message_carries(setup: Setup) -> None:
    with open_mailbox(setup.engine, setup.account_id) as mailbox:
        for number, day in ((1, 3), (2, 1), (3, 2)):  # delivered in another order than they were dated
            dated = raw_message(subject=f"Dated {number}", message_id=f"<d{number}@x>", date=datetime(2026, 1, day, 12, tzinfo=UTC))
            mailbox.deliver(mailbox.folder("Inbox"), dated, frozenset())
    rows = call(setup.engine, "message.list", account=setup.account_id, folder="Inbox", sort="sent", limit=1000).messages
    assert [row.subject for row in rows] == ["Dated 1", "Dated 3", "Dated 2"]
    assert [row.sent for row in rows] == [datetime(2026, 1, day, 12, tzinfo=UTC) for day in (3, 2, 1)]
    combined = call(setup.engine, "message.combined", view="inboxes", sort="sent", oldest_first=True, limit=1000)
    assert [row.message_id for row in combined] == [row.message_id for row in reversed(rows)]
    order = call(setup.engine, "message.order", account=setup.account_id, folder="Inbox", sort="sent", headings=True)
    assert order.messages == tuple(row.message_id for row in rows) and sum(group.count for group in order.groups) == len(rows)
    favorites = call(setup.engine, "message.combined-order", view="inboxes", sort="sent", headings=True)
    assert sum(group.count for group in favorites.groups) == len(rows)
