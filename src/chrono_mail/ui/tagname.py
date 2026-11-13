"""A tag's name typed by the person never holds a vertical bar, which marks a tag of a Google calendar (D-341): a typed bar is refused with a calm
line beside the field, a pasted one is left out without a word."""

from typing import override

from PySide6.QtCore import QEvent, QObject, QPoint
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QToolTip, QWidget

from chrono_mail.core.tagnames import BAR, BAR_REFUSED

# --- Version ------------------------------------------------------------
__version__ = "tagname.py 4.4.602"

# --- Version history ----------------------------------------------------
# v4.4.602: Initial version: no vertical bar in a tag's name, in New tag… and in Settings > Tags (D-341)


class NoBar(QObject):
    """Keeps the bar out of one field. told: the last line said (empty: none), for the tests."""

    def __init__(self, edit: QLineEdit) -> None:
        super().__init__(edit)
        self._edit = edit
        self._before = edit.text().replace(BAR, "")
        self.told = ""
        edit.textChanged.connect(self._changed)
        edit.textEdited.connect(self._edited)

    def _changed(self, text: str) -> None:
        if BAR not in text:
            self._before = text

    def _edited(self, text: str) -> None:
        if BAR not in text:
            return
        typed = len(text) == len(self._before) + 1 and text.count(BAR) == self._before.count(BAR) + 1  # one key: a bar typed
        position = self._edit.cursorPosition()
        gone = text[:position].count(BAR)
        self._edit.setText(text.replace(BAR, ""))
        self._edit.setCursorPosition(max(0, position - gone))
        if typed:
            self.told = BAR_REFUSED
            QToolTip.showText(self._edit.mapToGlobal(QPoint(0, self._edit.height())), BAR_REFUSED, self._edit)


def keep_bar_out(edit: QLineEdit) -> None:
    """The field takes no bar from now on (once per field)."""
    if edit.findChild(NoBar) is None:
        NoBar(edit)


class _Opening(QObject):
    """While a name is asked: the field of the box that opens takes no bar."""

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(watched, QInputDialog):
            edit = watched.findChild(QLineEdit)
            if edit is not None:
                keep_bar_out(edit)
        return False


def ask_tag_name(parent: QWidget | None, title: str, label: str, text: str = "") -> tuple[str, bool]:
    """(the name typed, whether OK was chosen), as QInputDialog.getText answers, in Qt's own box, with no bar in the name."""
    application = QApplication.instance()
    watch = _Opening()
    if application is not None:
        application.installEventFilter(watch)
    try:
        name, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, text)
    finally:
        if application is not None:
            application.removeEventFilter(watch)
    return name.replace(BAR, ""), ok
