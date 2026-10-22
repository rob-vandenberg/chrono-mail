"""What the Tasks and Contacts lists share (R-07): the model's greyed items and sync marks, the delegate's triangle (a click opens its conflict,
hovering says what a mark is), and the page's Delete, Move to, New tag…, the greying at the key press, the search delay and the error line.
Only the words differ ("task" or "contact"); a fix made here is made for both."""

from collections.abc import Callable

from PySide6.QtCore import QAbstractItemModel, QAbstractListModel, QEvent, QModelIndex, QPersistentModelIndex, QPoint, QRect, QTimer, Signal, SignalInstance
from PySide6.QtGui import QHelpEvent, QMouseEvent
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QAbstractItemView, QInputDialog, QLineEdit, QListView, QMessageBox, QStyledItemDelegate, QStyleOptionViewItem, QToolTip, QWidget
from typing import override

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.backend import AnyStep, Backend
from chrono_mail.ui.pimpages import PimPage
from chrono_mail.ui.syncmarks import CONFLICT, MARK_TIPS, SyncMarks
from chrono_mail.ui.tagchips import TagColourBook

# --- Version ------------------------------------------------------------
__version__ = "pimlist.py 1.9.273"

# --- Version history ----------------------------------------------------
# v1.9.273: R-19: delete and move of an item are the section's typed steps (_delete_step, _move_step) instead of command names
# v1.1.245: Initial: the shared part of the Tasks and Contacts lists, before written twice (R-07)

SEARCH_DELAY_MS: int = 250  # the list is asked again this long after the last key typed in the search field


def no_mark(item: str) -> str:
    return ""


class PimItemModel(QAbstractListModel):
    """A list of tasks or contacts: the items with an action under way (greyed) and each item's sync mark (D-194)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pending: set[str] = set()
        self.mark_of: Callable[[str], str] = no_mark

    def position_of(self, item: str) -> int:
        raise NotImplementedError

    def item_at(self, position: int) -> str:
        """The item on this row ('' for a heading, or outside the list)."""
        raise NotImplementedError

    def redraw(self, item: str) -> None:
        position = self.position_of(item)
        if position >= 0:
            self.dataChanged.emit(self.index(position), self.index(position))


class MarkDelegate(QStyledItemDelegate):
    """A row with a sync mark: a click on the triangle opens its conflict (what a person would guess, the owner); hovering a mark says what it
    is. Where the mark is drawn is the subclass's (_mark_area, the same place its paint uses)."""

    conflict_clicked = Signal(str)  # an item's triangle was clicked: its item (D-194)

    def __init__(self, colours: TagColourBook, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colours: TagColourBook = colours

    def _mark_area(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> tuple[QRect | None, str, str]:
        """Where the row's mark is drawn, its state and its item (no area without a mark)."""
        raise NotImplementedError

    @staticmethod
    def _mark_found(area: QRect | None, position: QPoint) -> bool:
        return area is not None and area.adjusted(-2, -2, 2, 2).contains(position)

    @override
    def editorEvent(self, event: QEvent, model: QAbstractItemModel, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            area, state, item = self._mark_area(option, index)
            if state == CONFLICT and self._mark_found(area, event.position().toPoint()):
                self.conflict_clicked.emit(item)
                return True
        return super().editorEvent(event, model, option, index)

    @override
    def helpEvent(self, event: QHelpEvent, view: QAbstractItemView, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> bool:
        area, state, _ = self._mark_area(option, index)
        if state and self._mark_found(area, event.pos()):
            QToolTip.showText(event.globalPos(), MARK_TIPS.get(state, ""), view)
            return True
        return super().helpEvent(event, view, option, index)


class PimListPage(PimPage):
    """Tasks or Contacts: a list of items with Delete, Move to, tags and a search field. The subclass gives its words, its commands and its
    model, list, delegate and search field (_items, _view, _marks_delegate, _search_field)."""

    item_word: str = "item"  # "task", "contact"


    def __init__(self, backend: Backend, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(backend, settings, parent)
        self._search_timer: QTimer = self._search_delay()

    def _items(self) -> PimItemModel:
        raise NotImplementedError

    def _view(self) -> QListView:
        raise NotImplementedError

    def _marks_delegate(self) -> MarkDelegate:
        raise NotImplementedError

    def _search_box(self) -> QLineEdit:
        raise NotImplementedError

    def _selected_items(self) -> list[str]:
        raise NotImplementedError

    def _set_tag(self, name: str, on: bool) -> None:
        """Give the selected items the tag, or take it away."""
        raise NotImplementedError

    def reload_list(self) -> None:
        raise NotImplementedError

    def edit_selected(self) -> None:
        raise NotImplementedError

    def _search_delay(self) -> QTimer:
        """The search field asks the list again a moment after the last key, not at every key."""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(SEARCH_DELAY_MS)
        timer.timeout.connect(self.reload_list)
        return timer

    def _search_changed(self, text: str) -> None:
        self._search_timer.start()

    @override
    def focus_search(self) -> None:
        self._search_box().setFocus()
        self._search_box().selectAll()

    def _double_clicked(self, index: QModelIndex) -> None:
        self.edit_selected()

    # --- an action under way -----------------------------------------------------------------------------------------

    def _take_away(self, items: list[str]) -> None:
        """At the key press of a delete or a move: greyed, and the selection moves at once to the next item (or the one before)."""
        model, view = self._items(), self._view()
        model.pending.update(items)
        for item in items:
            model.redraw(item)
        taken = set(items) | model.pending
        rows = [position for position in (model.position_of(item) for item in items) if position >= 0]
        if not rows:
            return

        def free(position: int) -> bool:
            item = model.item_at(position)
            return bool(item) and item not in taken

        after = next((position for position in range(max(rows) + 1, model.rowCount()) if free(position)), None)
        before = next((position for position in range(min(rows) - 1, -1, -1) if free(position)), None)
        target = after if after is not None else before
        if target is None:
            view.selectionModel().clear()
            self._nothing_left()
            return
        view.setCurrentIndex(model.index(target))
        view.scrollTo(model.index(target))

    def _nothing_left(self) -> None:
        """Every item that was there is under way: what the right side shows then."""

    def _ended(self, item: str) -> None:
        self._items().pending.discard(item)
        self._items().redraw(item)

    def _failed_for(self, item: str, error: ChronoMailError) -> None:
        self._ended(item)
        self._failed(error)

    # --- the actions -------------------------------------------------------------------------------------------------

    @override
    def delete_selected(self) -> None:
        items = self._selected_items()
        if not items:
            return
        word = f"this {self.item_word}" if len(items) == 1 else f"these {len(items)} {self.item_word}s"
        if QMessageBox.question(self.content, "Delete", f"Delete {word}?\n\nThe files are kept in the store's .deleted directory for 30 days.") != QMessageBox.StandardButton.Yes:
            return
        self._take_away(items)
        for item in items:
            self._delete_step(item).start(self._backend, lambda result, chosen=item: self._ended(chosen), lambda error, chosen=item: self._failed_for(chosen, error), owner=self)

    def _move_selected(self, target: str) -> None:
        items = self._selected_items()
        self._take_away(items)
        for item in items:
            self._move_step(item, target).start(self._backend, lambda result, chosen=item: self._ended(chosen), lambda error, chosen=item: self._failed_for(chosen, error), owner=self)

    def _delete_step(self, item: str) -> AnyStep[object]:
        """The delete of one item, as this section's engine command (task.delete, contact.delete)."""
        raise NotImplementedError

    def _move_step(self, item: str, target: str) -> AnyStep[object]:
        """The move of one item to another list or address book, as this section's engine command (task.move, contact.move)."""
        raise NotImplementedError

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self.content, "New tag", "Name of the tag:")
        if ok and name.strip():
            self._set_tag(name.strip(), True)

    # --- the sync marks ----------------------------------------------------------------------------------------------

    @override
    def conflict_sources(self) -> list[SignalInstance]:
        return [self._marks_delegate().conflict_clicked]

    @override
    def marks_changed(self, marks: SyncMarks) -> None:
        self._items().mark_of = marks.state
        self._view().viewport().update()
