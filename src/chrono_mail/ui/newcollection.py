from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLineEdit, QPushButton, QWidget

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.commands_accounts import NoParameters
from chrono_mail.engine.commands_pim import COLLECTION_ACCOUNTS, PlaceList
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.forms import part

# --- Version ------------------------------------------------------------
__version__ = "newcollection.py 4.0.378"

# --- Version history ----------------------------------------------------
# v4.0.378: built from its Qt Designer form (ui/forms/new_collection.ui, D-276); its smallest width follows the size factor
# v1.1.236: Initial: the New calendar, task list or address book window with its Account, set to the account it was opened on (R-29, D-198)


class NewCollectionDialog(QDialog):
    """Name and Account of a new calendar, task list or address book. The Account is set to the account the person right-clicked (the local ones on
    this computer when nothing was), and lists the local ones and every account with a calendar and contacts server. After OK: `name`, `account`."""

    def __init__(self, backend: Backend, title: str, account: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._wanted: str = account or LOCAL_ACCOUNT_ID
        form = forms.fill(self, "new_collection")  # D-276
        self.name_field: QLineEdit = part(form, QLineEdit, "name")
        self.account_field: QComboBox = part(form, QComboBox, "account")
        self.account_field.setEnabled(False)  # greyed until the accounts are in
        self.buttons: QDialogButtonBox = part(form, QDialogButtonBox, "buttons")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        forms.smallest(self, 380)  # its texts are never cut off, also at the largest size
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
