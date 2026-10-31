from pytestqt.qtbot import QtBot

from chrono_mail.ui.paletteeditor import SCREEN_WIDTH, PaletteEditor
from tests.ui.test_ui_timed_background import calendar_id, info_of, layout, open_calendar
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_palette_width.py 4.2.442"

# --- Version history ----------------------------------------------------
# v4.2.442: Initial version: Event colours opens at, and never goes below, 500 pixels on the screen (the owner)


def test_event_colours_opens_at_its_smallest_width_of_500_screen_pixels(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    editor = PaletteEditor(world.backend, info_of(page, calendar_id(world)))
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitExposed(editor)
    on_screen = round(editor.width() * editor.devicePixelRatio())
    assert SCREEN_WIDTH == 500 and on_screen == 500 and editor.minimumWidth() == editor.width()
