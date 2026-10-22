from collections.abc import Callable
from pathlib import Path

from typing import override

from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QToolBar, QWidget

from chrono_mail.engine.commands_mail import ShowResult
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_mail import MESSAGE_ATTACHMENT_SAVE, AttachmentSaveParameters
from chrono_mail.ui import scale, theme
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.icons import application_icon, theme_icon
from chrono_mail.ui.linkhint import StatusLine
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.windowstate import restore_geometry, save_geometry

# --- Version ------------------------------------------------------------
__version__ = "messagewindow.py 1.9.275"

# --- Version history ----------------------------------------------------
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.270: R-19: saving an attachment is a typed call
# v1.5.257: The bar at the bottom shows where the link under the pointer leads, with the orange triangle for another main domain (D-215)
# v0.5.144: Its look at the size of the Other windows and dialogs slider
# v0.0.83: The size and position of the main window, the message windows, the composers and the Settings window are remembered and only used when the window would be reachable on a connected screen
# v0.0.77: Message list and reading pane: Received instead of Date, blue unread dot (empty circle when read), flat list header, reading pane header and banner
# v0.0.74: A message window gets the privacy settings before it shows the message and saves Always for this sender
# v0.0.53: Standard keyboard shortcuts
# v0.0.41: Initial window for reading one message

Compose = Callable[[str, str, str, str], None]  # kind, account, folder, message id


class MessageWindow(QMainWindow):
    """One message in a window of its own, with Reply, Reply all and Forward."""

    def __init__(
        self, backend: Backend, account: str, folder: str, message_id: str, shown: ShowResult, compose: Compose, open_link: Callable[[QUrl], None], prepare: Callable[[ReaderWidget], None], settings: QSettings | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._where: tuple[str, str, str] = (account, folder, message_id)
        self.setWindowIcon(application_icon())
        self.setWindowTitle(shown.subject or "(no subject)")
        self.resize(820, 640)
        self._settings: QSettings | None = settings
        restore_geometry(self, settings, "message_window_geometry")
        self.setStyleSheet(theme.stylesheet(scale.windows()))  # a window of its own does not inherit the look of the main window; its sizes follow Other windows and dialogs
        self.reader = ReaderWidget(self)
        self.setCentralWidget(self.reader)
        prepare(self.reader)  # the privacy settings come before the message: what is allowed decides what is loaded
        self.reader.show_message(shown)
        toolbar = QToolBar("Message", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for kind, label, icon, keys in (
            ("reply", "Reply", "mail-reply-sender", "Ctrl+R"), ("reply_all", "Reply all", "mail-reply-all", "Ctrl+Shift+R"), ("forward", "Forward", "mail-forward", "Ctrl+F"),
        ):
            action = toolbar.addAction(theme_icon(icon), label)
            action.setShortcut(QKeySequence(keys))  # the same keys as in the main window
            action.triggered.connect(lambda checked=False, chosen=kind: compose(chosen, account, folder, message_id))
        close = QAction("Close", self)
        close.setShortcut(QKeySequence("Esc"))
        close.triggered.connect(self.close)
        self.addAction(close)
        self.reader.link_activated.connect(open_link)
        self.status_line = StatusLine(QLabel(self), self)  # where the link under the pointer leads (D-215)
        self.statusBar().addWidget(self.status_line, 1)
        self.reader.link_hovered.connect(self.status_line.show_link)
        self.reader.attachment_save_requested.connect(self._save_attachment)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        save_geometry(self, self._settings, "message_window_geometry")
        super().closeEvent(event)

    def _save_attachment(self, position: int, suggested: str) -> None:
        account, folder, message_id = self._where
        downloads = Path.home() / "Downloads"
        start = str((downloads if downloads.is_dir() else Path.home()) / suggested)
        path, _ = QFileDialog.getSaveFileName(self, "Save attachment", start)
        if not path:
            return

        def failed(error: ChronoMailError) -> None:
            self.statusBar().showMessage(f"Could not save the attachment: {error}", 8000)

        self._backend.ask(
            MESSAGE_ATTACHMENT_SAVE, AttachmentSaveParameters(account, folder, message_id, position, path, overwrite=True),
            lambda result: self.statusBar().showMessage(f"Saved {result.path}", 8000), failed, owner=self,
        )
