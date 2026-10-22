from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QWidget

from chrono_mail.ui.icons import theme_icon

# --- Version ------------------------------------------------------------
__version__ = "linkhint.py 1.5.257"

# --- Version history ----------------------------------------------------
# v1.5.257: Initial: the left part of a status bar shows its own text, or, while the pointer is on a link in a message, where that
#           link leads, with the orange triangle when it leads to another main domain than the sender's (D-215)

WARNING_ICON: str = "emblem-warning"  # Breeze: the orange triangle with "!"


class StatusLine(QStackedWidget):
    """The left part of a status bar: its own text (a widget of the window, never changed here), or, while the pointer is on a
    link, the link's address. Leaving the link shows the window's text again: it never went away."""

    def __init__(self, normal: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.normal: QWidget = normal
        self.addWidget(normal)
        self.link = QWidget(self)
        layout = QHBoxLayout(self.link)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.warning = QLabel(self.link)
        self.warning.setToolTip("This link leads to another domain than the sender's")
        self.address = QLabel(self.link)
        self.address.setTextFormat(Qt.TextFormat.PlainText)  # an address from a message is hostile input: never markup
        self.address.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # a long address is cut off, it never widens the window
        layout.addWidget(self.warning)
        layout.addWidget(self.address, 1)
        self.addWidget(self.link)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def show_link(self, address: str, elsewhere: bool) -> None:
        """The pointer is on a link (its address, and whether it leads to another main domain than the sender's), or left it ("")."""
        if not address:
            self.setCurrentWidget(self.normal)
            return
        if elsewhere:
            side = self.address.fontMetrics().height()
            self.warning.setPixmap(theme_icon(WARNING_ICON).pixmap(QSize(side, side), self.devicePixelRatioF()))
        self.warning.setVisible(elsewhere)
        self.address.setText(address)
        self.setCurrentWidget(self.link)
