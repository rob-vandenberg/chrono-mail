"""The smiley of the message editor's bar (D-363): a panel of emoji as the reference client has one, emoji only (the owner, Q83). A grid of
twelve to a row, the category row at the bottom (recently used, then Unicode's groups); a click puts the emoji at the cursor."""

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QMenu, QToolButton, QVBoxLayout, QWidget, QWidgetAction

from chrono_mail.ui.emojidata import EMOJI_GROUPS
from chrono_mail.ui.icons import smile_icon, theme_icon

# --- Version ------------------------------------------------------------
__version__ = "emojipanel.py 4.6.643"

# --- Version history ----------------------------------------------------
# v4.6.643: Initial version: the emoji panel of the smiley, emoji only, the recently used first (D-363)

PER_ROW: int = 12
CELL: int = 34  # the side of one emoji's place in the grid, in pixels
EMOJI_POINTS: int = 16
RECENT_MOST: int = 24  # the recently used kept, the newest first
RECENT: str = "Recently used"
GROUP_MARKS: dict[str, str] = {  # the emoji on each group's button in the category row, as in the reference client
    "Smileys": "😀", "People": "👍", "Animals and nature": "🐵", "Food and drink": "🍉", "Travel and places": "🌍", "Activities": "🎄",
    "Objects": "👠", "Symbols": "🚫", "Flags": "🏁",
}
ROLE_EMOJI: int = Qt.ItemDataRole.UserRole + 1


def recent_from(text: str) -> list[str]:
    """The recently used as kept (separated by spaces): only emoji of the panel, each once, at most RECENT_MOST."""
    known = {emoji for _, emojis in EMOJI_GROUPS for emoji in emojis.split(" ")}
    found: list[str] = []
    for emoji in text.split(" "):
        if emoji in known and emoji not in found:
            found.append(emoji)
    return found[:RECENT_MOST]


class EmojiButton(QToolButton):
    """The smiley: its menu is the panel; a click on an emoji puts it where `target` says and remembers it as recently used."""

    recent_changed = Signal(str)  # the recently used, separated by spaces, to be kept on this computer

    def __init__(self, insert: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._insert = insert
        self.recent: list[str] = []
        self.setIcon(smile_icon())  # drawn in the colour of text, as every icon of the bar
        self.setText("Emoji")
        self.setToolTip("Emoji")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.panel_menu = QMenu(self)
        panel = QWidget(self.panel_menu)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        self.grid = QListWidget(panel)
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setFlow(QListWidget.Flow.LeftToRight)
        self.grid.setWrapping(True)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setUniformItemSizes(True)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setGridSize(QSize(CELL, CELL))
        font = QFont(self.grid.font())
        font.setPointSize(EMOJI_POINTS)
        self.grid.setFont(font)
        scrollbar = self.grid.verticalScrollBar()
        self.grid.setFixedSize(PER_ROW * CELL + (scrollbar.sizeHint().width() if scrollbar is not None else 0) + 4, 8 * CELL)
        self.grid.itemClicked.connect(self._clicked)
        layout.addWidget(self.grid)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.categories: dict[str, QToolButton] = {}
        for name in (RECENT, *(group for group, _ in EMOJI_GROUPS)):
            button = QToolButton(panel)
            button.setToolTip(name)
            if name == RECENT:
                button.setIcon(theme_icon("clock"))
            else:
                button.setText(GROUP_MARKS.get(name, name[:1]))
            button.clicked.connect(lambda checked=False, chosen=name: self.show_group(chosen))
            row.addWidget(button)
            self.categories[name] = button
        row.addStretch(1)
        layout.addLayout(row)
        action = QWidgetAction(self.panel_menu)
        action.setDefaultWidget(panel)
        self.panel_menu.addAction(action)
        self.setMenu(self.panel_menu)
        self._first_of: dict[str, QListWidgetItem] = {}
        self._fill()

    def set_recent(self, text: str) -> None:
        self.recent = recent_from(text)
        self._fill()

    def _fill(self) -> None:
        """The grid: the recently used first (when there are any), then every group in Unicode's order."""
        self.grid.clear()
        self._first_of.clear()
        groups: list[tuple[str, list[str]]] = [(RECENT, self.recent)] if self.recent else []
        groups.extend((name, emojis.split(" ")) for name, emojis in EMOJI_GROUPS)
        for name, emojis in groups:
            for place, emoji in enumerate(emojis):
                item = QListWidgetItem(emoji)
                item.setData(ROLE_EMOJI, emoji)
                item.setToolTip(name)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.grid.addItem(item)
                if place == 0:
                    self._first_of[name] = item
        self.categories[RECENT].setEnabled(bool(self.recent))

    def show_group(self, name: str) -> None:
        """A button of the category row: the grid scrolls to that group."""
        item = self._first_of.get(name)
        if item is not None:
            self.grid.scrollToItem(item, QListWidget.ScrollHint.PositionAtTop)

    def _clicked(self, item: QListWidgetItem) -> None:
        emoji = str(item.data(ROLE_EMOJI))
        self.panel_menu.close()
        self._insert(emoji)
        self.recent = [emoji, *(kept for kept in self.recent if kept != emoji)][:RECENT_MOST]
        self._fill()
        self.recent_changed.emit(" ".join(self.recent))
