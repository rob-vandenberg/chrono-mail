from dataclasses import replace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from chrono_mail.ui import forms
from chrono_mail.ui.paletteeditor import OPENING_WIDTH, WIDTH, PaletteEditor
from chrono_mail.ui.scale import wpx
from tests.ui.test_ui_timed_background import calendar_id, info_of, layout, open_calendar
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_palette_width.py 4.3.503"

# --- Version history ----------------------------------------------------
# v4.3.503: Event colours opens at 550 pixels on the owner's screen, never below 500 (the owner)
# v4.3.502: a long calendar name never makes Event colours wider; the Calendar field is read-only text in the normal colour with its whole
#           name as hover text; the window opens at the width last given it, and as the first time when what was kept is no plain number (the owner)
# v4.2.445: the 500 pixels are the owner's screen's (125 %, slider 0.95), in Qt's units at the size slider
# v4.2.442: Initial version: Event colours opens at, and never goes below, 500 pixels on the screen (the owner)


def test_a_width_the_owner_gives_is_exactly_that_on_his_screen() -> None:
    assert forms.owner_width(500) == 421 and forms.owner_width(700) == 589
    for pixels in (500, 700):  # Qt's units at his slider (rounded as wpx does), then his screen's scale
        assert round(round(forms.owner_width(pixels) * forms.OWNER_WINDOWS_FACTOR) * forms.OWNER_SCREEN_SCALE) == pixels


def test_event_colours_opens_at_550_and_never_goes_below_500(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    editor = PaletteEditor(world.backend, info_of(page, calendar_id(world)))
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitExposed(editor)
    assert WIDTH == forms.owner_width(500) and editor.minimumWidth() == max(wpx(WIDTH), editor.minimumSizeHint().width())
    assert OPENING_WIDTH == forms.owner_width(550) and editor.width() == wpx(OPENING_WIDTH)


LONG_NAME: str = "a calendar with a name far longer than any window of 500 pixels can show on one line"


def test_a_long_calendar_name_never_keeps_event_colours_from_its_500_pixels(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    info = replace(info_of(page, calendar_id(world)), account_name="someone@example.com - mail.example.com", name=LONG_NAME)
    editor = PaletteEditor(world.backend, info)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitExposed(editor)
    assert editor.minimumWidth() == wpx(WIDTH)  # a drop-down asked for its whole text's width, so the window could not be made narrower (the owner)


def test_event_colours_shows_its_calendar_as_read_only_text_in_the_normal_colour_with_the_whole_name_as_hover_text(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    info = replace(info_of(page, calendar_id(world)), name=LONG_NAME)
    editor = PaletteEditor(world.backend, info)
    qtbot.addWidget(editor)
    field = editor.calendar
    assert isinstance(field, QLineEdit) and field.isReadOnly() and field.isEnabled()  # shown, never chosen here, and never greyed (the owner)
    assert field.text() == field.toolTip() and field.text().endswith(LONG_NAME) and field.cursorPosition() == 0


def test_event_colours_opens_at_the_width_last_given_it(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    info = info_of(page, calendar_id(world))
    settings = QSettings(str(world.settings_path), QSettings.Format.IniFormat)
    first = PaletteEditor(world.backend, info, None, settings)
    qtbot.addWidget(first)
    first.show()
    qtbot.waitExposed(first)
    given = first.width() + wpx(150)
    first.resize(given, first.height())
    qtbot.waitUntil(lambda: first.width() == given)
    first.reject()
    second = PaletteEditor(world.backend, info, None, settings)
    qtbot.addWidget(second)
    second.show()
    qtbot.waitExposed(second)
    assert second.width() == given


def test_event_colours_opens_at_550_when_the_kept_width_is_no_plain_number(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    settings = QSettings(str(world.settings_path), QSettings.Format.IniFormat)
    settings.setValue("event_colours_width", "\u00b2")  # a digit int() refuses: the window opened not at all
    editor = PaletteEditor(world.backend, info_of(page, calendar_id(world)), None, settings)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitExposed(editor)
    assert editor.width() == wpx(OPENING_WIDTH) and editor.minimumWidth() == wpx(WIDTH)
