from PySide6.QtCore import QObject, Signal, SignalInstance
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from chrono_mail.ui.icons import application_icon

# --- Version ------------------------------------------------------------
__version__ = "tray.py 0.0.56"

# --- Version history ----------------------------------------------------
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.21: Initial notification area icon and new mail notifications

NOTIFICATION_MILLISECONDS: int = 6000
TITLE: str = "Chrono Mail"


class TrayController(QObject):
    """The icon in the notification area (system tray) with its menu, and the new mail notifications.
    Where the desktop has no tray the icon is simply not there and everything else keeps working."""

    toggle_requested = Signal()
    check_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.available: bool = QSystemTrayIcon.isSystemTrayAvailable()
        self._icon: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None
        self._unread: int = 0
        if not self.available:
            return
        self._icon = QSystemTrayIcon(application_icon(), self)
        self._menu = QMenu(parent)
        self.show_action = self._menu.addAction("Show or hide Chrono Mail")
        self.check_action = self._menu.addAction("Refresh")
        self.settings_action = self._menu.addAction("Settings…")
        self._menu.addSeparator()
        self.quit_action = self._menu.addAction("Quit")
        self._connect(self.show_action, self.toggle_requested)
        self._connect(self.check_action, self.check_requested)
        self._connect(self.settings_action, self.settings_requested)
        self._connect(self.quit_action, self.quit_requested)
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._activated)
        self._icon.setToolTip(TITLE)

    @staticmethod
    def _connect(action: QAction, signal: SignalInstance) -> None:
        action.triggered.connect(signal)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_requested.emit()

    @property
    def visible(self) -> bool:
        return self._icon is not None and self._icon.isVisible()

    def set_visible(self, visible: bool) -> None:
        if self._icon is not None:
            self._icon.setVisible(visible)

    def set_unread(self, unread: int) -> None:
        self._unread = unread
        if self._icon is not None:
            self._icon.setToolTip(f"{TITLE}: {unread} unread" if unread else TITLE)

    def notify(self, title: str, text: str) -> bool:
        """Tell the person something. False when there is no tray to do it with."""
        if self._icon is None or not self._icon.isVisible():
            return False
        self._icon.showMessage(title, text, application_icon(), NOTIFICATION_MILLISECONDS)
        return True
