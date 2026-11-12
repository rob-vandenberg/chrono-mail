from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractItemView, QApplication, QListWidget, QListWidgetItem, QMenu, QStyle, QStyleOptionViewItem, QVBoxLayout, QWidget

from chrono_mail.ui.elidedlabel import ElidedLabel
from chrono_mail.ui.tagchips import TagColourBook
from chrono_mail.ui.foldertree import COUNT_GAP, COUNT_MARGIN, FolderItemDelegate
from chrono_mail.ui.models import COUNT_ROLE
from chrono_mail.ui.tagging import tag_icon

# --- Version ------------------------------------------------------------
__version__ = "tagfilter.py 4.4.585"

# --- Version history ----------------------------------------------------
# v4.4.585: show_quietly: a filter set elsewhere (Mail's Filter > Tagged with) shown in the block, its line in view (D-334)
# v4.4.584: One Tags block for every section (D-334): no "All …" line; a click on a tag turns its filter on, a second click (or Space, Enter) off; the active
#           line stays blue wherever the focus is, a check mark in place of its number, a hover text; the heading names the tag; the coloured tag shape of Mail
# v4.4.581: The number after a tag is drawn as a folder's unread, in bold at the right (the owner, D-327)
# v4.4.575: Settings > Sidebar's Tags tick on the Calendar and Tasks tabs: tags_shown, kept on this computer (D-327)
# v4.4.572: A tag's right-click menu has Show everything tagged…: the Tag overview (D-328)
# v4.4.571: Initial tag filter of a section's sidebar: "All …" and the tags in use with their counts, followed in place (D-327)

def tags_shown_key(kind: str) -> str:
    """The setting of Settings > Sidebar's Tags tick on the Calendar ("events") or Tasks ("tasks") tab: whether that sidebar shows its Tags."""
    return f"show_tags_{kind}"


def tags_shown(settings: QSettings, kind: str) -> bool:
    """Whether the Calendar or Tasks sidebar shows its Tags list (on unless the person took it away), this computer's view only."""
    return str(settings.value(tags_shown_key(kind), "true")).lower() != "false"


TAG_ROLE: int = Qt.ItemDataRole.UserRole + 1
ACTIVE_ROLE: int = Qt.ItemDataRole.UserRole + 2  # the line of the tag whose filter is on
CHECK_MARK: str = "✓"  # in place of the number on the active line (the owner, D-334)
ACTIVE_HINT: str = "Click again to show everything"


class TagLineDelegate(FolderItemDelegate):
    """A tag's line as a folder's, with its number in bold at the right; the active line blue whatever has the focus, with a check mark
    in place of the number: it shows a state, never a selection (D-334)."""

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        if not index.data(ACTIVE_ROLE):
            super().paint(painter, option, index)
            return
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        found: object = getattr(option, "widget")  # getattr: PySide's stubs give the fields of a style option no type
        widget = found if isinstance(found, QWidget) else None
        style = widget.style() if widget is not None else QApplication.style()
        palette: QPalette = getattr(item, "palette")  # getattr: no typed field
        full = getattr(item, "rect")  # getattr: no typed field
        bold = QFont(getattr(item, "font"))  # getattr: no typed field
        bold.setBold(True)
        mark_width = QFontMetrics(bold).horizontalAdvance(CHECK_MARK)
        painter.save()
        painter.fillRect(full, palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight))  # the blue also when the window is in the background
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, item, widget)
        icon_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, item, widget)
        icon = getattr(item, "icon")  # getattr: no typed field
        icon.paint(painter, icon_rect)
        room = full.right() - COUNT_MARGIN - mark_width - COUNT_GAP - text_rect.left()
        metrics = QFontMetrics(getattr(item, "font"))  # getattr: no typed field
        painter.setPen(palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText))
        painter.setFont(getattr(item, "font"))  # getattr: no typed field
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), metrics.elidedText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""), Qt.TextElideMode.ElideRight, max(room, 0)))
        painter.setFont(bold)
        painter.drawText(full.adjusted(0, 0, -COUNT_MARGIN, 0), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), CHECK_MARK)
        if widget is not None and widget.hasFocus() and isinstance(widget, QListWidget) and widget.currentIndex() == index:
            painter.setPen(QPen(palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText), 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(full.adjusted(1, 1, -2, -2))  # the keyboard focus is on this line (OR-16)
        painter.restore()


class TagLines(QListWidget):
    """The list of a Tags block: Space and Enter on the line with the focus turn its filter on or off, as a click does (OR-16)."""

    toggled = Signal(QListWidgetItem)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        item = self.currentItem()
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter) and item is not None:
            if not event.isAutoRepeat():  # a held key turns it once, never on and off again and again
                self.toggled.emit(item)
            return
        super().keyPressEvent(event)


class TagFilter(QWidget):
    """The Tags block of a sidebar (D-334): each tag in use with its number; a click on a tag shows only the items with it, a second click
    shows everything again. The tags are followed in place (D-146); the tag whose filter is on stays, also when no item carries it any more."""

    chosen = Signal(str)  # the tag whose filter is now on ('' for none)
    overview_wanted = Signal(str)  # Show everything tagged… on a tag: its name (D-328)

    def __init__(self, colours: TagColourBook, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colours = colours
        self._active: str = ""
        self._tags: list[tuple[str, int]] = []  # the tags in use last told, with their numbers
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title = ElidedLabel("Tags", self)  # a long tag name is cut with "…", never widening the sidebar
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.title.setContentsMargins(10, 6, 10, 4)
        layout.addWidget(self.title)
        self.list = TagLines(self)
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)  # the active tag is a state, drawn by the delegate, never a selection
        self.list.setItemDelegate(TagLineDelegate(self.list))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.list, 1)
        self.list.itemClicked.connect(self._toggle)
        self.list.toggled.connect(self._toggle)
        self.list.customContextMenuRequested.connect(self._menu)

    def tag(self) -> str:
        """The tag whose filter is on ('' for none)."""
        return self._active

    def choose(self, name: str) -> None:
        """Turn the filter of this tag on (off with ''), as a click does; its line comes into view. A tag not in the list changes nothing."""
        target = self._name_at(self._row(name)) if name else ""
        if name and not target:
            return
        if target.casefold() == self._active.casefold():
            return
        self._set_active(target)
        row = self._row(target) if target else None
        if row is not None:
            self.list.scrollToItem(self.list.item(row))  # turned on from elsewhere: its line comes into view (a refresh of the tags never scrolls)
        self.chosen.emit(self._active)

    def show_quietly(self, name: str) -> None:
        """The filter as it was set elsewhere (Mail's Filter > Tagged with): shown here, its line in view, without telling anyone; a tag no item
        carries gets its line while it is on."""
        if name.casefold() == self._active.casefold():
            return
        self._active = name
        self.follow(self._tags)
        row = self._row(name) if name else None
        if row is not None:
            self.list.scrollToItem(self.list.item(row))

    def clear_quietly(self) -> None:
        """No filter, without telling anyone (the caller loads what it shows itself)."""
        self._set_active("")

    def follow(self, tags: list[tuple[str, int]]) -> None:
        """The tags as they are now, with how many items carry them, changed in place: a number that changed is written again, a tag that came
        is put at its place, one that went is taken out; the rest stay (D-146). The tag whose filter is on stays, with no number, when it went."""
        self._tags = list(tags)
        if self._active and not any(name.casefold() == self._active.casefold() for name, _ in tags):
            tags = sorted([*tags, (self._active, 0)], key=lambda pair: pair[0].casefold())
        wanted = {name.casefold() for name, _ in tags}
        for row in range(self.list.count() - 1, -1, -1):
            if str(self.list.item(row).data(TAG_ROLE)).casefold() not in wanted:
                self.list.takeItem(row)
        for number, (name, count) in enumerate(tags):
            item = self.list.item(number)
            if item is None or str(item.data(TAG_ROLE)).casefold() != name.casefold():
                elsewhere = next((row for row in range(number + 1, self.list.count()) if str(self.list.item(row).data(TAG_ROLE)).casefold() == name.casefold()), None)
                if elsewhere is not None:  # its place in the order changed (a rename)
                    item = self.list.takeItem(elsewhere)
                    self.list.insertItem(number, item)
                else:
                    item = QListWidgetItem(name)
                    item.setData(TAG_ROLE, name)
                    self.list.insertItem(number, item)
            if item.text() != name:
                item.setText(name)
                item.setData(TAG_ROLE, name)
            if item.data(COUNT_ROLE) != count:
                item.setData(COUNT_ROLE, count)
            item.setIcon(tag_icon(QColor(self._colours.colour(name).name())))
        self._show_active()

    def _row(self, name: str) -> int | None:
        return next((row for row in range(self.list.count()) if str(self.list.item(row).data(TAG_ROLE)).casefold() == name.casefold()), None)

    def _name_at(self, row: int | None) -> str:
        return str(self.list.item(row).data(TAG_ROLE)) if row is not None else ""

    def _set_active(self, name: str) -> None:
        self._active = name
        self._show_active()

    def _show_active(self) -> None:
        """The active line marked (blue, a check mark, a hover text), the heading naming the tag, its line in view."""
        for row in range(self.list.count()):
            item = self.list.item(row)
            on = bool(self._active) and str(item.data(TAG_ROLE)).casefold() == self._active.casefold()
            if bool(item.data(ACTIVE_ROLE)) != on:
                item.setData(ACTIVE_ROLE, on)
            item.setToolTip(ACTIVE_HINT if on else "")
        self.title.setText(f"Tags: {self._active}" if self._active else "Tags")

    def _toggle(self, item: QListWidgetItem) -> None:
        name = str(item.data(TAG_ROLE) or "")
        self._set_active("" if self._active and name.casefold() == self._active.casefold() else name)
        self.chosen.emit(self._active)

    def _menu(self, position: QPoint) -> None:
        item = self.list.itemAt(position)
        name = str(item.data(TAG_ROLE) or "") if item is not None else ""
        if name:
            menu = QMenu(self)
            menu.addAction("Show everything tagged…", lambda: self.overview_wanted.emit(name))
            menu.exec(self.list.viewport().mapToGlobal(position))
