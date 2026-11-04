from collections.abc import Callable
from dataclasses import dataclass

from typing import override

from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QScrollArea, QVBoxLayout, QWidget

from chrono_mail.engine.invitations import InvitationInfo
from chrono_mail.ui import scale, theme
from chrono_mail.ui.icons import application_icon
from chrono_mail.ui.invitationcard import InvitationCard
from chrono_mail.ui.linkhint import StatusLine
from chrono_mail.ui.scale import px_for
from chrono_mail.ui.windowstate import restore_geometry, save_geometry

# --- Version ------------------------------------------------------------
__version__ = "invitationwindow.py 4.4.508"

# --- Version history ----------------------------------------------------
# v4.4.508: CalendarSource.item: the one item of a text with several (D-310)
# v4.4.507: Initial: a calendar attachment or file as a card in a window of its own (D-310 step 4)


@dataclass(frozen=True, slots=True)
class CalendarSource:
    """Where a calendar message on a card comes from: a mail's own, one of its attachments (part), or a file."""

    account: str = ""
    folder: str = ""
    message: str = ""
    part: int = -1
    file: str = ""
    item: int = -1  # in a text with several items, the one meant


def is_calendar_file(name: str) -> bool:
    """Whether a file's name makes it one to show as a card (.ics), whatever the case."""
    return name.lower().endswith(".ics")


class InvitationWindow(QMainWindow):
    """A calendar attachment or file as a card, not modal: the reading pane answers its buttons as it does those of a card in a mail."""

    def __init__(self, info: InvitationInfo, title: str, open_link: Callable[[QUrl], None], settings: QSettings | None = None) -> None:
        super().__init__()
        self._settings: QSettings | None = settings
        self.setWindowIcon(application_icon())
        self.setWindowTitle(title)
        self.resize(640, 520)
        restore_geometry(self, settings, "invitation_window_geometry")
        self.setStyleSheet(theme.stylesheet(scale.windows()))  # as a message window: a window of its own does not inherit the main window's look
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        holder = QWidget(area)
        rows = QVBoxLayout(holder)
        margin = px_for(self, 16)
        rows.setContentsMargins(margin, margin, margin, margin)
        self.card = InvitationCard(holder)
        rows.addWidget(self.card)
        rows.addStretch(1)
        area.setWidget(holder)
        self.setCentralWidget(area)
        self.card.link_activated.connect(open_link)
        self.status_line = StatusLine(QLabel(self), self)  # where the organizer's address leads (D-215)
        self.statusBar().addWidget(self.status_line, 1)
        self.card.link_hovered.connect(self.status_line.show_link)
        close = QAction("Close", self)
        close.setShortcut(QKeySequence("Esc"))
        close.triggered.connect(self.close)
        self.addAction(close)
        self.card.show_invitation(info)

    @property
    def invitation(self) -> InvitationInfo | None:
        """The card on screen."""
        return self.card.info

    def show_card(self, info: InvitationInfo) -> None:
        self.card.show_invitation(info)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        save_geometry(self, self._settings, "invitation_window_geometry")
        super().closeEvent(event)
