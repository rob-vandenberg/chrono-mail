from pytestqt.qtbot import QtBot

from chrono_mail.ui import forms
from chrono_mail.ui.paletteeditor import WIDTH, PaletteEditor
from chrono_mail.ui.scale import wpx
from tests.ui.test_ui_timed_background import calendar_id, info_of, layout, open_calendar
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_palette_width.py 4.2.445"

# --- Version history ----------------------------------------------------
# v4.2.445: the 500 pixels are the owner's screen's (125 %, slider 0.95), in Qt's units at the size slider
# v4.2.442: Initial version: Event colours opens at, and never goes below, 500 pixels on the screen (the owner)


def test_a_width_the_owner_gives_is_exactly_that_on_his_screen() -> None:
    assert forms.owner_width(500) == 421 and forms.owner_width(700) == 589
    for pixels in (500, 700):  # Qt's units at his slider (rounded as wpx does), then his screen's scale
        assert round(round(forms.owner_width(pixels) * forms.OWNER_WINDOWS_FACTOR) * forms.OWNER_SCREEN_SCALE) == pixels


def test_event_colours_opens_at_its_smallest_width(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot, layout.MONTH)
    editor = PaletteEditor(world.backend, info_of(page, calendar_id(world)))
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitExposed(editor)
    assert WIDTH == forms.owner_width(500) and editor.width() == editor.minimumWidth() == max(wpx(WIDTH), editor.minimumSizeHint().width())
