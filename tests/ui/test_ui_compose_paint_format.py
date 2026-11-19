from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from chrono_mail.ui.composer import ComposeWindow
from tests.ui.test_ui_signatures import close, composer
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_compose_paint_format.py 4.6.641"

# --- Version history ----------------------------------------------------
# v4.6.641: Initial: Paint Format gives the format of the selected text to the next text selected, then is off (D-363)

SOURCE = '<p><span style="font-weight:700; color:#ff0000; font-size:14pt;">Offer</span> plain <a href="https://example.org">link</a> <span style="color:#0000ff;">blue</span></p>'


def rich_window(world: World, qtbot: QtBot) -> ComposeWindow:
    window = composer(world, qtbot)
    window.rich_action.trigger()
    window.rich.set_html(SOURCE)
    return window


def select(window: ComposeWindow, word: str) -> None:
    text = window.rich.edit.toPlainText()
    cursor = window.rich.edit.textCursor()
    cursor.setPosition(text.index(word))
    cursor.setPosition(text.index(word) + len(word), QTextCursor.MoveMode.KeepAnchor)
    window.rich.edit.setTextCursor(cursor)


def format_of(window: ComposeWindow, word: str) -> QTextCharFormat:
    cursor = QTextCursor(window.rich.edit.document())
    cursor.setPosition(window.rich.edit.toPlainText().index(word) + 1)
    return cursor.charFormat()


def release_mouse(window: ComposeWindow) -> None:
    """The mouse let go after selecting, as at the end of a drag over the text."""
    viewport = window.rich.edit.viewport()
    assert viewport is not None
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(1, 1))


def test_the_format_of_the_selection_goes_to_the_next_text_selected_then_it_is_off(world: World, qtbot: QtBot) -> None:
    window = rich_window(world, qtbot)
    select(window, "Offer")
    window.rich.paint_format.trigger()
    assert window.rich.paint_format.isChecked()
    select(window, "plain")
    release_mouse(window)
    painted = format_of(window, "plain")
    assert painted.fontWeight() == 700 and painted.foreground().color().name() == "#ff0000" and painted.fontPointSize() == 14
    assert not window.rich.paint_format.isChecked()
    select(window, "blue")  # Paint Format is off: the next selection keeps its own format
    release_mouse(window)
    assert format_of(window, "blue").foreground().color().name() == "#0000ff"
    close(window)


def test_a_link_stays_a_link_and_a_colour_the_copied_text_lacks_goes(world: World, qtbot: QtBot) -> None:
    window = rich_window(world, qtbot)
    select(window, "plain")  # no colour, no bold
    window.rich.paint_format.trigger()
    select(window, "link blue")
    window.rich.paint_on_selection()
    link = format_of(window, "link")
    assert link.isAnchor() and link.anchorHref() == "https://example.org" and link.fontUnderline()  # a link keeps its look
    assert not format_of(window, "blue").hasProperty(QTextCharFormat.Property.ForegroundBrush)
    close(window)


def test_pressed_again_it_gives_nothing_and_one_undo_takes_it_back(world: World, qtbot: QtBot) -> None:
    window = rich_window(world, qtbot)
    select(window, "Offer")
    window.rich.paint_format.trigger()
    window.rich.paint_format.trigger()  # pressed again: off
    select(window, "plain")
    window.rich.paint_on_selection()
    assert format_of(window, "plain").fontWeight() != 700
    select(window, "Offer")
    window.rich.paint_format.trigger()
    select(window, "plain link blue")
    window.rich.paint_on_selection()
    assert format_of(window, "blue").foreground().color().name() == "#ff0000"
    window.rich.undo.trigger()
    assert format_of(window, "blue").foreground().color().name() == "#0000ff" and format_of(window, "plain").fontWeight() != 700
    close(window)


def test_a_right_click_on_a_selection_never_paints(world: World, qtbot: QtBot) -> None:
    window = rich_window(world, qtbot)
    select(window, "Offer")
    window.rich.paint_format.trigger()
    select(window, "plain")
    viewport = window.rich.edit.viewport()
    assert viewport is not None
    QTest.mouseRelease(viewport, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(1, 1))
    assert format_of(window, "plain").fontWeight() != 700 and window.rich.paint_format.isChecked()
    window.rich.paint_format.trigger()
    close(window)


def test_in_plain_text_paint_format_is_greyed(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    assert not window.rich.paint_format.isEnabled()
    window.rich_action.trigger()
    assert window.rich.paint_format.isEnabled()
    close(window)
