from pathlib import Path

import pytest

from chrono_mail.engine import Engine
from chrono_mail.engine.commands_calendar import EVENT_LIST, EventList
from chrono_mail.engine.commands_calendar import ListParameters as EventListParameters
from chrono_mail.engine.commands_tasklist import TASK_PLACES, PlacesParameters
from tests.enginefactory import make_engine
from tests.test_engine_commands import REGISTRY, call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_pim_lists.py 1.9.266"

# --- Version history ----------------------------------------------------
# v1.9.266: R-45: the shown calendars stay under the cap of 5,000 per range (D-174); the old test asked 6,000
# v1.1.205: Initial tests: contacts, tasks and events are never cut at 5,000; hidden lists and calendars take no room (R-02)

MANY: int = 5_100  # more than the old cut of 5,000


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path)
    call(engine, "pim.enable")
    return engine


def collection(engine: Engine, kind: str) -> str:
    return next(c.collection for c in call(engine, "collection.list", kind=kind))


def cards(count: int) -> str:
    return "".join(f"BEGIN:VCARD\r\nVERSION:4.0\r\nUID:card-{number}\r\nFN:Person {number:05d}\r\nN:{number:05d};Person;;;\r\nEND:VCARD\r\n" for number in range(count))


def todos(count: int, prefix: str) -> str:
    body = "".join(f"BEGIN:VTODO\r\nUID:{prefix}-{number}\r\nSUMMARY:{prefix} {number:05d}\r\nDTSTAMP:20261020T000000Z\r\nEND:VTODO\r\n" for number in range(count))
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n{body}END:VCALENDAR\r\n"


def test_every_contact_of_a_large_address_book_is_listed(engine: Engine) -> None:
    book = collection(engine, "contacts")
    assert call(engine, "contact.import", collection=book, text=cards(MANY)).imported == MANY
    listed = call(engine, "contact.list", collection=book, limit=0)
    assert listed.total == MANY and len(listed.contacts) == MANY and len({row.item for row in listed.contacts}) == MANY
    assert len(call(engine, "contact.list", collection=book).contacts) == 200  # the command line still pages


def test_every_task_has_a_place_and_a_hidden_list_takes_no_room(engine: Engine) -> None:
    shown = collection(engine, "tasks")
    hidden = call(engine, "collection.create", kind="tasks", name="Hidden").collection
    assert call(engine, "task.import", list=hidden, text=todos(MANY, "hidden")).imported == MANY
    mine = [call(engine, "task.add", list=shown, summary=summary, due=due) for summary, due in (("Late", "2020-01-01"), ("Soon", "2099-01-01"), ("Whenever", ""))]
    call(engine, "task.complete", item=mine[0].item)
    places = REGISTRY.ask(engine, TASK_PLACES, PlacesParameters(lists=(shown,), state="all"))
    assert [place.item for place in places.places] == [mine[1].item, mine[2].item, mine[0].item]  # open before done, then by due date (none last)
    assert (places.total, places.active, places.completed) == (3, 2, 1) and places.places[0].due_day == "2099-01-01" and places.places[1].due_day == ""
    everything = call(engine, "task.places", state="all")
    assert everything.total == MANY + 3 and len(everything.places) == MANY + 3  # never cut
    page = call(engine, "task.list", state="all", items=(mine[0].item, mine[1].item))
    assert [row.summary for row in page.tasks] == ["Soon", "Late"]  # only the tasks asked for, in the order of the list
    one = call(engine, "task.places", lists=(shown,), state="open", item=mine[2].item)
    assert one.position == 1 and [place.item for place in one.places] == [mine[2].item]


def test_every_event_of_the_days_is_listed_only_for_the_calendars_shown(engine: Engine) -> None:
    """Every occurrence of the calendars shown is listed, and a hidden calendar takes no room: shown 2 x 2,000 = 4,000 stay
    under the cap of 5,000 per range (D-174), with the hidden 3,000 counted they would not (R-45: the old test asked 6,000)."""
    shown = collection(engine, "events")
    hidden = call(engine, "collection.create", kind="events", name="Hidden").collection
    for calendar, summary, count in ((shown, "Ping", 2_000), (shown, "Pong", 2_000), (hidden, "Other", 3_000)):
        call(engine, "event.add", calendar=calendar, summary=summary, start="2026-11-02T00:00", end="2026-11-02T00:01", time_zone="UTC", rrule=f"FREQ=MINUTELY;COUNT={count}")
    listed = REGISTRY.ask(engine, EVENT_LIST, EventListParameters(from_date="2026-11-02", to_date="2026-11-05", calendars=(shown,)))
    assert isinstance(listed, EventList) and len(listed.events) == 4_000 and not listed.cut and {event.summary for event in listed.events} == {"Ping", "Pong"}


def test_an_event_that_happens_too_often_is_said_to_be_cut(engine: Engine) -> None:
    call(engine, "event.add", calendar=collection(engine, "events"), summary="Tick", start="2026-11-02T00:00", end="2026-11-02T00:01", time_zone="UTC", rrule="FREQ=MINUTELY")
    listed = call(engine, "event.list", from_date="2026-11-02", to_date="2026-11-07")
    assert listed.cut and len(listed.events) == 5_000
