from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QFocusEvent, QFont, QFontMetrics, QIcon, QPainter, QPalette
from PySide6.QtWidgets import QAbstractItemView, QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeView, QWidget

from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.dragdata import read_messages_accounts, read_messages_mime_data, read_messages_rest, read_messages_sources, read_messages_summary
from chrono_mail.ui.columns import SCHEDULED_FOLDER_PATH
from chrono_mail.ui.models import ACCOUNT_ROLE, COUNT_ROLE, FOLDER_PATH_ROLE, FOLDER_ROLE_ROLE, KIND_ROLE, SCHEDULED_TARGET_ROLES
from chrono_mail.ui.theme import show_focus

# --- Version ------------------------------------------------------------
__version__ = "foldertree.py 3.6.367"

# --- Version history ----------------------------------------------------
# v3.6.367: a drag of messages of several accounts is taken by a folder of any account of the list; the drop gives each message with its account (D-272)
# v3.6.366: messages dropped on a folder of another account are moved there; the drop names the account they come from (D-272)
# v3.6.354: Chrono Mail's own folders (Outbox, Scheduled, Snoozed, Recovered) known by their role, no longer by their name (CR-36)
# v3.6.350: a drop says when the rest of the selection follows (RV-206)
# v3.6.349: the drag checks at every pointer move read only the drag's summary, never its whole list; the drop checks the list against
#           it once (RV-205)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.1.208: A scheduled message can be dropped only on Drafts or Trash (D-178)
# v0.2.104: Dragged messages are taken as the copy kind (for the desktop's arrow with a plus) and still moved
# v0.2.103: The selection and the hover start just before the icon (the indent keeps the colour of the list); a folder that can take dragged messages gets a blue bar across the whole row
# v0.0.88: The selection is blue while the folder list has the focus and grey without it
# v0.0.87: The number of unread messages is drawn as a bold number at the right edge
# v0.0.17: Initial folder tree that accepts dropped messages

COUNT_MARGIN: int = 8  # pixels between the number of unread messages and the right edge
COUNT_GAP: int = 6  # pixels kept free between the name and the number


class FolderItemDelegate(QStyledItemDelegate):
    """Draws a folder as the style does, with the number of unread messages (when there are any) in bold at the right edge; the name is shortened before it reaches the number."""

    @staticmethod
    def count_text(index: QModelIndex | QPersistentModelIndex) -> str:
        count = int(index.data(COUNT_ROLE) or 0)
        return str(count) if count > 0 else ""

    @staticmethod
    def has_focus(option: QStyleOptionViewItem) -> bool:
        found: object = getattr(option, "widget")  # getattr: PySide's stubs give the fields of a style option no type
        return not isinstance(found, QWidget) or found.property("focused") is not False

    @override
    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        super().initStyleOption(option, index)
        if _is_selected(option) and not self.has_focus(option):  # on the grey selection the icon keeps the colour of text
            icon = _typed(option, "icon", QIcon)
            normal = icon.pixmap(_typed(option, "decorationSize", QSize), QIcon.Mode.Normal)
            plain = QIcon()
            plain.addPixmap(normal, QIcon.Mode.Normal)
            plain.addPixmap(normal, QIcon.Mode.Selected)
            setattr(option, "icon", plain)

    @override
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        count = self.count_text(index)
        if not count:
            super().paint(painter, option, index)
            return
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        bold = QFont(_typed(item, "font", QFont))
        bold.setBold(True)
        count_width = QFontMetrics(bold).horizontalAdvance(count)
        found: object = getattr(option, "widget")  # getattr: PySide's stubs give the fields of a style option no type
        widget = found if isinstance(found, QWidget) else None
        style = widget.style() if widget is not None else QApplication.style()
        full = _rect(option)
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, item, widget)
        room = full.right() - COUNT_MARGIN - count_width - COUNT_GAP - text_rect.left()
        name = _typed(item, "fontMetrics", QFontMetrics).elidedText(_typed(item, "text", str), Qt.TextElideMode.ElideRight, max(room, 0))
        setattr(item, "text", name)
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item, painter, widget)
        palette = _typed(option, "palette", QPalette)
        painter.save()
        painter.setFont(bold)
        painter.setPen(palette.color(QPalette.ColorRole.HighlightedText if _is_selected(option) and self.has_focus(option) else QPalette.ColorRole.Text))
        painter.drawText(full.adjusted(0, 0, -COUNT_MARGIN, 0), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), count)
        painter.restore()

    @override
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        size = super().sizeHint(option, index)
        count = self.count_text(index)
        if count:
            bold = QFont(_typed(option, "font", QFont))
            bold.setBold(True)
            size.setWidth(size.width() + COUNT_GAP + QFontMetrics(bold).horizontalAdvance(count) + COUNT_MARGIN)
        return size


class FolderTree(QTreeView):
    """The folder tree. Messages dragged from the list can be dropped on a folder of their own account or of another (D-272)."""

    messages_dropped = Signal(str, str, list, int, str)  # account id, target folder path, [(folder, message id)], the list's places_epoch when the
    # rest of the selection follows (-1: nothing follows)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(True)
        self.folder_delegate = FolderItemDelegate(self)
        self.setItemDelegate(self.folder_delegate)
        self.setProperty("focused", False)
        self.drop_row: QPersistentModelIndex | None = None  # the folder under the pointer that can take the dragged messages

    @override
    def drawBranches(self, painter: QPainter, rect: QRect, index: QModelIndex | QPersistentModelIndex) -> None:
        """The space left of a folder (its indent) keeps the colour of the list: the selection and the hover start just before the icon."""
        if not self._is_drop_row(index):
            painter.fillRect(rect, self.palette().color(QPalette.ColorRole.Window))
        super().drawBranches(painter, rect, index)  # the arrows that open and close a group

    def _is_drop_row(self, index: QModelIndex | QPersistentModelIndex) -> bool:
        return self.drop_row is not None and self.drop_row.isValid() and QPersistentModelIndex(index) == self.drop_row

    @override
    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        """The folder that will take a drop: a blue bar across the whole row, the text in the colour for it."""
        if not self._is_drop_row(index):
            super().drawRow(painter, option, index)
            return
        row = _rect(option)
        palette = QPalette(_typed(option, "palette", QPalette))
        painter.fillRect(QRect(0, row.top(), self.viewport().width(), row.height()), palette.color(QPalette.ColorRole.Highlight))
        palette.setColor(QPalette.ColorRole.Text, palette.color(QPalette.ColorRole.HighlightedText))
        marked = QStyleOptionViewItem(option)
        setattr(marked, "palette", palette)
        super().drawRow(painter, marked, index)

    def _set_drop_row(self, index: QModelIndex | None) -> None:
        wanted = QPersistentModelIndex(index) if index is not None and index.isValid() else None
        if wanted != self.drop_row:
            self.drop_row = wanted
            self.viewport().update()

    @override
    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        show_focus(self, True)

    @override
    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        if event.reason() != Qt.FocusReason.PopupFocusReason:  # a context menu leaves the selection as it is
            show_focus(self, False)

    def _target(self, event: QDragMoveEvent | QDropEvent) -> tuple[str, str] | None:
        """The (account, folder) under the pointer when the dragged messages may go there."""
        dragged = read_messages_summary(event.mimeData())  # never the whole list: it is read at every pointer move (RV-205)
        index = self.indexAt(event.position().toPoint())
        if dragged is None or not index.isValid() or index.data(KIND_ROLE) != "folder":
            return None
        account, folder, role = str(index.data(ACCOUNT_ROLE)), str(index.data(FOLDER_PATH_ROLE)), str(index.data(FOLDER_ROLE_ROLE))
        sources = frozenset({dragged[0]}) if dragged[0] else read_messages_accounts(event.mimeData())  # several accounts: each message's (D-272)
        if not sources or role == "outbox" or (sources == {account} and dragged[1] == {folder}):
            return None
        if SCHEDULED_FOLDER_PATH in dragged[1] and (sources != {account} or role not in SCHEDULED_TARGET_ROLES):
            return None  # a scheduled message goes back to Drafts (no longer scheduled) or to Trash of its own account, nowhere else
        if sources != {account} and (role == "scheduled" or not all(self._known(source) for source in sources - {account})):
            return None  # Scheduled takes only its own account's messages; the others take those of the accounts of this list (D-272)
        return account, folder

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if read_messages_summary(event.mimeData()) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._target(event) is not None:
            self._set_drop_row(self.indexAt(event.position().toPoint()))
            event.setDropAction(Qt.DropAction.CopyAction)  # the cursor with a plus; the messages are moved
            event.accept()
        else:
            self._set_drop_row(None)
            event.ignore()

    @override
    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._set_drop_row(None)
        super().dragLeaveEvent(event)

    def _known(self, account: str) -> bool:
        """Whether this account is in the list (a drag from another account: never one of another program or one removed meanwhile)."""
        model = self.model()
        return model is not None and bool(model.match(model.index(0, 0), ACCOUNT_ROLE, account, 1, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive))

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        self._set_drop_row(None)
        target = self._target(event)
        dragged = read_messages_mime_data(event.mimeData())
        sources = read_messages_sources(event.mimeData())
        summary = read_messages_summary(event.mimeData())
        rest = read_messages_rest(event.mimeData()) if event.source() is not None else None  # only a drag of this program's own list
        folders = {folder for folder, _ in dragged[1]} if dragged is not None else set[str]()
        if target is None or dragged is None or sources is None or summary is None or dragged[0] != summary[0] or not (folders == summary[1] or (rest is not None and folders <= summary[1])):
            event.ignore()  # the list that moves must be the one the checks were made for (a drag from another program)
            return
        event.setDropAction(Qt.DropAction.CopyAction)  # the cursor with a plus; the messages are moved
        event.accept()
        self.messages_dropped.emit(target[0], target[1], sources, -1 if rest is None else rest, dragged[0])  # each as (account, folder, id)
