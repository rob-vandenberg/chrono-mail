from collections.abc import Generator
from pathlib import Path

import pytest

from chrono_mail.config import load_tags
from chrono_mail.core import ChronoMailError
from chrono_mail.engine import Engine, commands_colourtags
from tests.enginefactory import make_engine
from tests.test_engine_commands import call

# --- Version ------------------------------------------------------------
__version__ = "test_engine_colour_tags.py 4.4.573"

# --- Version history ----------------------------------------------------
# v4.4.573: Initial tests: every appointment colour in use becomes a tag of the same name and colour, given as the colour tag (D-329)


@pytest.fixture
def engine(tmp_path: Path) -> Generator[Engine]:
    made = make_engine(tmp_path)
    call(made, "pim.enable")
    yield made


def calendar(engine: Engine) -> str:
    return next(info.collection for info in call(engine, "collection.list", kind="events"))


def coloured(engine: Engine) -> tuple[str, str]:
    """A calendar with the colours Peacock and Tomato, and an appointment in Peacock that already has the tag Work."""
    cal = calendar(engine)
    call(engine, "collection.set-palette", collection=cal, colours=("g7\tPeacock\t#039be5", "g1\tTomato\t#d50000"))
    event = call(engine, "event.add", calendar=cal, summary="Swimming", start="2026-10-14T18:00", end="2026-10-14T19:00", time_zone="UTC", tag=("Work",), colour="g7")
    return cal, event.item


def test_an_appointment_colour_becomes_its_colour_tag_and_the_tag_has_that_colour(engine: Engine) -> None:
    _, item = coloured(engine)
    plain = call(engine, "event.add", calendar=calendar(engine), summary="Dentist", start="2026-10-15T09:00", end="2026-10-15T10:00", time_zone="UTC")
    result = call(engine, "event.colours-to-tags")
    assert (result.appointments, result.failed) == (1, 0)
    shown = call(engine, "event.show", item=item)
    assert shown.tags == ("Peacock", "Work") and shown.colour_id == ""  # the colour tag first, the colour gone
    assert load_tags(engine.directories.tags_file).colour_of("Peacock") == "#039be5"
    assert call(engine, "event.show", item=plain.item).tags == ()  # an appointment without a colour is left as it is


def test_it_runs_once_per_account(engine: Engine) -> None:
    _, item = coloured(engine)
    call(engine, "event.colours-to-tags")
    call(engine, "event.edit", item=item, colour="g1")  # coloured again by an older version, say: the account is done
    assert call(engine, "event.colours-to-tags").appointments == 0


def test_a_tag_of_the_same_name_with_another_colour_gives_a_numbered_tag(engine: Engine) -> None:
    _, item = coloured(engine)
    call(engine, "tag.create", name="Peacock", colour="#00ff00")
    call(engine, "event.colours-to-tags")
    assert call(engine, "event.show", item=item).tags[0] == "Peacock 2"
    tags = load_tags(engine.directories.tags_file)
    assert (tags.colour_of("Peacock"), tags.colour_of("Peacock 2")) == ("#00ff00", "#039be5")  # the person's own tag keeps its colour


def test_an_appointment_that_cannot_be_changed_now_is_tried_again_at_the_next_start(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _, item = coloured(engine)
    real = commands_colourtags.event_edit

    def refused(engine_: Engine, parameters: commands_colourtags.EditParameters) -> object:
        raise ChronoMailError("The server cannot be reached")

    monkeypatch.setattr(commands_colourtags, "event_edit", refused)
    assert call(engine, "event.colours-to-tags").failed == 1
    assert call(engine, "event.show", item=item).colour_id == "g7"  # its colour still shows meanwhile
    monkeypatch.setattr(commands_colourtags, "event_edit", real)
    assert call(engine, "event.colours-to-tags").appointments == 1  # not marked done: the next start finishes it
    assert call(engine, "event.show", item=item).tags[0] == "Peacock"


def test_the_first_tag_of_an_appointment_stays_first_through_an_edit(engine: Engine) -> None:
    event = call(engine, "event.add", calendar=calendar(engine), summary="Party", start="2026-10-16T20:00", end="2026-10-16T23:00", time_zone="UTC", tag=("Private", "Birthday"))
    call(engine, "event.edit", item=event.item, tag=("Birthday", "Private"))
    assert call(engine, "event.show", item=event.item).tags == ("Birthday", "Private")  # the order says which tag gives the colour (D-329)


def test_a_changed_date_with_a_colour_of_its_own_gets_its_tag_too(engine: Engine) -> None:
    cal = calendar(engine)
    call(engine, "collection.set-palette", collection=cal, colours=("g7\tPeacock\t#039be5", "g1\tTomato\t#d50000"))
    series = call(engine, "event.add", calendar=cal, summary="Training", start="2026-10-12T18:00", end="2026-10-12T19:00", time_zone="UTC", rrule="FREQ=DAILY;COUNT=3")
    date = next(row.occurrence for row in call(engine, "event.list", from_date="2026-10-13", to_date="2026-10-14").events if row.summary == "Training")
    call(engine, "event.edit", item=series.item, occurrence=date, colour="g1")
    assert call(engine, "event.colours-to-tags").appointments == 1
    rows = {row.start[:10]: row for row in call(engine, "event.list", from_date="2026-10-12", to_date="2026-10-15").events}
    assert rows["2026-10-13"].tags == ("Tomato",) and rows["2026-10-13"].colour_id == ""
    assert rows["2026-10-12"].tags == () and rows["2026-10-14"].tags == ()  # the other dates had no colour of their own


def test_a_damaged_tags_file_leaves_the_colours_and_the_account_is_tried_again(engine: Engine) -> None:
    _, item = coloured(engine)
    engine.directories.tags_file.write_text("this is no tags file", encoding="utf-8")
    result = call(engine, "event.colours-to-tags")
    assert (result.appointments, result.failed) == (0, 1)
    assert call(engine, "event.show", item=item).colour_id == "g7"  # its colour still shows
    engine.directories.tags_file.unlink()
    assert call(engine, "event.colours-to-tags").appointments == 1
