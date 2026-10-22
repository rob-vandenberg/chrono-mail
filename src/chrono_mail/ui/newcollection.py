from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_pim import COLLECTION_ACCOUNTS, PlaceList
from chrono_mail.ui.backend import Backend

# --- Version ------------------------------------------------------------
__version__ = "newcollection.py 1.1.236"

# --- Version history ----------------------------------------------------
# v1.1.236: Initial: the New calendar, task list or address book window with its Account, set to the account it was opened on (R-29, D-198)


class NewCollectionDialog(QDialog):
    """Name and Account of a new calendar, task list or address book. The Account is set to the account the person right-clicked (the local ones on
    this computer when nothing was), and lists the local ones and every account with a calendar and contacts server. After OK: `name`, `account`."""

    def __init__(self, backend: Backend, title: str, account: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self._wanted: str = account or LOCAL_ACCOUNT_ID
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_field = QLineEdit(self)
        self.account_field = QComboBox(self)
        self.account_field.setEnabled(False)  # greyed until the accounts are in
        form.addRow("Name:", self.name_field)
        form.addRow("Account:", self.account_field)
        layout.addLayout(form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.name_field.textChanged.connect(self._usable)
        self._usable()
        backend.ask(COLLECTION_ACCOUNTS, NoParameters(), self._listed, lambda error: None, owner=self)

    def _ok(self) -> QPushButton | None:
        return self.buttons.button(QDialogButtonBox.StandardButton.Ok)

    def _usable(self) -> None:
        button = self._ok()
        if button is not None:
            button.setEnabled(bool(self.name_field.text().strip()) and self.account_field.count() > 0)

    def _listed(self, places: PlaceList) -> None:
        for place in places.places:
            self.account_field.addItem(place.name, place.account)
        found = self.account_field.findData(self._wanted)
        self.account_field.setCurrentIndex(found if found >= 0 else 0)
        self.account_field.setEnabled(True)
        self._usable()

    @property
    def name(self) -> str:
        return self.name_field.text().strip()

    @property
    def account(self) -> str:
        return str(self.account_field.currentData() or LOCAL_ACCOUNT_ID)
