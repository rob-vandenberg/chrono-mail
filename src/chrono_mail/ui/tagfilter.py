from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from chrono_mail.core.colours import COLLECTION_WITHOUT_COLOUR
from chrono_mail.ui.collectionpanel import colour_icon
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "tagfilter.py 4.4.571"

# --- Version history ----------------------------------------------------
# v4.4.571: Initial tag filter of a section's sidebar: "All …" and the tags in use with their counts, followed in place (D-327)

TAG_ROLE: int = Qt.ItemDataRole.UserRole + 1
EVERY_TAG: str = ""  # the first row: no filter


class TagFilter(QWidget):
    """The Tags heading of a sidebar and its list: the first row shows everything, each other row only the items with that tag (D-327).
    The list follows the tags in place, so the tag chosen stays chosen; when the chosen tag goes, the first row is chosen."""

    chosen = Signal(str)  # the tag now chosen ('' for everything)
    menu_wanted = Signal(str, QPoint)  # a right-click on a tag: its name and where (global)

    def __init__(self, everything: str, colours: TagColourBook, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._everything = everything
        self._colours = colours
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title = QLabel("Tags", self)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.title.setContentsMargins(10, 6, 10, 4)
        layout.addWidget(self.title)
        self.list = QListWidget(self)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.list, 1)
        self.list.currentItemChanged.connect(self._current_changed)
        self.list.customContextMenuRequested.connect(self._menu)

    def tag(self) -> str:
        """The tag chosen ('' for everything)."""
        item = self.list.currentItem()
        return str(item.data(TAG_ROLE) or "") if item is not None else EVERY_TAG

    def choose(self, name: str) -> None:
        """Choose this tag (or everything with ''); nothing happens when it is not in the list."""
        for row in range(self.list.count()):
            if str(self.list.item(row).data(TAG_ROLE)).casefold() == name.casefold():
                self.list.setCurrentRow(row)
                return

    def follow(self, tags: list[tuple[str, int]]) -> None:
        """The tags as they are now, with how many items carry them, changed in place: a number that changed is written again, a tag that came
        is put at its place, one that went is taken out; the rest, and the tag chosen, stay (D-146). The chosen tag going chooses the first row."""
        if self.list.count() == 0 or str(self.list.item(0).data(TAG_ROLE)) != EVERY_TAG:
            everything = QListWidgetItem(self._everything)
            everything.setData(TAG_ROLE, EVERY_TAG)
            self.list.insertItem(0, everything)
            if self.list.currentItem() is None:
                self.list.blockSignals(True)  # the first filling: everything is shown already, nothing to load again
                self.list.setCurrentItem(everything)
                self.list.blockSignals(False)
        wanted = {name.casefold() for name, _ in tags}
        for row in range(self.list.count() - 1, 0, -1):
            if str(self.list.item(row).data(TAG_ROLE)).casefold() not in wanted:
                if self.list.currentRow() == row:
                    self.list.setCurrentRow(0)  # the chosen tag went: everything is shown again
                self.list.takeItem(row)
        for number, (name, count) in enumerate(tags, start=1):
            text = f"{name}  ({count})"
            item = self.list.item(number)
            if item is None or str(item.data(TAG_ROLE)).casefold() != name.casefold():
                elsewhere = next((row for row in range(number + 1, self.list.count()) if str(self.list.item(row).data(TAG_ROLE)).casefold() == name.casefold()), None)
                if elsewhere is not None:  # its place in the order changed (a rename)
                    was_chosen = self.list.currentRow() == elsewhere
                    self.list.blockSignals(True)  # the same tag stays chosen: nothing to load again
                    item = self.list.takeItem(elsewhere)
                    self.list.insertItem(number, item)
                    if was_chosen:
                        self.list.setCurrentItem(item)
                    self.list.blockSignals(False)
                else:
                    item = QListWidgetItem(text)
                    item.setData(TAG_ROLE, name)
                    self.list.insertItem(number, item)
            if item.text() != text:
                item.setText(text)
            item.setIcon(colour_icon(self._colours.colour(name).name() or COLLECTION_WITHOUT_COLOUR))

    def _current_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        self.chosen.emit(self.tag())

    def _menu(self, position: QPoint) -> None:
        item = self.list.itemAt(position)
        name = str(item.data(TAG_ROLE) or "") if item is not None else ""
        if name:
            self.menu_wanted.emit(name, self.list.viewport().mapToGlobal(position))
