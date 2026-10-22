import collections
import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtCore import QModelIndex
from PySide6.QtGui import QFocusEvent, QFontDatabase, QFontInfo, QPalette
from PySide6.QtWidgets import QAbstractItemView, QApplication, QScrollArea
from pytestqt.qtbot import QtBot

from chrono_mail.ui import appearance
from chrono_mail.ui.colours import selection_grey
from chrono_mail.ui.paletteeditor import PaletteEditor
from chrono_mail.ui.sections import CALENDAR, MAIL
from tests.ui.test_ui_calendar import calendar_id, open_calendar
from tests.ui.test_ui_theme import settle
from tests.ui.test_ui_window import started
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_windows_display.py 2.0.297"

# --- Version history ----------------------------------------------------
# v2.0.297: Taken into the project (the Linux computer): the colour helper typed (QAbstractItemView, QModelIndex), QPalette.ColorRole; the calendar checked before use
# v2.0.296: Initial (the Windows computer): what only Windows shows. Every run on Windows: the window tests have real fonts (offscreen
#           Qt has none of its own there, 2.0.293), Event colours fits its rows with the Windows font and keeps a margin to the scrollbar.
#           Only on the real Windows display (QT_QPA_PLATFORM=windows): the narrowest window, the selection colours with the program's look
#           at the display's own scale (125 % on the Windows computer) and the system's light or dark scheme

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="what only Windows shows")
real_display = pytest.mark.skipif(os.environ.get("QT_QPA_PLATFORM") != "windows", reason="the real Windows display only (QT_QPA_PLATFORM=windows)")


@pytest.fixture
def program_look(qapp: QApplication, tmp_path: Path) -> None:
    """What the program does at start before any window is made (ui/app.py: appearance.start); requested before world, so it runs first."""
    appearance.start(qapp, QSettings(str(tmp_path / "look.ini"), QSettings.Format.IniFormat))


def test_the_window_tests_have_real_fonts(qapp: QApplication, world: World) -> None:
    """Offscreen Qt on Windows has no font files of its own: without them every text is measured with a fallback twice as wide (2.0.293)."""
    assert QFontDatabase.families(), "no font families: QT_QPA_FONTDIR is not set for this run"
    assert QFontInfo(qapp.font()).family() == qapp.font().family() != ""  # the family asked for is the family drawn (not a fallback)


def test_event_colours_fit_their_rows_and_keep_a_margin_to_the_scrollbar(world: World, qtbot: QtBot) -> None:
    page = open_calendar(world, qtbot)
    calendar = page.panel.info(calendar_id(world))
    assert calendar is not None
    editor = PaletteEditor(world.backend, calendar)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitExposed(editor)
    scroll = editor.findChild(QScrollArea)
    assert isinstance(scroll, QScrollArea)
    qtbot.waitUntil(lambda: not scroll.horizontalScrollBar().isVisible())  # every row fits, the delete buttons included (F4)
    for _ in range(4):
        editor.add_button.click()
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().isVisible() and not scroll.horizontalScrollBar().isVisible())
    button = editor.rows[0].delete_button
    right = button.mapTo(scroll.viewport(), button.rect().topRight()).x()
    assert scroll.viewport().width() - 1 - right >= 6  # the margin of 2.0.291 between the delete buttons and the scrollbar


@real_display
def test_the_narrowest_window_is_768_pixels_in_every_section(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    window.show()
    qtbot.waitExposed(window)
    for section in (MAIL, CALENDAR, MAIL):
        window.switch_section(section)
        window.resize(100, 700)
        qtbot.waitUntil(lambda: window.width() == window.minimumWidth())
        assert window.width() == 768, section


def most_common_colour(view: QAbstractItemView, index: QModelIndex) -> str:
    """The colour that fills most of a row in a grabbed view, at the display's own scale (a grab is in device pixels)."""
    rect = view.visualRect(index)
    image = view.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    counts = collections.Counter(
        image.pixelColor(x, y).name()
        for x in range(int(rect.left() * ratio), int(rect.right() * ratio))
        for y in range(int((rect.top() + 2) * ratio), int((rect.bottom() - 1) * ratio))
    )
    return counts.most_common(1)[0][0]


@real_display
def test_the_selection_is_blue_with_the_focus_and_the_themes_grey_without(program_look: None, world: World, qtbot: QtBot) -> None:
    """With the program's look as at start, whatever the system's scheme (dark on the Windows computer) and scale (125 % there)."""
    window = world.window
    window.resize(1100, 600)
    window.show()
    window.start()
    settle(qtbot, world, 3)
    view = window.list_view
    view.setCurrentIndex(window.list_model.index(0, window.list_model.columns.index("subject")))
    for focus_in, wanted in ((True, window.palette().color(QPalette.ColorRole.Highlight).name()), (False, selection_grey())):
        QApplication.sendEvent(view, QFocusEvent(QEvent.Type.FocusIn if focus_in else QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason))
        qtbot.waitUntil(lambda focus_in=focus_in: view.property("focused") is focus_in)
        assert most_common_colour(view, view.currentIndex()) == wanted, ("focus" if focus_in else "no focus", QApplication.styleHints().colorScheme())

