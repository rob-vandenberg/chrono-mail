"""What the window knows of the sync conflicts and of the changes waiting for the servers (D-194): which items carry the triangle (a conflict) or the
waiting mark, how many there are, and the drawing of both marks. One SyncMarks for the window; it asks the engine again only when a fact says
something changed (conflicts_changed, waiting_changed, or a synchronisation that ended)."""

from PySide6.QtCore import QLineF, QObject, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPen, QPixmap

from chrono_mail.engine.changes import Change
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_conflicts import CONFLICT_LIST, SYNC_WAITING, ConflictList, ConflictSummary, WaitingList
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.icons import theme_icon

# --- Version ------------------------------------------------------------
__version__ = "syncmarks.py 2.0.286"

# --- Version history ----------------------------------------------------
# v2.0.286: the icons of a collection's name or colour conflict: changed on both sides, or refused by the server (F3)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.1.240: Folders renamed or deleted offline: refused, or changed on the server (R-31)
# v1.1.237: A mail folder deleted on the server with changes here is drawn as deleted on the server (R-30)
# v1.1.235: A collection deleted here and changed on the server, or its delete refused (R-28)
# v1.1.234: A whole collection deleted on the server is drawn as deleted on the server (R-25)
# v1.1.233: The kinds of conflict in lines (the owner): our own two arrows, Breeze's no-entry circle, database and computer, the last two with a
#           solid minus badge; drawn in the theme's text colour (paint_why, why_pixmap)
# v1.1.230: MARK_GAP: 3 pixels after a mark, as after the coloured dot (the owner)
# v1.1.229: Marks 20% smaller (MARK_SIZE 11); their hover texts; the four kinds of conflict with their Breeze icons (the owner)
# v1.1.226: Initial: the conflicts and waiting changes the window shows, and their marks (R-23, R-24 step C, D-194)

CONFLICT: str = "conflict"
WAITING: str = "waiting"
CONFLICT_ICON: str = "emblem-warning"  # Breeze: the triangle with "!" (the owner's choice)
WAITING_ICON: str = "upload-later"  # Breeze: a clock with an arrow up, "send later", in the colour of text: quiet
FACTS: frozenset[str] = frozenset({"conflicts_changed", "waiting_changed", "collection_changed"})
MARK_SIZE: int = 11  # pixels at the normal size: about as tall as a capital letter (the owner: 20% smaller than 14)
MARK_GAP: int = 3  # pixels between a mark and the text after it: the same as between the coloured dot and the text (the owner)
MARK_TIPS: dict[str, str] = {CONFLICT: "Sync conflict: click to settle it", WAITING: "Waiting for the server"}  # what hovering a mark says

# Why two sides differ, in four kinds (What and Fields say what it is about), each with its Breeze icon (D-194, the owner)
BOTH: str = "both"  # changed on both sides
REFUSED: str = "refused"  # refused by the server
GONE_THERE: str = "gone_there"  # deleted on the server, changed here
GONE_HERE: str = "gone_here"  # deleted here, changed on the server
WHY_WORDS: dict[str, str] = {
    BOTH: "Changed on both sides", REFUSED: "Refused by the server", GONE_THERE: "Deleted on the server, changed here", GONE_HERE: "Deleted here, changed on the server",
}
WHY_ICONS: dict[str, str] = {REFUSED: "dialog-cancel-symbolic", GONE_THERE: "network-server-database-symbolic", GONE_HERE: "computer-symbolic"}  # Breeze, lines only
WITH_BADGE: frozenset[str] = frozenset({GONE_THERE, GONE_HERE})  # a solid minus badge: deleted on that side
REASON_KINDS: dict[str, str] = {
    "clash": BOTH, "deleted_on_server": GONE_THERE, "changed_on_server": GONE_HERE, "collection_deleted_on_server": GONE_THERE, "collection_look_clash": BOTH, "collection_look_refused": REFUSED, "folder_deleted_on_server": GONE_THERE, "folder_deleted_with_mail_here": GONE_THERE, "collection_changed_on_server": GONE_HERE, "folder_deleted_here_changed": GONE_HERE,
    "refused": REFUSED, "delete_refused": REFUSED, "marks_refused": REFUSED, "move_refused": REFUSED, "message_refused": REFUSED, "folder_refused": REFUSED, "collection_delete_refused": REFUSED, "folder_rename_refused": REFUSED, "folder_delete_refused": REFUSED,
}


def why_kind(reason: str) -> str:
    return REASON_KINDS.get(reason, REFUSED)


def _two_arrows(painter: QPainter, area: QRectF, colour: QColor) -> None:
    """Changed on both sides: our own two arrows, right above and left below, in the line style of the Breeze icons (the owner)."""
    size = area.width()
    pen = QPen(colour, max(1.0, size / 16))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for top, rightwards in ((0.32, True), (0.68, False)):
        y = area.top() + size * top
        painter.drawLine(QLineF(area.left() + size * 0.12, y, area.left() + size * 0.88, y))
        tip = area.left() + size * (0.88 if rightwards else 0.12)
        back = size * 0.2 * (-1 if rightwards else 1)
        painter.drawLine(QLineF(tip, y, tip + back, y - size * 0.17))
        painter.drawLine(QLineF(tip, y, tip + back, y + size * 0.17))


def _badge(painter: QPainter, area: QRectF, colour: QColor, background: QColor) -> None:
    """A solid circle in the colour of the lines with a minus in the background's colour, at the bottom right, free of the lines under it (the
    owner: it stands out against an icon of lines, and needs no outline)."""
    size = area.width()
    diameter = size * 0.56
    centre = QPointF(area.right() - diameter / 2, area.bottom() - diameter / 2)
    ring = max(1.0, size / 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(background)
    painter.drawEllipse(centre, diameter / 2 + ring, diameter / 2 + ring)
    painter.setBrush(colour)
    painter.drawEllipse(centre, diameter / 2, diameter / 2)
    painter.setPen(QPen(background, max(1.2, size / 11)))
    painter.drawLine(QLineF(centre.x() - diameter * 0.28, centre.y(), centre.x() + diameter * 0.28, centre.y()))


def paint_why(painter: QPainter, area: QRectF, kind: str, background: QColor, selected: bool = False) -> None:
    """The icon of a kind of conflict, in lines of the theme's text colour (on a selected row its highlighted text), drawn in `area` (square)."""
    palette = QGuiApplication.palette()
    colour = palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.ButtonText)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind == BOTH:
        _two_arrows(painter, area, colour)
    else:
        theme_icon(WHY_ICONS[kind]).paint(painter, area.toRect(), Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Selected if selected else QIcon.Mode.Normal)
    if kind in WITH_BADGE:
        _badge(painter, area, colour, background)
    painter.restore()


def why_pixmap(kind: str, size: int, background: QColor) -> QPixmap:
    """The icon of a kind of conflict as a picture (the legend)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_why(painter, QRectF(0, 0, size, size), kind, background)
    painter.end()
    return pixmap


def mark_icon(state: str) -> QIcon:
    return theme_icon(CONFLICT_ICON if state == CONFLICT else WAITING_ICON) if state else QIcon()


def paint_mark(painter: QPainter, left: int, centre: int, state: str, size: int = MARK_SIZE) -> int:
    """Draw an item's mark with its left edge at `left`, centred on `centre`; returns the room it took (0 without a mark)."""
    if not state:
        return 0
    mark_icon(state).paint(painter, QRect(left, centre - size // 2, size, size))
    return size + MARK_GAP


class SyncMarks(QObject):
    """The open sync conflicts and the changes that wait for the servers, as the engine tells them."""

    changed = Signal()

    def __init__(self, backend: Backend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.conflicts: tuple[ConflictSummary, ...] = ()
        self.waiting_count: int = 0
        self._conflicted: set[str] = set()
        self._waiting: set[str] = set()
        self._asking: bool = False
        self._again: bool = False
        backend.changed.connect(self._fact)
        self.refresh()

    def state(self, item: str) -> str:
        """An item's mark: CONFLICT, WAITING or ""."""
        if item in self._conflicted:
            return CONFLICT
        return WAITING if item in self._waiting else ""

    def _fact(self, change: Change) -> None:
        if str(change.kind) in FACTS:
            self.refresh()

    def refresh(self) -> None:
        """Ask the engine again (one question at a time; a fact meanwhile asks once more when it is answered)."""
        if self._asking:
            self._again = True
            return
        self._asking = True
        answers: dict[str, object] = {}

        def done() -> None:
            self._asking = False
            listed, waiting = answers.get("conflicts"), answers.get("waiting")
            if isinstance(listed, ConflictList):
                self.conflicts = listed.conflicts
                self._conflicted = {c.item for c in listed.conflicts if c.item}
            if isinstance(waiting, WaitingList):
                self.waiting_count = sum(account.changes for account in waiting.accounts)
                self._waiting = {item for account in waiting.accounts for item in account.items}
            self.changed.emit()
            if self._again:
                self._again = False
                self.refresh()

        def conflicts(result: ConflictList) -> None:
            answers["conflicts"] = result
            self._backend.ask(SYNC_WAITING, NoParameters(), waiting, failed, owner=self)

        def waiting(result: WaitingList) -> None:
            answers["waiting"] = result
            done()

        def failed(error: ChronoMailError) -> None:
            done()  # what was known stays; the next fact asks again

        self._backend.ask(CONFLICT_LIST, NoParameters(), conflicts, failed, owner=self)
