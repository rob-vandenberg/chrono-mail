from collections.abc import Callable
from pathlib import Path

from typing import override

from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMainWindow, QToolBar, QWidget

from chrono_mail.core import ChronoMailError
from chrono_mail.ui import scale, theme
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.icons import application_icon, theme_icon
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.windowstate import restore_geometry, save_geometry

# --- Version ------------------------------------------------------------
__version__ = "messagewindow.py 0.5.144"

# --- Version history ----------------------------------------------------
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
        self, backend: Backend, account: str, folder: str, message_id: str, shown: object, compose: Compose, open_link: Callable[[QUrl], None], prepare: Callable[[ReaderWidget], None], settings: QSettings | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._where: tuple[str, str, str] = (account, folder, message_id)
        self.setWindowIcon(application_icon())
        self.setWindowTitle(str(getattr(shown, "subject")) or "(no subject)")
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

        self._backend.call(
            "message.attachment.save", {"account": account, "folder": folder, "message": message_id, "index": position, "path": path, "overwrite": True},
            lambda result: self.statusBar().showMessage(f"Saved {getattr(result, 'path')}", 8000), failed, owner=self,
        )
