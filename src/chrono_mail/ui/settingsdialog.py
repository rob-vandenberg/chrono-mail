from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.core import ChronoMailError
from chrono_mail.ui.accountedit import AccountEditDialog
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "settingsdialog.py 0.0.31"

# --- Version history ----------------------------------------------------
# v0.0.31: the account type is shown
# v0.0.24: Rich text composing
# v0.0.21: Tray icon, new mail notifications, unread count in the title
# v0.0.20: Initial settings window

ROLE_ACCOUNT: int = 256


@dataclass(frozen=True, slots=True)
class GeneralSettings:
    auto_check: bool = True
    auto_check_minutes: int = 5
    conversations: bool = False
    notifications: bool = True
    tray: bool = True
    close_to_tray: bool = False
    compose_html: bool = False


class SettingsDialog(QDialog):
    """The settings window: how the program behaves (General) and the accounts (add, change, remove)."""

    accounts_changed = Signal()
    add_account_requested = Signal()

    def __init__(self, backend: Backend, general: GeneralSettings, tray_available: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self.setWindowTitle("Settings")
        self.setMinimumSize(520, 380)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        page = QWidget(self)
        form = QFormLayout(page)
        self.auto_check = QCheckBox("Check for new mail automatically", page)
        self.auto_check.setChecked(general.auto_check)
        self.interval = QSpinBox(page)
        self.interval.setRange(1, 240)
        self.interval.setSuffix(" minutes")
        self.interval.setValue(general.auto_check_minutes)
        self.interval.setEnabled(general.auto_check)
        self.conversations = QCheckBox("Show messages grouped as conversations", page)
        self.conversations.setChecked(general.conversations)
        form.addRow(self.auto_check)
        form.addRow("Check every", self.interval)
        form.addRow(self.conversations)
        self.compose_html = QCheckBox("Write new messages as rich text (bold, lists, links; sent as text and HTML)", page)
        self.compose_html.setChecked(general.compose_html)
        form.addRow(self.compose_html)
        self.notifications = QCheckBox("Show a notification when new mail arrives (only the number, never the content)", page)
        self.notifications.setChecked(general.notifications)
        self.tray = QCheckBox("Show an icon in the notification area", page)
        self.tray.setChecked(general.tray and tray_available)
        self.close_to_tray = QCheckBox("Closing the window keeps Chrono Mail running in the notification area", page)
        self.close_to_tray.setChecked(general.close_to_tray and tray_available)
        for widget in (self.notifications, self.tray, self.close_to_tray):
            form.addRow(widget)
        if not tray_available:
            self.tray.setEnabled(False)
            self.close_to_tray.setEnabled(False)
            self.notifications.setEnabled(False)
            form.addRow(QLabel("This desktop has no notification area, so there are no icon and no notifications.", page))
        self.tray.toggled.connect(self._tray_toggled)
        self._tray_toggled(self.tray.isChecked())
        self.auto_check.toggled.connect(self.interval.setEnabled)
        self.tabs.addTab(page, "General")

        accounts_page = QWidget(self)
        accounts_layout = QHBoxLayout(accounts_page)
        self.accounts = QListWidget(accounts_page)
        accounts_layout.addWidget(self.accounts, 1)
        side = QVBoxLayout()
        self.add_button = QPushButton("Add…", accounts_page)
        self.edit_button = QPushButton("Change…", accounts_page)
        self.remove_button = QPushButton("Remove…", accounts_page)
        for button in (self.add_button, self.edit_button, self.remove_button):
            side.addWidget(button)
        side.addStretch(1)
        accounts_layout.addLayout(side)
        self.tabs.addTab(accounts_page, "Accounts")
        self.status = QLabel("", self)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.add_button.clicked.connect(self.add_account_requested)
        self.edit_button.clicked.connect(self.edit_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.accounts.itemDoubleClicked.connect(self._double_clicked)
        self.accounts.currentItemChanged.connect(self._selection_changed)
        self.reload()

    def _tray_toggled(self, on: bool) -> None:
        if not on:
            self.close_to_tray.setChecked(False)
        self.close_to_tray.setEnabled(on and self.tray.isEnabled())

    def general(self) -> GeneralSettings:
        return GeneralSettings(
            self.auto_check.isChecked(), self.interval.value(), self.conversations.isChecked(),
            self.notifications.isChecked(), self.tray.isChecked(), self.close_to_tray.isChecked(), self.compose_html.isChecked(),
        )

    # --- accounts -----------------------------------------------------------------------------------------

    def reload(self) -> None:
        """Show the accounts as they are now."""
        selected = self._selected_id()

        def listed(result: object) -> None:
            self.accounts.clear()
            for account in list(result) if isinstance(result, (tuple, list)) else []:
                kind = "IMAP" if str(getattr(account, "kind", "pop3")) == "imap" else "POP3"
                item = QListWidgetItem(f"{getattr(account, 'display_name')}  <{getattr(account, 'email_address')}>  ({kind})")
                item.setData(ROLE_ACCOUNT, account)
                self.accounts.addItem(item)
                if str(getattr(account, "account_id")) == selected:
                    self.accounts.setCurrentItem(item)
            if self.accounts.count() and self.accounts.currentRow() < 0:
                self.accounts.setCurrentRow(0)
            self._selection_changed()

        self._backend.call("account.list", {}, listed, lambda error: self.status.setText(f"The accounts could not be listed: {error}"), owner=self)

    def _selected(self) -> object | None:
        item = self.accounts.currentItem()
        return None if item is None else item.data(ROLE_ACCOUNT)

    def _selected_id(self) -> str | None:
        account = self._selected()
        return None if account is None else str(getattr(account, "account_id"))

    def _selection_changed(self) -> None:
        has = self._selected() is not None
        self.edit_button.setEnabled(has)
        self.remove_button.setEnabled(has)

    def _double_clicked(self, item: QListWidgetItem) -> None:
        self.edit_selected()

    def edit_selected(self) -> None:
        account = self._selected()
        if account is None:
            return
        dialog = AccountEditDialog(self._backend, account, self)
        if dialog.exec() == AccountEditDialog.DialogCode.Accepted:
            self.status.setText("Account saved")
            self.reload()
            self.accounts_changed.emit()

    def remove_selected(self) -> None:
        account = self._selected()
        if account is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove account",
            f"Remove the account {getattr(account, 'email_address')} from Chrono Mail?\n\n"
            "The program forgets the account and its passwords. The mail stored on this computer is not deleted, "
            "and nothing is removed from the server.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(result: object) -> None:
            self.status.setText("Account removed")
            self.reload()
            self.accounts_changed.emit()

        def failed(error: ChronoMailError) -> None:
            QMessageBox.warning(self, "Remove account", f"The account could not be removed: {error}")

        self._backend.call("account.remove", {"account": str(getattr(account, "account_id"))}, done, failed, owner=self)
