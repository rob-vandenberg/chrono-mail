from collections.abc import Callable
from typing import override

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QCursor, QEnterEvent, QKeyEvent
from PySide6.QtWidgets import QMessageBox, QToolButton, QToolTip, QWidget

from chrono_mail.ui.icons import accent_icon
from chrono_mail.ui.plainboxes import plain_tip

# --- Version ------------------------------------------------------------
__version__ = "infomark.py 4.2.431"

# --- Version history ----------------------------------------------------
# v4.2.431: the options an (i) explains have no hover text while it shows, their own back when it goes (the owner, D-294)
# v4.2.424: Initial: the (i) after a greyed option, which says why: at once at a hover, in a small window at a click or Enter (D-294)


class InfoMark(QToolButton):
    """The (i) right after an option that is greyed for a reason. It is shown only while there is a reason and is never greyed itself: a hover
    shows the reason at once, a click (or Enter or Space, Tab reaches it) opens it in a small window (D-294). While it is shown the options it
    explains have no hover text: one or the other, never both (the owner); when it goes, their own hover text comes back."""

    def __init__(self, parent: QWidget | None = None, keep_place: bool = False) -> None:
        super().__init__(parent)
        self.title: str = ""  # the option it explains, the title of its window
        self.reason: str = ""
        self.setIcon(accent_icon("help-about"))
        size = self.fontMetrics().height()
        self.setIconSize(QSize(size, size))
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Why is this not available?")
        if keep_place:  # rows below each other stay lined up whether or not theirs is shown
            policy = self.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            self.setSizePolicy(policy)
        self.clicked.connect(self.open)
        self.setVisible(False)
        self._own: list[tuple[QWidget, str]] = []  # the options it explains, each with its own hover text (kept while the (i) shows)

    def explain(self, title: str, reason: str, *options: QWidget) -> None:
        """The option this (i) belongs to and why it is greyed; no reason: the (i) is not shown. The options it explains lose their hover text
        while it shows and get their own back when it goes (D-294)."""
        self.title = title
        self.reason = reason
        self.setToolTip(plain_tip(reason))
        self.setVisible(bool(reason))
        for option in options:
            if all(known is not option for known, _ in self._own):
                self._own.append((option, option.toolTip()))
        for option, own in self._own:
            option.setToolTip("" if reason else own)

    def open(self) -> None:
        """The reason in a small window with OK (plain text: a reason may hold a server's or a file's words)."""
        if self.reason:
            QMessageBox.information(self.window(), self.title or "Not available", self.reason)

    @override
    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        if self.reason:
            QToolTip.showText(QCursor.pos(), self.toolTip(), self)  # at once, without the usual wait

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open()  # Space works as on every button; Enter too, since the (i) is no default button of its window
            return
        super().keyPressEvent(event)

    @override
    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and not self.reason:
            return True
        return super().event(event)


def info_marks(*names: str, keep_place: bool = False) -> dict[str, Callable[[QWidget], QWidget]]:
    """Placeholders of a form (by name) that become an (i) each (forms.load's own parts)."""

    def make(parent: QWidget) -> QWidget:
        return InfoMark(parent, keep_place)

    return dict.fromkeys(names, make)
