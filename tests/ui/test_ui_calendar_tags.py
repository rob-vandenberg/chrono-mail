import dataclasses
from datetime import date

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit
from pytestqt.qtbot import QtBot

from chrono_mail.config import AccountSettings
from chrono_mail.core import CollectionKind
from chrono_mail.core.tagnames import BAR_REFUSED
from chrono_mail.engine import Engine, commands_calendar, commands_pim
from chrono_mail.engine.commands_calendar import EventInfo
from chrono_mail.engine.google_colours import CalendarTag, GoogleShown
from chrono_mail.pimstore import ItemQuery, PimStore
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.calendarwidgets import event_tip
from chrono_mail.ui.eventeditor import EventEditor
from chrono_mail.ui.tagbox import TagPill
from chrono_mail.ui.tagname import NoBar, keep_bar_out
from chrono_mail.ui.tagsdialog import rows_from
from tests.ui.test_ui_calendar import open_calendar
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_calendar_tags.py 4.4.602"

# --- Version history ----------------------------------------------------
# v4.4.602: Initial tests: a Google calendar's tag is shown by its name and the calendar's, never by its identifier; never in Settings > Tags;
#           no vertical bar in a typed name (D-341)

RED, BLUE = "#ff0000", "#0000ff"


def ours(collection: str, name: str = "Rood") -> str:
    """The identifier the stand-in gives a tag of this calendar: its name, a bar and a made-up Google id."""
    return f"{name}|cal-{collection}@group.calendar.google.com"


class EveryEvent(dict[str, str]):
    def __init__(self, value: str) -> None:
        super().__init__()
        self._value = value

    def get(self, key: str, default: str = "") -> str:  # type: ignore[override]
        return self._value


class PerCalendar(dict[str, dict[str, str]]):
    """Every event of a calendar carries that calendar's tag Rood (names) or its colour (colours)."""

    def __init__(self, names: bool) -> None:
        super().__init__()
        self._names = names

    def get(self, key: str, default: dict[str, str] | None = None) -> dict[str, str]:  # type: ignore[override]
        return EveryEvent(ours(key) if self._names else RED)


@pytest.fixture
def google_page(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> CalendarPage:
    """The calendar, its calendars standing in for Google calendars: each has the tags Rood and Blauw, and every appointment carries Rood."""

    def tags(engine: Engine, settings: AccountSettings, store: PimStore) -> list[CalendarTag]:
        for info in store.collections(CollectionKind.EVENTS):  # as the synchronisation keeps Google's tag of each event in the index
            store.index.keep_other_tags(info.collection_id, {item.uid: ours(info.collection_id) for item in store.index.query(ItemQuery(CollectionKind.EVENTS, (info.collection_id,), limit=100_000))})
        return [CalendarTag(ours(info.collection_id, name), name, colour, f"{name} ({info.name})", info.collection_id)
                for info in store.collections(CollectionKind.EVENTS) for name, colour in (("Rood", RED), ("Blauw", BLUE))]

    def shown(engine: Engine, settings: AccountSettings) -> GoogleShown:
        return GoogleShown(PerCalendar(False), PerCalendar(True))

    monkeypatch.setattr(commands_pim, "calendar_tags", tags)
    monkeypatch.setattr(commands_calendar, "shown_colours", shown)
    page = open_calendar(world, qtbot)
    page.reload_tags()
    wait(qtbot, lambda: page.tag_filter.list.count() > 0 and any(entry.summary == "Dentist" and entry.tag_colours for entry in page.entries))
    return page


def dentist_calendar(page: CalendarPage) -> tuple[str, str]:
    """(the collection of Dentist, its calendar's name)."""
    entry = next(entry for entry in page.entries if entry.summary == "Dentist")
    info = page.panel.info(entry.row.collection)
    assert info is not None
    return entry.row.collection, info.name


def test_the_tags_block_and_the_appointments_show_a_calendars_tag_by_its_name_and_the_calendars(google_page: CalendarPage, qtbot: QtBot) -> None:
    page = google_page
    collection, name = dentist_calendar(page)
    lines = [page.tag_filter.list.item(row).text() for row in range(page.tag_filter.list.count())]
    assert f"Rood ({name})" in lines and not any("|" in line for line in lines)  # never the identifier (D-341)
    page.tag_filter.choose(ours(collection))
    wait(qtbot, lambda: page.tag_filter.title.text() == f"Tags: Rood ({name})")
    entry = next(entry for entry in page.entries if entry.summary == "Dentist")
    assert entry.tag_colours == ((f"Rood ({name})", RED),) and entry.colour == RED
    assert f"Rood ({name})" in event_tip(entry) and "|" not in event_tip(entry)


def test_the_appointment_window_shows_and_offers_the_calendars_tags_by_name_one_at_a_time(world: World, google_page: CalendarPage, qtbot: QtBot) -> None:
    page = google_page
    collection, name = dentist_calendar(page)
    item = next(entry.row.item for entry in page.entries if entry.summary == "Dentist")
    event = world.call("event.show", item=item)
    editor = EventEditor(world.backend, page._calendars(), page._tags_known, event, event.collection)  # noqa: SLF001
    qtbot.addWidget(editor)
    pills = [pill.text() for pill in editor.tag_box.findChildren(TagPill)]
    assert pills == [f"Rood ({name})"]
    editor._fill_tag_menu()  # noqa: SLF001
    offered = [action.text() for action in editor.tag_menu.actions() if action.text()]
    assert f"Blauw ({name})" in offered and not any("|" in text for text in offered)
    assert [text for text in offered if text.startswith(("Rood (", "Blauw ("))] == [f"Blauw ({name})"]  # only its own calendar's, Rood it has already
    editor.choose_tag(ours(collection, "Blauw"), True)
    assert editor.tag_box.tags() == [ours(collection, "Blauw")]  # a Google calendar gives one colour: the other one goes, the new one colours it


def test_the_tag_overview_is_titled_with_the_calendars_name(world: World, google_page: CalendarPage, qtbot: QtBot) -> None:
    collection, name = dentist_calendar(google_page)
    world.window.show_tag_overview(ours(collection))
    window = world.window.tag_overviews[ours(collection).casefold()]
    wait(qtbot, lambda: window.windowTitle() == f"Everything tagged Rood ({name})")


def test_settings_tags_never_lists_a_calendars_tag(world: World, google_page: CalendarPage) -> None:
    listed = world.call("tag.list")
    assert any(tag.collection for tag in listed)
    assert all("|" not in row.name for row in rows_from(listed))


def test_a_bar_typed_in_a_tags_name_is_refused_with_a_calm_line_and_a_pasted_one_left_out(qtbot: QtBot) -> None:
    typed = QLineEdit()
    qtbot.addWidget(typed)
    keep_bar_out(typed)
    QTest.keyClicks(typed, "A|B")
    watch = typed.findChild(NoBar)
    assert typed.text() == "AB" and watch is not None and watch.told == BAR_REFUSED
    pasted = QLineEdit()
    qtbot.addWidget(pasted)
    keep_bar_out(pasted)
    clipboard = QApplication.clipboard()
    before = clipboard.text()
    clipboard.setText("Red|Blue")
    try:
        pasted.paste()
    finally:
        clipboard.setText(before)
    watch = pasted.findChild(NoBar)
    assert pasted.text() == "RedBlue" and watch is not None and watch.told == ""


def test_one_date_of_a_series_opens_with_its_own_google_colour(google_page: CalendarPage, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """A date Google colours on its own opens with that colour's tag, never the series': a title changed there leaves its colour alone (D-341)."""
    page = google_page
    thursday = next(e for e in page.entries if e.summary == "Standup" and e.first_day == date(2026, 10, 15))
    own = ours(thursday.row.collection, "Blauw")
    page.selected = dataclasses.replace(thursday, row=dataclasses.replace(thursday.row, tags=(own,)))  # as event.list gives a date with a colour of its own
    opened: list[EventInfo] = []

    def scope(verb: str) -> str:
        return "occurrence"

    def editor(event: EventInfo, *rest: object, **keywords: object) -> None:
        opened.append(event)

    monkeypatch.setattr(page, "_ask_scope", scope)
    monkeypatch.setattr(page, "_editor", editor)
    page.edit_selected()
    wait(qtbot, lambda: bool(opened))
    assert opened[0].tags == (own,) and opened[0].google_tag == own
