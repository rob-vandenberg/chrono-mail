from typing import override

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

# --- Version ------------------------------------------------------------
__version__ = "plainboxes.py 3.4.344"

# --- Version history ----------------------------------------------------
# v3.4.344: Initial: every message box shows plain text unless it says otherwise (RV-167, D-259)


class PlainMessageBoxes(QObject):
    """Message boxes show their text as plain text. A box guesses by itself (Qt's AutoText) and takes text with "<...>" for markup,
    while it often holds a folder name, a subject or a server's reply: hostile input that could change what the box shows. A box that
    wants markup says so (setTextFormat); this touches only the ones that guess."""

    @override
    def eventFilter(self, watched: QObject, event: QEvent, /) -> bool:
        if event.type() == QEvent.Type.Polish and isinstance(watched, QMessageBox) and watched.textFormat() == Qt.TextFormat.AutoText:
            watched.setTextFormat(Qt.TextFormat.PlainText)
        return False


def plain_message_boxes(app: QApplication) -> PlainMessageBoxes:
    """Install it for the whole program (once, at start)."""
    boxes = PlainMessageBoxes(app)
    app.installEventFilter(boxes)
    return boxes
