from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QListWidgetItem
from pytestqt.qtbot import QtBot

from chrono_mail.ui.composer import RECENT_EMOJI_KEY, ComposeWindow
from chrono_mail.ui.emojidata import EMOJI_GROUPS
from chrono_mail.ui.emojipanel import PER_ROW, RECENT, RECENT_MOST, ROLE_EMOJI, recent_from
from tests.ui.test_ui_signatures import close, composer
from tests.ui.world import World, add_window, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_compose_emoji.py 4.6.643"

# --- Version history ----------------------------------------------------
# v4.6.643: Initial: the smiley's emoji panel, emoji only, the recently used kept (D-363)

EVERY: int = sum(len(emojis.split(" ")) for _, emojis in EMOJI_GROUPS)


def item_of(window: ComposeWindow, emoji: str) -> QListWidgetItem:
    grid = window.rich.emoji.grid
    return next(grid.item(row) for row in range(grid.count()) if grid.item(row).data(ROLE_EMOJI) == emoji)


def test_the_panel_has_the_emoji_twelve_to_a_row_and_the_category_row(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    emoji = window.rich.emoji
    assert emoji.toolTip() == "Emoji" and emoji.grid.count() == EVERY and EVERY > 1500
    assert list(emoji.categories) == [RECENT, "Smileys", "People", "Animals and nature", "Food and drink", "Travel and places", "Activities",
                                      "Objects", "Symbols", "Flags"]
    assert not emoji.categories[RECENT].isEnabled()  # nothing used yet
    assert emoji.grid.gridSize().width() * PER_ROW <= emoji.grid.viewport().width() + emoji.grid.gridSize().width() - 1  # twelve to a row
    close(window)


def test_a_click_puts_the_emoji_at_the_cursor_in_plain_and_in_rich_text(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    window.body.setPlainText("Thanks ")
    cursor = window.body.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    window.body.setTextCursor(cursor)
    assert window.rich.emoji.isEnabled()  # also in plain text
    window.rich.emoji.grid.itemClicked.emit(item_of(window, "😀"))
    assert window.body.toPlainText().startswith("Thanks 😀")
    window.rich_action.trigger()
    window.rich.emoji.grid.itemClicked.emit(item_of(window, "🎄"))
    assert "🎄" in window.rich.toPlainText()
    assert window.rich.emoji.recent == ["🎄", "😀"] and window.rich.emoji.categories[RECENT].isEnabled()
    assert window.rich.emoji.grid.item(0).data(ROLE_EMOJI) == "🎄"  # the recently used come first
    close(window)


def test_the_category_row_scrolls_to_its_group(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot)
    emoji = window.rich.emoji
    emoji.panel_menu.popup(window.mapToGlobal(window.rect().center()))
    wait(qtbot, emoji.grid.isVisible)
    emoji.categories["Flags"].click()
    scrollbar = emoji.grid.verticalScrollBar()
    assert scrollbar is not None and scrollbar.value() > 0
    emoji.panel_menu.close()
    close(window)


def test_the_recently_used_are_kept_for_the_next_window(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    first = ComposeWindow(world.backend, list(world.call("account.list")), world.account_id, settings=settings)
    add_window(qtbot, first)
    first.rich.emoji.grid.itemClicked.emit(item_of(first, "👍"))
    assert settings.value(RECENT_EMOJI_KEY) == "👍"
    close(first)
    second = ComposeWindow(world.backend, list(world.call("account.list")), world.account_id, settings=settings)
    add_window(qtbot, second)
    assert second.rich.emoji.recent == ["👍"]
    close(second)


def test_what_is_kept_is_read_safely() -> None:
    assert recent_from("") == [] and recent_from("x 😀 😀 abc 🎄") == ["😀", "🎄"]
    many = " ".join(emojis for _, emojis in EMOJI_GROUPS)
    assert len(recent_from(many)) == RECENT_MOST
