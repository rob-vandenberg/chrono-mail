from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.ui import calendarlayout as layout
from chrono_mail.ui.calendarview import CalendarPage
from chrono_mail.ui.calendarwidgets import DOT_CENTRE
from chrono_mail.ui.paletteeditor import PaletteEditor
from chrono_mail.ui.tagchips import TagColourBook
from tests.fakedav import FakeDav
from tests.ui.test_ui_calendar import calendar_id, open_calendar
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_timed_background.py 4.2.438"

# --- Version history ----------------------------------------------------
# v4.2.438: Initial tests: Month shows timed events in their colour when the account says so (UR-02, D-297); the default name of a
#           calendar's own colour is never kept as chosen

TICK_TEXT = "Show background colour for timed events in Month view"


def summary_of(world: World, collection: str) -> CollectionSummary:
    found = next(info for info in world.call("collection.list", kind="events") if info.collection == collection)
    assert isinstance(found, CollectionSummary)
    return found


def info_of(page: CalendarPage, collection: str) -> CollectionSummary:
    found = page.panel.info(collection)
    assert isinstance(found, CollectionSummary)
    return found


def month_picture(page: CalendarPage) -> QImage:
    page.month.resize(900, 600)
    return page.month.grab().toImage()


def drawn(page: CalendarPage, summary: str) -> tuple[QPointF, QPointF]:
    """Where an event's line was drawn in Month: a point in its middle right (its background) and the centre of its dot."""
    rect = next(rect for rect, entry in page.month.hits if entry.summary == summary)
    return QPointF(rect.right() - 4, rect.center().y()), QPointF(rect.left() + DOT_CENTRE, rect.center().y())


def colour_at(picture: QImage, point: QPointF) -> QColor:
    return picture.pixelColor(QPoint(int(point.x()), int(point.y())))


def close(first: QColor, second: QColor) -> bool:
    return max(abs(first.red() - second.red()), abs(first.green() - second.green()), abs(first.blue() - second.blue())) <= 24


def test_the_tick_is_in_event_colours_off_by_default_and_its_text_works_it(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    editor = PaletteEditor(world.backend, info_of(page, calendar_id(world)))
    qtbot.addWidget(editor)
    assert editor.rows[0].name.text() == "Default calendar colour"
    assert not editor.timed_background.isChecked() and editor.timed_background.text() == ""
    text = editor.findChild(QLabel, "timedBackgroundText")
    assert isinstance(text, QLabel) and text.text() == TICK_TEXT and text.wordWrap()  # it wraps when the window is narrow
    editor.show()
    QTest.mouseClick(text, Qt.MouseButton.LeftButton)
    assert editor.timed_background.isChecked()
    QTest.mouseClick(text, Qt.MouseButton.LeftButton)
    assert not editor.timed_background.isChecked()


def test_save_keeps_the_tick_with_the_account_and_month_fills_its_timed_events(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    cal = calendar_id(world)
    world.call("collection.edit", collection=cal, colour="#123456")  # dark: the dot is white
    wait(qtbot, lambda: any(entry.summary == "Lunch" and entry.colour == "#123456" for entry in page.entries))
    picture = month_picture(page)
    middle, dot = drawn(page, "Lunch")
    assert not close(colour_at(picture, middle), QColor("#123456"))  # off: a dot before the text, no background
    editor = PaletteEditor(world.backend, info_of(page, cal))
    qtbot.addWidget(editor)
    editor.timed_background.setChecked(True)
    editor._save()  # noqa: SLF001
    wait(qtbot, lambda: editor.result_summary is not None)
    assert summary_of(world, cal).timed_background
    wait(qtbot, lambda: all(entry.filled for entry in page.entries))
    picture = month_picture(page)
    middle, dot = drawn(page, "Lunch")
    assert close(colour_at(picture, middle), QColor("#123456"))
    assert close(colour_at(picture, dot), TagColourBook.text_colour(QColor("#123456")))
    world.call("collection.edit", collection=cal, colour="#ffff99")  # light: the dot is dark
    wait(qtbot, lambda: any(entry.summary == "Lunch" and entry.colour == "#ffff99" for entry in page.entries))
    picture = month_picture(page)
    middle, dot = drawn(page, "Lunch")
    assert close(colour_at(picture, middle), QColor("#ffff99"))
    assert close(colour_at(picture, dot), TagColourBook.text_colour(QColor("#ffff99")))
    assert TagColourBook.text_colour(QColor("#123456")) != TagColourBook.text_colour(QColor("#ffff99"))


def test_saving_colours_without_the_tick_keeps_it_as_it_is(world: World, qtbot: QtBot) -> None:
    world.call("pim.enable")
    cal = calendar_id(world)
    world.call("collection.set-palette", collection=cal, timed_background=True)
    world.call("collection.set-palette", collection=cal, colours=("g7\tPeacock\t#00ffff",))  # the command line, without the tick
    assert summary_of(world, cal).timed_background
    world.call("collection.set-palette", collection=cal, timed_background=False)
    assert not summary_of(world, cal).timed_background


def test_two_accounts_keep_their_own_choice(world: World, qtbot: QtBot) -> None:
    mail_account = next(account.account_id for account in world.call("account.list") if account.account_id != LOCAL_ACCOUNT_ID)
    with FakeDav() as server:
        world.call("account.set-dav", account=mail_account, url=server.url, username="ann", password="secret")
        world.call("dav.sync", account=mail_account)
        other = next(info.collection for info in world.call("collection.list", kind="events") if info.account == mail_account)
        page = open_calendar(world, qtbot, layout.MONTH)
        assert info_of(page, calendar_id(world)).account == LOCAL_ACCOUNT_ID
        wait(qtbot, lambda: page.panel.info(other) is not None)
        world.call("event.add", calendar=other, summary="Review", start="2026-10-21T10:00", end="2026-10-21T11:00", time_zone="UTC")
        wait(qtbot, lambda: any(entry.summary == "Review" for entry in page.entries))
        world.call("collection.set-palette", collection=calendar_id(world), timed_background=True)
        wait(qtbot, lambda: any(entry.summary == "Lunch" and entry.filled for entry in page.entries))
        assert not next(entry for entry in page.entries if entry.summary == "Review").filled
        assert not summary_of(world, other).timed_background
        world.call("collection.set-palette", collection=other, timed_background=True)
        wait(qtbot, lambda: next(entry for entry in page.entries if entry.summary == "Review").filled)


def test_a_read_only_calendar_greys_the_tick(world: World, qtbot: QtBot) -> None:
    from chrono_mail.engine.pimaccess import open_pim

    page = open_calendar(world, qtbot, layout.MONTH)
    cal = calendar_id(world)
    with open_pim(world.engine, LOCAL_ACCOUNT_ID) as store:
        store.update_collection(cal, read_only=True)
    wait(qtbot, lambda: info_of(page, cal).read_only)
    editor = PaletteEditor(world.backend, info_of(page, cal))
    qtbot.addWidget(editor)
    assert not editor.timed_background.isEnabled()
    editor.show()
    text = editor.findChild(QLabel, "timedBackgroundText")
    assert isinstance(text, QLabel)
    QTest.mouseClick(text, Qt.MouseButton.LeftButton)
    assert not editor.timed_background.isChecked()


def test_the_default_name_is_never_kept_as_a_name_of_its_own(world: World, qtbot: QtBot) -> None:
    """A Save of the shown default kept its name as chosen, so a renamed default never reached that account (the review of 4.2.438)."""
    from dataclasses import replace

    world.call("pim.enable")
    cal = calendar_id(world)
    for shown in ("Default calendar colour", "Calendar colour"):
        world.call("collection.set-palette", collection=cal, own_name=shown)
        assert world.engine.config().find(LOCAL_ACCOUNT_ID).calendar_own_colour_name == ""
        assert summary_of(world, cal).palette[0].name == "Default calendar colour"
    world.engine.change_config(lambda config: config.with_account(replace(config.find(LOCAL_ACCOUNT_ID), calendar_own_colour_name="Calendar colour")))
    assert summary_of(world, cal).palette[0].name == "Default calendar colour"  # kept by an older version: the default, under its new name
    world.call("collection.set-palette", collection=cal, own_name="Family")
    assert summary_of(world, cal).palette[0].name == "Family"


def test_letting_go_beside_the_text_leaves_the_tick(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    editor = PaletteEditor(world.backend, info_of(page, calendar_id(world)))
    qtbot.addWidget(editor)
    editor.show()
    text = editor.findChild(QLabel, "timedBackgroundText")
    assert isinstance(text, QLabel)
    QTest.mousePress(text, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    QTest.mouseRelease(text, Qt.MouseButton.LeftButton, pos=QPoint(text.width() + 40, 2))  # dragged off the text
    assert not editor.timed_background.isChecked()
