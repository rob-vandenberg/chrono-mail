import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.config import AccountSettings
from chrono_mail.core.colours import GOOGLE_EVENT_COLOURS
from chrono_mail.engine import Engine
from chrono_mail.engine import commands_calendar
from chrono_mail.engine.google_colours import GoogleShown
from tests.ui.test_ui_calendar import open_calendar
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_google_colours.py 4.4.599"

# --- Version history ----------------------------------------------------
# v4.4.599: A Google colour the person named shows as a tag on the appointment, in the tag's colour (D-338)
# v4.4.598: Initial test: an appointment without tags shows Google's colour, one with a tag the colour of its tag (D-337, D-329)


class EveryEvent(dict[str, str]):
    """Google gives every event of a calendar the same colour."""

    def __init__(self, colour: str) -> None:
        super().__init__()
        self._colour = colour

    def get(self, key: str, default: str = "") -> str:  # type: ignore[override]
        return self._colour


class EveryCalendar(dict[str, dict[str, str]]):
    def __init__(self, colour: str) -> None:
        super().__init__()
        self._colour = colour

    def get(self, key: str, default: dict[str, str] | None = None) -> dict[str, str]:  # type: ignore[override]
        return EveryEvent(self._colour)


def test_an_appointment_without_tags_shows_googles_colour_and_one_with_a_tag_its_tags_colour(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    tomato = GOOGLE_EVENT_COLOURS["11"]
    def shown(engine: Engine, settings: AccountSettings) -> GoogleShown:
        return GoogleShown(EveryCalendar(tomato), EveryCalendar(""))

    monkeypatch.setattr(commands_calendar, "shown_colours", shown)
    page = open_calendar(world, qtbot)
    lunch = next(entry for entry in page.entries if entry.summary == "Lunch")
    dentist = next(entry for entry in page.entries if entry.summary == "Dentist")
    assert dentist.row.tags == () and dentist.row.other_colour == tomato and dentist.colour == tomato
    assert lunch.row.tags and lunch.colour == page.colours.colour(lunch.row.tags[0]).name() != tomato  # the colour tag wins (D-329)


def test_a_google_colour_the_person_named_shows_as_a_tag_in_its_own_colour(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-338: the name of Google's colour is a tag on the appointment, and the appointment is drawn in that tag's colour."""
    red = "#ff0000"
    world.call("tag.create", name="Rood", colour=red)

    def shown(engine: Engine, settings: AccountSettings) -> GoogleShown:
        return GoogleShown(EveryCalendar(red), EveryCalendar("Rood"))

    monkeypatch.setattr(commands_calendar, "shown_colours", shown)
    page = open_calendar(world, qtbot)
    page.reload_tags()
    wait(qtbot, lambda: any(entry.summary == "Dentist" and entry.colour == red for entry in page.entries))
    dentist = next(entry for entry in page.entries if entry.summary == "Dentist")
    assert dentist.row.tags == ("Rood",) and page.colours.colour("Rood").name() == red
