from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QKeySequenceEdit, QWidget

# --- Version ------------------------------------------------------------
__version__ = "keyfield.py 4.4.581"

# --- Version history ----------------------------------------------------
# v4.4.581: Initial: a shortcut field with a ✕ that clears it; Backspace and Delete clear it too (the owner)


class KeyField(QKeySequenceEdit):
    """A field that takes the next keys pressed as a shortcut; the ✕ in it, Backspace or Delete leave it empty (no shortcut)."""

    def __init__(self, keys: QKeySequence, parent: QWidget | None = None) -> None:
        super().__init__(keys, parent)
        self.setClearButtonEnabled(True)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt's name
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.clear()  # never a shortcut of its own: they empty the field
            event.accept()
            return
        super().keyPressEvent(event)
