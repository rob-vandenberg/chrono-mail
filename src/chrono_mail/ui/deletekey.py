from collections.abc import Callable
from typing import override

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QWidget

# --- Version ------------------------------------------------------------
__version__ = "deletekey.py 1.7.262"

# --- Version history ----------------------------------------------------
# v1.7.262: Initial: a list that has the keyboard claims Delete and Shift+Delete for what is selected in it (R-43, D-217)


def _is_delete(event: QEvent) -> bool:
    if not isinstance(event, QKeyEvent) or event.key() != Qt.Key.Key_Delete:
        return False
    return event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier)


class DeleteKey(QObject):
    """Delete and Shift+Delete, pressed while this widget has the keyboard, act on what is selected in it (a folder, a calendar),
    never on what is selected in another part of the window. The widget claims the key before the window's own Delete sees it,
    as a text field claims the keys it uses (Qt's ShortcutOverride); a mouse click on the window's Delete button is not a key and
    is not touched. The filter watches only this one widget."""

    def __init__(self, widget: QWidget, pressed: Callable[[], None]) -> None:
        super().__init__(widget)
        self._pressed: Callable[[], None] = pressed
        widget.installEventFilter(self)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not _is_delete(event):
            return False
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()  # the key is this widget's: the window's Delete does not take it
            return True
        if event.type() == QEvent.Type.KeyPress:
            self._pressed()
            return True
        return False
