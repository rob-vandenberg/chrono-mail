import json
from pathlib import Path

import pytest

from chrono_mail.core.colours import GOOGLE_EVENT_COLOURS
from chrono_mail.engine import Engine, collection_looks, google_colours
from chrono_mail.sync import davsync
from chrono_mail.engine.registry import UsageError
from tests.enginefactory import make_engine
from tests.fakedav import Collection, FakeDav
from tests.test_engine_commands import call
from tests.test_google_colours import CALENDAR, EVENT, FakeCalendarApi, google_world, item_of, on_both, ours, shown, tag_colour, tags_shown

# --- Version ------------------------------------------------------------
__version__ = "test_calendar_tags.py 4.4.602"

# --- Version history ----------------------------------------------------
# v4.4.602: Initial tests: a Google colour is a tag of its own calendar, known by name and calendar, shown with the calendar's name (D-341)

__all__ = ["google_world"]  # the fixture, used by name

ESTHER = "esther@gmail.com"


def test_two_calendars_with_a_colour_of_one_name_have_two_tags_and_the_persons_own_tag_is_untouched(
    google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner: "She has hers, I have mine." Each appointment takes the colour of its own calendar's tag (D-341)."""
    engine, account, calendars, api = google_world
    calendars.collections["esther"] = Collection("Esther", "calendar", "VEVENT")

    def calendar_of(href: str) -> str:
        end = href.rstrip("/")
        return CALENDAR if end.endswith("/events") else ESTHER if end.endswith("/esther") else ""

    monkeypatch.setattr(google_colours, "calendar_id_of", calendar_of)
    call(engine, "tag.create", name="Rood", colour="#123456")  # the person's own
    api.label("L-red", "#ff0000", "Rood")
    on_both(calendars, api, "u1", "Dentist", label="L-red")
    calendars.add("esther", "e1.ics", EVENT.format(uid="e1", summary="Lunch", extra=""))
    call(engine, "dav.sync", account=account)
    path = google_colours.colours_path(engine, account)
    kept = json.loads(path.read_text("utf-8"))
    esther = next(value for value in kept["calendars"].values() if value["calendar"] == ESTHER)  # its id is known; the fake API serves only Ann's
    esther.update(labels={"L-er": ["Rood", "#00aa00"]}, events={"e1": ["g-e1", "", "L-er"]})  # as Google would give Esther's calendar
    path.write_text(json.dumps(kept), "utf-8")
    call(engine, "dav.sync", account=account)
    tags = {tag.name: tag for tag in call(engine, "tag.list")}
    assert (tags["Rood"].colour, tags["Rood"].collection, tags["Rood"].shown) == ("#123456", "", "Rood")
    assert (tags[ours("Rood")].colour, tags[ours("Rood")].shown) == ("#ff0000", "Rood (Calendar)")
    assert (tags[f"Rood|{ESTHER}"].colour, tags[f"Rood|{ESTHER}"].shown) == ("#00aa00", "Rood (Esther)")
    assert tags_shown(engine) == {"Dentist": (ours("Rood"),), "Lunch": (f"Rood|{ESTHER}",)}
    assert shown(engine) == {"Dentist": "#ff0000", "Lunch": "#00aa00"}
    assert [row.summary for row in call(engine, "event.list", from_date="2030-11-20", to_date="2030-11-21", tag=f"Rood|{ESTHER}").events] == ["Lunch"]
    assert [row.summary for row in call(engine, "event.list", from_date="2030-11-20", to_date="2030-11-21", tag="Rood").events] == []  # the person's own is another tag


def test_a_calendar_tag_chosen_for_an_appointment_goes_to_google_first_and_never_into_the_appointment(
    google_world: tuple[Engine, str, FakeDav, FakeCalendarApi],
) -> None:
    engine, account, calendars, api = google_world
    api.label("L-red", "#ff0000", "Rood")
    api.label("L-blue", "#0000ff", "Blauw")
    on_both(calendars, api, "u1", "Dentist", label="L-blue")
    call(engine, "dav.sync", account=account)
    call(engine, "tag.create", name="Work", colour=GOOGLE_EVENT_COLOURS["10"])
    call(engine, "event.edit", item=item_of(engine, "Dentist"), tag=(ours("Rood"), "Work"))  # the calendar tag is its colour tag
    assert api.patches == [("g-u1", "L-red")]
    text = calendars.collections["events"].items["u1.ics"][1]
    assert "Rood" not in text and "CATEGORIES:Work" in text  # only the person's own tag is written into the appointment
    assert tags_shown(engine)["Dentist"] == (ours("Rood"), "Work") and shown(engine)["Dentist"] == "#ff0000"  # it leads its own tags
    call(engine, "dav.sync", account=account)
    assert tags_shown(engine)["Dentist"] == (ours("Rood"), "Work") and api.patches == [("g-u1", "L-red")]  # nothing sent again
    call(engine, "event.edit", item=item_of(engine, "Dentist"), tag=("Work", ours("Rood")))  # the person's own tag is its colour tag again
    call(engine, "dav.sync", account=account)
    assert api.patches[-1] == ("g-u1", "10")  # Google gets the colour of Work
    assert tags_shown(engine)["Dentist"] == ("Work",)  # its own tag says it all (D-339)


def test_a_calendar_tag_added_is_the_colour_tag_and_taking_it_off_takes_the_colour_off_google(
    google_world: tuple[Engine, str, FakeDav, FakeCalendarApi],
) -> None:
    engine, account, calendars, api = google_world
    api.label("L-red", "#ff0000", "Rood")
    on_both(calendars, api, "u1", "Dentist")
    call(engine, "dav.sync", account=account)
    call(engine, "event.edit", item=item_of(engine, "Dentist"), add_tag=(ours("Rood"),))
    assert api.patches == [("g-u1", "L-red")] and tags_shown(engine)["Dentist"] == (ours("Rood"),)
    call(engine, "event.edit", item=item_of(engine, "Dentist"), remove_tag=(ours("Rood"),))
    call(engine, "dav.sync", account=account)
    assert api.patches[-1] == ("g-u1", "") and tags_shown(engine)["Dentist"] == ()


def test_a_tag_of_another_calendar_is_never_given_to_an_appointment(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    api.label("L-red", "#ff0000", "Rood")
    on_both(calendars, api, "u1", "Dentist")
    call(engine, "dav.sync", account=account)
    with pytest.raises(UsageError, match="only to an appointment of that calendar"):
        call(engine, "event.edit", item=item_of(engine, "Dentist"), tag=(f"Rood|{ESTHER}",))
    assert api.patches == [] and "Rood" not in calendars.collections["events"].items["u1.ics"][1]


def test_a_tags_name_never_holds_a_vertical_bar(tmp_path: Path) -> None:
    """D-341: the bar marks a tag of a Google calendar, so no tag the person makes or gives holds one."""
    engine = make_engine(tmp_path)
    collections = {summary.kind: summary.collection for summary in call(engine, "pim.enable")}
    call(engine, "tag.create", name="Work", colour="#112233")
    attempts: list[tuple[str, dict[str, object]]] = [
        ("tag.create", {"name": "A|B"}),
        ("tag.rename", {"name": "Work", "new_name": "A|B"}),
        ("task.add", {"list": collections["tasks"], "summary": "Call", "tag": ("A|B",)}),
        ("contact.add", {"collection": collections["contacts"], "name": "Ann Lee", "tag": ("A|B",)}),
    ]
    for command, values in attempts:
        with pytest.raises(UsageError, match="vertical bar"):
            call(engine, command, **values)
    assert tag_colour(engine, "Work") == "#112233" and tag_colour(engine, "A|B") == ""


def test_a_refused_save_gives_google_nothing(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    """Server first: the save of the appointment comes first; a save the server refuses sends no colour to Google, then or later (D-193)."""
    engine, account, calendars, api = google_world
    api.label("L-red", "#ff0000", "Rood")
    on_both(calendars, api, "u1", "Dentist")
    call(engine, "dav.sync", account=account)
    calendars.writes_refused = True
    with pytest.raises(UsageError):
        call(engine, "event.edit", item=item_of(engine, "Dentist"), tag=(ours("Rood"),))
    calendars.writes_refused = False
    call(engine, "dav.sync", account=account)
    assert api.patches == [] and tags_shown(engine)["Dentist"] == ()


def test_a_google_colour_whose_name_holds_a_bar_is_still_its_calendars_tag(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    api.label("L-x", "#ff0000", "Werk|Thuis")  # a name made in Google Calendar may hold a bar; a calendar's id never does
    on_both(calendars, api, "u1", "Dentist")
    call(engine, "dav.sync", account=account)
    call(engine, "event.edit", item=item_of(engine, "Dentist"), tag=(ours("Werk|Thuis"),))
    assert api.patches == [("g-u1", "L-x")] and tags_shown(engine)["Dentist"] == (ours("Werk|Thuis"),)


def test_a_tag_with_a_bar_made_before_stays_usable(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    """A tag file or an appointment from before 4.4.602 may hold a name with a bar: it loads, and the appointment's tags can still be changed."""
    engine, account, calendars, api = google_world
    path = engine.directories.tags_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": 1, "tags": [{"name": "A|B", "colour": "#112233"}]}), "utf-8")
    on_both(calendars, api, "u1", "Dentist", extra="CATEGORIES:A|B\r\n")
    call(engine, "dav.sync", account=account)
    assert tag_colour(engine, "A|B") == "#112233"  # the tag file is read as it is
    call(engine, "tag.create", name="Work", colour="#445566")
    call(engine, "event.edit", item=item_of(engine, "Dentist"), tag=("A|B", "Work"))
    assert "CATEGORIES:A|B,Work" in calendars.collections["events"].items["u1.ics"][1] and api.patches == []


def test_a_save_that_becomes_a_sync_conflict_gives_google_nothing(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    """The server changed the appointment meanwhile: the person settles that first, and Google gets no colour before (D-193, D-194)."""
    engine, account, calendars, api = google_world
    api.label("L-red", "#ff0000", "Rood")
    on_both(calendars, api, "u1", "Dentist")
    call(engine, "dav.sync", account=account)
    item = item_of(engine, "Dentist")
    calendars.add("events", "u1.ics", EVENT.format(uid="u1", summary="Dentist at nine", extra=""))  # changed on the server, not seen here yet
    call(engine, "event.edit", item=item, summary="Dentist at ten", tag=(ours("Rood"),))
    call(engine, "dav.sync", account=account)
    assert api.patches == []


def test_a_date_that_follows_its_series_colour_keeps_following_it_after_a_save(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    """A title changed on one date of a series sends no colour: the date keeps the series' colour, also when it changes later (D-341)."""
    engine, account, calendars, api = google_world
    api.label("L-red", "#ff0000", "Rood")
    on_both(calendars, api, "u1", "Standup", extra="RRULE:FREQ=DAILY;COUNT=3\r\n", label="L-red")
    call(engine, "dav.sync", account=account)
    rows = call(engine, "event.list", from_date="2030-11-21", to_date="2030-11-22").events
    date_row = next(row for row in rows if row.summary == "Standup")
    assert date_row.tags == (ours("Rood"),)
    call(engine, "event.edit", item=date_row.item, occurrence=date_row.occurrence, summary="Standup, later", tag=(ours("Rood"),))  # as the window sends it
    api.add("g-u1_20301121T090000Z", "u1", series="g-u1", original="2030-11-21T09:00:00Z")  # Google makes the changed date an event of its own
    call(engine, "dav.sync", account=account)
    assert api.patches == []  # it never gets a colour of its own


def test_a_second_save_while_the_chosen_colour_waits_for_google_keeps_it(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi]) -> None:
    engine, account, calendars, api = google_world
    api.label("L-red", "#ff0000", "Rood")
    on_both(calendars, api, "u1", "Dentist")
    call(engine, "dav.sync", account=account)
    call(engine, "tag.create", name="Work", colour="#445566")
    api.busy = True  # Google cannot be reached: the choice waits
    call(engine, "event.edit", item=item_of(engine, "Dentist"), tag=(ours("Rood"), "Work"))
    call(engine, "event.edit", item=item_of(engine, "Dentist"), summary="Dentist, later", tag=(ours("Rood"), "Work"))  # saved again, the same tags
    api.busy = False
    call(engine, "dav.sync", account=account)
    assert api.patches == [("g-u1", "L-red")]


def google_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake server's calendar counts as a Google calendar wherever the rename asks (as the fixture does for the colours)."""
    def calendar_of(href: str) -> str:
        return CALENDAR if href.rstrip("/").endswith("/events") else ""

    for module in (davsync, collection_looks):
        monkeypatch.setattr(module, "calendar_id_of", calendar_of)


def test_renaming_a_google_calendar_gives_it_a_name_only_for_you(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch) -> None:
    """D-343: Google keeps one's own name for a calendar apart from its own name; a CalDAV rename would change it for everyone (seen live)."""
    engine, account, calendars, api = google_world
    google_addresses(monkeypatch)
    api.label("L-red", "#ff0000", "Rood")
    on_both(calendars, api, "u1", "Dentist", label="L-red")
    call(engine, "dav.sync", account=account)
    collection = next(item.collection for item in call(engine, "collection.list") if item.account == account and item.kind == "events")
    assert next(item.own_name for item in call(engine, "collection.list") if item.collection == collection)
    assert call(engine, "collection.edit", collection=collection, name="Mine").name == "Mine"
    assert api.own_names == {CALENDAR: "Mine"} and calendars.collections["events"].name == "Calendar"  # never the calendar's name for everyone
    assert next(tag.shown for tag in call(engine, "tag.list") if tag.name == ours("Rood")) == "Rood (Mine)"
    assert call(engine, "collection.edit", collection=collection, name="").name == "Calendar"  # empty: Google's name again
    assert api.own_names == {}


def test_a_refused_own_name_keeps_the_calendars_name(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, calendars, api = google_world
    google_addresses(monkeypatch)
    call(engine, "dav.sync", account=account)
    collection = next(item.collection for item in call(engine, "collection.list") if item.account == account and item.kind == "events")
    api.refuses_own_names = True
    call(engine, "collection.edit", collection=collection, name="Mine")
    assert api.own_names == {} and calendars.collections["events"].name == "Calendar"


def test_google_s_name_back_while_google_cannot_be_reached_waits_and_comes_later(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, calendars, api = google_world
    google_addresses(monkeypatch)
    call(engine, "dav.sync", account=account)
    collection = next(item.collection for item in call(engine, "collection.list") if item.account == account and item.kind == "events")
    call(engine, "collection.edit", collection=collection, name="Mine")
    api.busy = True
    assert call(engine, "collection.edit", collection=collection, name="").name == "Mine"  # it waits: the name shown stays until Google answers
    api.busy = False
    calendars.collections["events"].name = "Mine"  # as Google's CalDAV gives one's own name
    call(engine, "dav.sync", account=account)
    assert api.own_names == {}
    calendars.collections["events"].name = "Calendar"
    call(engine, "dav.sync", account=account)
    assert next(item.name for item in call(engine, "collection.list") if item.collection == collection) == "Calendar"


def test_a_calendar_google_does_not_know_in_your_list_is_not_asked_again(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, calendars, api = google_world
    google_addresses(monkeypatch)
    call(engine, "dav.sync", account=account)
    collection = next(item.collection for item in call(engine, "collection.list") if item.account == account and item.kind == "events")
    api.unknown_own_names = True
    call(engine, "collection.edit", collection=collection, name="Mine")
    asked = api.own_name_asks
    call(engine, "dav.sync", account=account)
    assert api.own_name_asks == asked and calendars.collections["events"].name == "Calendar"


def test_google_s_name_back_waiting_is_called_off_by_typing_the_name_again(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, calendars, api = google_world
    google_addresses(monkeypatch)
    call(engine, "dav.sync", account=account)
    collection = next(item.collection for item in call(engine, "collection.list") if item.account == account and item.kind == "events")
    call(engine, "collection.edit", collection=collection, name="Mine")
    calendars.collections["events"].name = "Mine"  # as Google's CalDAV gives one's own name
    api.busy = True
    call(engine, "collection.edit", collection=collection, name="")
    call(engine, "collection.edit", collection=collection, name="Mine")  # changed one's mind before Google was reached
    api.busy = False
    call(engine, "dav.sync", account=account)
    assert api.own_names == {CALENDAR: "Mine"} and next(item.name for item in call(engine, "collection.list") if item.collection == collection) == "Mine"


def test_google_s_name_back_waiting_survives_a_rename_on_the_server_and_a_refusal_keeps_nothing(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, calendars, api = google_world
    google_addresses(monkeypatch)
    call(engine, "dav.sync", account=account)
    collection = next(item.collection for item in call(engine, "collection.list") if item.account == account and item.kind == "events")
    call(engine, "collection.edit", collection=collection, name="Mine")
    calendars.collections["events"].name = "Mine"
    api.busy = True
    call(engine, "collection.edit", collection=collection, name="")
    api.busy = False
    calendars.collections["events"].name = "Mine, renamed on the web"  # changed meanwhile
    call(engine, "dav.sync", account=account)
    assert api.own_names == {} and call(engine, "conflict.list").conflicts == ()  # sent, no conflict to settle
    api.refuses_own_names = True
    call(engine, "collection.edit", collection=collection, name="Again")
    api.refuses_own_names, api.busy = False, True
    call(engine, "collection.edit", collection=collection, name="")
    api.busy, api.refuses_own_names = False, True
    call(engine, "dav.sync", account=account)
    api.refuses_own_names = False
    asked = api.own_name_asks
    call(engine, "dav.sync", account=account)
    assert api.own_name_asks == asked  # refused once: nothing kept, never asked again


def test_google_s_name_back_refused_at_once_keeps_nothing(google_world: tuple[Engine, str, FakeDav, FakeCalendarApi], monkeypatch: pytest.MonkeyPatch) -> None:
    engine, account, calendars, api = google_world
    google_addresses(monkeypatch)
    call(engine, "dav.sync", account=account)
    collection = next(item.collection for item in call(engine, "collection.list") if item.account == account and item.kind == "events")
    call(engine, "collection.edit", collection=collection, name="Mine")
    calendars.collections["events"].name = "Mine"
    api.refuses_own_names = True
    assert call(engine, "collection.edit", collection=collection, name="").name == "Mine"
    api.refuses_own_names = False
    calendars.collections["events"].name = "Mine, renamed on the web"
    asked = api.own_name_asks
    call(engine, "dav.sync", account=account)
    assert api.own_name_asks == asked  # nothing was kept, so nothing is sent again
