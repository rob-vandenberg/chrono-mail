import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap

# --- Version ------------------------------------------------------------
__version__ = "dragcursors.py 0.2.103"

# --- Version history ----------------------------------------------------
# v0.2.103: Initial drag cursors: an arrow with a move sign over a folder that can take the drop, an arrow with a red cross elsewhere

STANDARD_SIZE: int = 24  # pixels, when the desktop does not say how large its cursor is
ARROW: tuple[tuple[float, float], ...] = ((0, 0), (0, 16), (4.2, 12.3), (7, 18.5), (9.6, 17.4), (6.9, 11.3), (12, 11.3))  # on a grid of 24
RED: QColor = QColor("#e0302c")


def cursor_size() -> int:
    """The size of the desktop's cursor (XCURSOR_SIZE on Linux), so a drag cursor is as large as the normal arrow."""
    try:
        size = int(os.environ.get("XCURSOR_SIZE", ""))
    except ValueError:
        return STANDARD_SIZE
    return size if 16 <= size <= 256 else STANDARD_SIZE


def _canvas(size: int) -> tuple[QPixmap, QPainter]:
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)  # drawn on a grid of 24
    return pixmap, painter


def _arrow(painter: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(QPointF(*ARROW[0]) + QPointF(1, 1))
    for x, y in ARROW[1:]:
        path.lineTo(QPointF(x, y) + QPointF(1, 1))
    path.closeSubpath()
    painter.setPen(QPen(QColor("white"), 1.2))
    painter.setBrush(QColor("black"))
    painter.drawPath(path)


def move_cursor(size: int | None = None) -> QPixmap:
    """An arrow with a small move sign (four arrow heads): here the messages can be dropped."""
    pixmap, painter = _canvas(size or cursor_size())
    _arrow(painter)
    centre = QPointF(17.5, 17.5)
    for pen in (QPen(QColor("white"), 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap), QPen(QColor("black"), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)):
        painter.setPen(pen)
        painter.drawLine(centre + QPointF(-5, 0), centre + QPointF(5, 0))
        painter.drawLine(centre + QPointF(0, -5), centre + QPointF(0, 5))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tip = centre + QPointF(5 * dx, 5 * dy)
            side = QPointF(-2 * dx + 2 * dy, -2 * dy + 2 * dx)
            other = QPointF(-2 * dx - 2 * dy, -2 * dy - 2 * dx)
            painter.drawLine(tip, tip + side)
            painter.drawLine(tip, tip + other)
    painter.end()
    return pixmap


def no_drop_cursor(size: int | None = None) -> QPixmap:
    """An arrow with a small red cross: the messages cannot be dropped here."""
    pixmap, painter = _canvas(size or cursor_size())
    _arrow(painter)
    box = QRectF(13, 13, 9, 9)
    for pen in (QPen(QColor("white"), 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap), QPen(RED, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)):
        painter.setPen(pen)
        painter.drawLine(box.topLeft(), box.bottomRight())
        painter.drawLine(box.topRight(), box.bottomLeft())
    painter.end()
    return pixmap
