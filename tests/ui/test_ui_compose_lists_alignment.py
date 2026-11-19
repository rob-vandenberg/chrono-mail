from PySide6.QtCore import Qt
from PySide6.QtGui import QTextListFormat
from PySide6.QtWidgets import QMenu, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.ui.composer import ComposeWindow
from tests.ui.test_ui_signatures import close, composer
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_compose_lists_alignment.py 4.6.642"

# --- Version history ----------------------------------------------------
# v4.6.642: Initial: the list buttons' styles and Alignment (D-363)


def rich_window(world: World, qtbot: QtBot) -> ComposeWindow:
    window = composer(world, qtbot)
    window.rich_action.trigger()
    window.rich.edit.setPlainText("First line")
    return window


def names(window: ComposeWindow, button: str) -> list[str]:
    action = {"bullets": window.rich.bullets, "numbers": window.rich.numbers, "align": window.rich.align}[button]
    menu = action.menu()
    assert isinstance(menu, QMenu)
    return [choice.text() for choice in menu.actions()]


def choose(window: ComposeWindow, button: str, name: str) -> None:
    action = {"bullets": window.rich.bullets, "numbers": window.rich.numbers, "align": window.rich.align}[button]
    menu = action.menu()
    assert isinstance(menu, QMenu)
    next(choice for choice in menu.actions() if choice.text() == name).trigger()


def list_style(window: ComposeWindow) -> QTextListFormat.Style | None:
    current = window.rich.edit.textCursor().currentList()
    return current.format().style() if current is not None else None


def test_the_menus_offer_the_styles_and_alignments_of_the_reference_client(world: World, qtbot: QtBot) -> None:
    window = rich_window(world, qtbot)
    assert names(window, "bullets") == ["Disc", "Circle", "Square"]
    assert names(window, "numbers") == ["Decimal", "Lower Latin", "Upper Latin", "Lower Roman", "Upper Roman"]
    assert names(window, "align") == ["Left Align", "Center", "Right Align", "Justify"]
    for action, mode in ((window.rich.bullets, QToolButton.ToolButtonPopupMode.MenuButtonPopup), (window.rich.align, QToolButton.ToolButtonPopupMode.InstantPopup)):
        button = window.rich.toolbar.widgetForAction(action)
        assert isinstance(button, QToolButton) and button.popupMode() == mode
    close(window)


def test_a_style_starts_a_list_or_changes_the_list_the_cursor_is_in_and_is_sent(world: World, qtbot: QtBot) -> None:
    window = rich_window(world, qtbot)
    choose(window, "bullets", "Square")
    assert list_style(window) == QTextListFormat.Style.ListSquare
    assert '<ul type="square"' in window.rich.outgoing_html()
    choose(window, "bullets", "Circle")
    assert list_style(window) == QTextListFormat.Style.ListCircle
    choose(window, "numbers", "Upper Roman")
    assert list_style(window) == QTextListFormat.Style.ListUpperRoman
    assert '<ol type="I"' in window.rich.outgoing_html()  # standard HTML: the reader numbers I, II, III
    close(window)


def test_the_button_ends_a_list_of_its_kind_in_any_style(world: World, qtbot: QtBot) -> None:
    window = rich_window(world, qtbot)
    choose(window, "numbers", "Lower Latin")
    window.rich.numbers.trigger()
    assert list_style(window) is None
    window.rich.bullets.trigger()
    assert list_style(window) == QTextListFormat.Style.ListDisc
    window.rich.numbers.trigger()  # a bulleted list becomes a numbered one
    assert list_style(window) == QTextListFormat.Style.ListDecimal
    close(window)


def test_alignment_moves_the_paragraph_is_ticked_and_is_sent(world: World, qtbot: QtBot) -> None:
    window = rich_window(world, qtbot)
    choose(window, "align", "Center")
    assert window.rich.edit.alignment() & Qt.AlignmentFlag.AlignHCenter
    menu = window.rich.align.menu()
    assert isinstance(menu, QMenu)
    menu.aboutToShow.emit()
    assert [choice.text() for choice in menu.actions() if choice.isChecked()] == ["Center"]
    assert 'align="center"' in window.rich.outgoing_html()
    choose(window, "align", "Justify")
    assert 'align="justify"' in window.rich.outgoing_html()
    close(window)


def test_in_plain_text_the_lists_and_alignment_are_greyed(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    assert not window.rich.bullets.isEnabled() and not window.rich.numbers.isEnabled() and not window.rich.align.isEnabled()
    window.rich_action.trigger()
    assert window.rich.align.isEnabled()
    close(window)
