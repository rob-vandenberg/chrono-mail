import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QColorDialog, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.core.colours import TEXT_COLOUR_ROWS
from chrono_mail.ui.composer import ComposeWindow
from chrono_mail.ui.fontstyle import TICK, size_of
from tests.ui.test_ui_signatures import close, composer
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_compose_font_style.py 4.6.640"

# --- Version history ----------------------------------------------------
# v4.6.640: Initial: Font Style, the A of the bar: font, size, Text Color and Background Color of the selected text (D-363)


def rich_with_selection(world: World, qtbot: QtBot, text: str = "Offer today") -> ComposeWindow:
    window = composer(world, qtbot)
    window.rich_action.trigger()
    window.rich.edit.setPlainText(text)
    cursor = window.rich.edit.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(len("Offer"), QTextCursor.MoveMode.KeepAnchor)
    window.rich.edit.setTextCursor(cursor)
    return window


def format_at(window: ComposeWindow, position: int) -> QTextCharFormat:
    cursor = QTextCursor(window.rich.edit.document())
    cursor.setPosition(position + 1)
    return cursor.charFormat()


def test_the_menu_holds_the_font_the_size_and_two_grids_with_custom_and_no_color(world: World, qtbot: QtBot) -> None:
    window = rich_with_selection(world, qtbot)
    style = window.rich.font_style
    assert style.text() == "A" and style.toolTip() == "Font Style" and style.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    every = [colour for row in TEXT_COLOUR_ROWS for colour in row]
    assert len(every) == 56 and list(style.text_colour.swatches) == every and list(style.background.swatches) == every
    assert style.text_colour.none is None and style.background.none is not None
    assert style.size_box.isEditable() and style.size_box.itemText(0) == "8"
    close(window)


def test_a_colour_of_the_grid_colours_the_selection_and_is_sent_so(world: World, qtbot: QtBot) -> None:
    window = rich_with_selection(world, qtbot)
    window.rich.font_style.text_colour.swatches["#ff0000"].click()
    assert format_at(window, 0).foreground().color().name() == "#ff0000"
    assert not format_at(window, len("Offer ")).hasProperty(QTextCharFormat.Property.ForegroundBrush)  # only the selection
    assert "color:#ff0000" in window.rich.outgoing_html() and window.rich.has_formatting()
    window.rich.font_style.show_current()
    assert window.rich.font_style.text_colour.swatches["#ff0000"].text() == TICK
    close(window)


def test_a_background_colour_and_no_color(world: World, qtbot: QtBot) -> None:
    window = rich_with_selection(world, qtbot)
    style = window.rich.font_style
    style.background.swatches["#ffff00"].click()
    assert format_at(window, 0).background().color().name() == "#ffff00"
    assert "background-color:#ffff00" in window.rich.outgoing_html()
    style.show_current()
    assert style.background.swatches["#ffff00"].text() == TICK and style.background.none is not None and TICK not in style.background.none.text()
    style.background.none.click()
    assert "background-color:#ffff00" not in window.rich.outgoing_html()
    window.rich.set_html('<p><span style="background-color:#00ff00;">Pasted</span> rest</p>')  # a background the text came with
    cursor = window.rich.edit.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(len("Pasted"), QTextCursor.MoveMode.KeepAnchor)
    window.rich.edit.setTextCursor(cursor)
    style.background.none.click()
    assert "background-color:#00ff00" not in window.rich.outgoing_html()
    style.show_current()
    assert TICK in style.background.none.text()
    close(window)


def test_custom_color_takes_the_colour_picked_and_cancel_changes_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = rich_with_selection(world, qtbot)
    answers = [QColor("#123456"), QColor()]  # a colour picked, then Cancel (an invalid colour)
    def picked(*args: object) -> QColor:
        return answers.pop(0)

    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(picked))
    window.rich.font_style.text_colour.custom.click()
    assert format_at(window, 0).foreground().color().name() == "#123456"
    window.rich.font_style.show_current()
    assert window.rich.font_style.text_colour.custom.text().startswith(TICK)  # not in the grid: Custom color… is ticked
    window.rich.font_style.text_colour.custom.click()
    assert format_at(window, 0).foreground().color().name() == "#123456" and answers == []
    close(window)


def test_the_size_and_the_font_change_the_selection_and_a_wrong_size_is_ignored(world: World, qtbot: QtBot) -> None:
    window = rich_with_selection(world, qtbot)
    style = window.rich.font_style
    line = style.size_box.lineEdit()
    assert line is not None
    line.setText("13")  # typed, not one of the list
    QTest.keyClick(line, Qt.Key.Key_Return)
    assert format_at(window, 0).fontPointSize() == 13
    style.size_box.setEditText("14")
    style._size_chosen(0)  # noqa: SLF001 - what picking a size from the list does
    assert format_at(window, 0).fontPointSize() == 14
    for wrong in ("abc", "0", "-3", "100000", ""):
        line.setText(wrong)
        QTest.keyClick(line, Qt.Key.Key_Return)
        assert format_at(window, 0).fontPointSize() == 14
    assert size_of(" 12 ") == 12 and size_of("401") is None
    assert style.size_box.count() == 16  # a size typed is never added to the list
    family = style.family_box.itemText(0)
    style.family_box.setCurrentIndex(0)
    style._family_chosen(0)  # noqa: SLF001 - what choosing a font does
    assert format_at(window, 0).fontFamilies() == [family]
    assert "font-size:14pt" in window.rich.outgoing_html()
    close(window)


def test_with_nothing_selected_it_acts_on_what_is_typed_next(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    window.rich_action.trigger()
    window.rich.font_style.text_colour.swatches["#0000ff"].click()
    window.rich.edit.textCursor().insertText("Blue")
    assert format_at(window, 0).foreground().color().name() == "#0000ff"
    close(window)


def test_in_plain_text_font_style_is_greyed(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    assert not window.is_rich() and not window.rich.font_style.isEnabled() and window.rich.font_style.isVisible()
    window.rich_action.trigger()
    assert window.rich.font_style.isEnabled()
    close(window)
