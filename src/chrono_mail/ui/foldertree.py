from typing import override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QCursor, QDrag, QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QFocusEvent, QFont, QFontMetrics, QIcon, QMouseEvent, QPainter, QPalette,
)
from PySide6.QtWidgets import QAbstractItemView, QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeView, QWidget

from chrono_mail.core.folder import OWN_FOLDERS
from chrono_mail.ui.delegate import _is_selected, _rect, _typed
from chrono_mail.ui.dragdata import (
    folder_mime_data, read_folder_mime_data, read_messages_accounts, read_messages_mime_data, read_messages_rest, read_messages_sources, read_messages_summary,
)
from chrono_mail.ui.columns import SCHEDULED_FOLDER_PATH
from chrono_mail.ui.models import ACCOUNT_ROLE, COUNT_ROLE, FOLDER_PATH_ROLE, FOLDER_ROLE_ROLE, KIND_ROLE, SCHEDULED_TARGET_ROLES
from chrono_mail.ui.theme import show_focus

# --- Version ------------------------------------------------------------
__version__ = "foldertree.py 4.8.661"

# --- Version history ----------------------------------------------------
# v4.8.661: a folder the person made can be dragged onto another folder of its account or onto its account, and moves there (B-38,
#           D-372): the folder that would take it gets the blue bar; never into itself, where it is already, where a folder of that name is,
#           nor into Chrono Mail's own folders; a closed folder held under it opens after a moment
# v4.4.612: the indent left of a folder has the dark list background (base), as the rest of the tree (the owner)
# v4.4.596: an arrow or page key moves past a folder that only holds folders (Gmail's [Gmail]), opening it on the way down; nothing is
#           dropped on it (D-335)
# v4.2.415: a drop of a mix is taken when one of its messages may go there; each message is then moved by its own rules (D-287)
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
OPEN_AFTER: int = 700  # milliseconds a dragged folder is held over a closed folder before that opens
OWN_ROLES: frozenset[str] = frozenset(role.value for role in OWN_FOLDERS)  # Outbox, Scheduled, Snoozed, Recovered: no folder goes into them


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


KEYS_DOWN: frozenset[QAbstractItemView.CursorAction] = frozenset({
    QAbstractItemView.CursorAction.MoveDown, QAbstractItemView.CursorAction.MoveNext, QAbstractItemView.CursorAction.MovePageDown,
    QAbstractItemView.CursorAction.MoveEnd,
})


def holds_only_folders(index: QModelIndex | QPersistentModelIndex) -> bool:
    """A folder entry that exists only as the parent of other folders (Gmail's [Gmail]): it cannot be selected (D-335)."""
    return index.isValid() and str(index.data(KIND_ROLE)) == "folder" and not bool(index.flags() & Qt.ItemFlag.ItemIsSelectable)


class FolderTree(QTreeView):
    """The folder tree. Messages dragged from the list can be dropped on a folder of their own account or of another (D-272); a folder the
    person made can be dragged onto another folder of its account or onto its account (B-38)."""

    @override
    def moveCursor(self, cursorAction: QAbstractItemView.CursorAction, modifiers: Qt.KeyboardModifier) -> QModelIndex:  # noqa: N802, N803 - Qt's names
        """The keyboard never stops on a folder that only holds folders: down it opens and goes on to its first folder, up it goes on to
        the entry above; with nothing beyond it, the current entry stays."""
        index = super().moveCursor(cursorAction, modifiers)
        if holds_only_folders(index) and cursorAction is QAbstractItemView.CursorAction.MoveLeft:
            return self.currentIndex()  # Left from a folder inside it: stay, as at the top of a tree
        down = cursorAction in KEYS_DOWN
        while holds_only_folders(index):
            if down:
                self.setExpanded(index, True)
            onward = self.indexBelow(index) if down else self.indexAbove(index)
            if not onward.isValid():
                return self.currentIndex()
            index = onward
        if cursorAction is QAbstractItemView.CursorAction.MoveEnd:
            while self.indexBelow(index).isValid():  # End: the last entry, also inside a holder that opened on the way
                index = self.indexBelow(index)
        return index

    messages_dropped = Signal(str, str, list, int, str)  # account id, target folder path, [(folder, message id)], the list's places_epoch when the
    # rest of the selection follows (-1: nothing follows)
    folder_dropped = Signal(str, str, str)  # account id, the folder dragged, its new path (B-38)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)  # only a folder the person made can be picked up (FolderTreeModel.flags)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDropIndicatorShown(True)
        self.folder_delegate = FolderItemDelegate(self)
        self.setItemDelegate(self.folder_delegate)
        self.setProperty("focused", False)
        self.drop_row: QPersistentModelIndex | None = None  # the folder under the pointer that can take the dragged messages or folder
        self._pressed: QPersistentModelIndex | None = None  # the entry the left button went down on: the one a drag picks up

    @override
    def drawBranches(self, painter: QPainter, rect: QRect, index: QModelIndex | QPersistentModelIndex) -> None:
        """The space left of a folder (its indent) keeps the colour of the list: the selection and the hover start just before the icon."""
        if not self._is_drop_row(index):
            painter.fillRect(rect, self.palette().color(QPalette.ColorRole.Base))
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
        if dragged is None or not index.isValid() or index.data(KIND_ROLE) != "folder" or holds_only_folders(index):
            return None  # Gmail's [Gmail] holds only folders: no mail can go there (D-335)
        account, folder, role = str(index.data(ACCOUNT_ROLE)), str(index.data(FOLDER_PATH_ROLE)), str(index.data(FOLDER_ROLE_ROLE))
        sources = frozenset({dragged[0]}) if dragged[0] else read_messages_accounts(event.mimeData())  # several accounts: each message's (D-272)
        if not sources or role == "outbox" or (sources == {account} and dragged[1] == {folder}):
            return None
        moving = dragged[1] - {folder} if sources == {account} else dragged[1]  # of one account: those not in the target already; no folders
        # known yet (a large selection whose places come later, RV-206): taken, the drop waits for them as before
        if (dragged[1] and not moving) or (moving == {SCHEDULED_FOLDER_PATH} and (account not in sources or role not in SCHEDULED_TARGET_ROLES)):
            return None  # only scheduled messages: they go back to Drafts (no longer scheduled) or to Trash of their own account, nowhere else
        if role == "scheduled" and account not in sources:
            return None  # Scheduled takes only its own account's messages
        if not all(self._known(source) for source in sources - {account}):
            return None  # the other folders take the messages of the accounts of this list (D-272)
        # a mix: the drop is taken when one message may go; each is then moved as if dropped alone (messageactions.move_allowed, D-287)
        return account, folder

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        index = self.indexAt(event.position().toPoint()) if event.button() == Qt.MouseButton.LeftButton else QModelIndex()
        self._pressed = QPersistentModelIndex(index) if index.isValid() else None
        super().mousePressEvent(event)

    @override
    def startDrag(self, supportedActions: Qt.DropAction) -> None:  # noqa: N802, N803 - Qt's names
        """The folder the button went down on is picked up, when it can move (FolderTreeModel.flags); its row goes along under the pointer."""
        index = self._pressed
        if index is None or not index.isValid() or not (index.flags() & Qt.ItemFlag.ItemIsDragEnabled):
            return
        drag = QDrag(self)
        drag.setMimeData(folder_mime_data(str(index.data(ACCOUNT_ROLE)), str(index.data(FOLDER_PATH_ROLE))))
        row = self.visualRect(self.model().index(index.row(), 0, index.parent()))
        if row.isValid():
            drag.setPixmap(self.viewport().grab(row))
            pointer = self.viewport().mapFromGlobal(QCursor.pos())
            drag.setHotSpot(pointer - row.topLeft() if row.contains(pointer) else QPoint(0, 0))
        drag.exec(Qt.DropAction.MoveAction)
        self._set_drop_row(None)

    def _folder_target(self, event: QDragMoveEvent | QDropEvent) -> tuple[str, str, str] | None:
        """(account, the folder dragged, its new path) when the folder under the pointer, or the account's own row, may take the dragged
        folder: only in its own account, never into itself or a folder inside it, where it is already, where a folder of its name is
        (the engine would refuse it), into a folder that only holds folders (Gmail's [Gmail]) or into Chrono Mail's own folders."""
        dragged = read_folder_mime_data(event.mimeData())
        index = self.indexAt(event.position().toPoint())
        if dragged is None or not index.isValid():
            return None
        account, folder = dragged
        if str(index.data(ACCOUNT_ROLE) or "") != account or not self._movable(account, folder):
            return None  # only a folder of this list that can move, into its own account
        kind = str(index.data(KIND_ROLE))
        if kind == "account":
            parent = ""  # its account: to the top
        elif kind == "folder" and not holds_only_folders(index) and str(index.data(FOLDER_ROLE_ROLE)) not in OWN_ROLES:
            parent = str(index.data(FOLDER_PATH_ROLE))
        else:
            return None  # Favorites, groups, Unread, Flagged, a pinned folder: no place for a folder
        if parent == folder or parent.startswith(folder + "/") or parent == folder.rpartition("/")[0]:
            return None
        name = folder.rpartition("/")[2]
        new = f"{parent}/{name}" if parent else name
        model = index.model()
        if any(str(model.index(row, 0, index).data(FOLDER_PATH_ROLE) or "") == new for row in range(model.rowCount(index))):
            return None
        return account, folder, new

    def _movable(self, account: str, folder: str) -> bool:
        """Whether the dragged folder is a folder of this list that can move (another program's drag data, or a folder gone meanwhile: no)."""
        model = self.model()
        if model is None or model.rowCount() == 0:
            return False
        found = model.match(model.index(0, 0), FOLDER_PATH_ROLE, folder, -1, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)
        return any(str(index.data(ACCOUNT_ROLE)) == account and str(index.data(KIND_ROLE)) == "folder" and bool(index.flags() & Qt.ItemFlag.ItemIsDragEnabled) for index in found)

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        folder = read_folder_mime_data(event.mimeData()) is not None
        if folder or read_messages_summary(event.mimeData()) is not None:
            self.setAutoExpandDelay(OPEN_AFTER if folder else -1)  # a dragged folder opens a closed folder held under it; messages as before
            event.acceptProposedAction()
        else:
            event.ignore()

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if read_folder_mime_data(event.mimeData()) is not None:
            self._drag_folder(event)
            return
        if self._target(event) is not None:
            self._set_drop_row(self.indexAt(event.position().toPoint()))
            event.setDropAction(Qt.DropAction.CopyAction)  # the cursor with a plus; the messages are moved
            event.accept()
        else:
            self._set_drop_row(None)
            event.ignore()

    def _drag_folder(self, event: QDragMoveEvent) -> None:
        if self._folder_target(event) is not None:
            self._set_drop_row(self.indexAt(event.position().toPoint()))
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            self._set_drop_row(None)
            event.ignore()
        super().dragMoveEvent(event)  # Qt opens a closed folder held under the pointer (autoExpandDelay) and scrolls near the edges
        event.setAccepted(self.drop_row is not None)  # Qt's own answer is about its model, never about the folder

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
        if read_folder_mime_data(event.mimeData()) is not None:
            moving = self._folder_target(event)
            if moving is None:
                event.ignore()
                return
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self.setExpanded(self.indexAt(event.position().toPoint()), True)  # the folder it went into opens, so it is seen there
            self.folder_dropped.emit(*moving)
            return
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
