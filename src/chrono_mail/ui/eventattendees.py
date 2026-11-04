from dataclasses import dataclass
from email.utils import getaddresses
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget, QPushButton, QWidget

from chrono_mail.ui import forms
from chrono_mail.ui.addresscompleter import AddressCompleter
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.colours import red
from chrono_mail.ui.forms import part
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "eventattendees.py 4.4.535"

# --- Version history ----------------------------------------------------
# v4.4.535: Initial version: the attendees of an event are added and removed in a dialog of their own (round 4)


@dataclass(frozen=True, slots=True)
class Person:
    """An attendee as the window shows it: the name when it is known, else the address. Given: what goes to the engine."""

    address: str
    name: str = ""
    given: str = ""

    @property
    def shown(self) -> str:
        return self.name or self.address


class AttendeesDialog(QDialog):
    """Adds and removes the attendees of an event; only OK changes the event."""

    def __init__(self, backend: Backend, people: list[Person], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = forms.fill(self, "event_attendees")  # D-276
        self.people: list[Person] = list(people)
        self.address: QLineEdit = part(form, QLineEdit, "address")
        self.completer_of_address = AddressCompleter(backend, self.address)
        self.add_button: QPushButton = part(form, QPushButton, "add")
        self.list: QListWidget = part(form, QListWidget, "people")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.remove_button: QPushButton = part(form, QPushButton, "remove")
        self.result_label: QLabel = part(form, QLabel, "result")
        buttons = part(form, QDialogButtonBox, "buttons")
        buttons.accepted.connect(self._finish)
        buttons.rejected.connect(self.reject)
        self.address.returnPressed.connect(self.add_typed)
        self.add_button.clicked.connect(self.add_typed)
        self.remove_button.clicked.connect(self.remove_chosen)
        self.list.itemSelectionChanged.connect(self._follow_selection)
        forms.smallest(self, wpx(420))
        self._show()

    @override
    def keyPressEvent(self, arg__1: QKeyEvent, /) -> None:
        if arg__1.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):  # Enter in the field adds the address, it never closes the dialog
            super().keyPressEvent(arg__1)

    def _show(self) -> None:
        self.list.clear()
        self.list.addItems([person.shown for person in self.people])
        self._follow_selection()

    def _follow_selection(self) -> None:
        self.remove_button.setEnabled(bool(self.list.selectedItems()))

    def add_typed(self) -> bool:
        """What is in the field joins the list, a pasted row of addresses included. Nothing joins when one of them is no address."""
        found = [(name, address.strip()) for name, address in getaddresses([self.address.text()]) if address.strip()]
        wrong = next((address for _name, address in found if "@" not in address), None)
        if wrong is not None:
            self.result_label.setText(f"<span style='color:{red()}'>That is not an e-mail address yet.</span>")
            return False
        taken = {person.address.lower() for person in self.people}
        for name, address in found:
            if address.lower() not in taken:
                taken.add(address.lower())
                self.people.append(Person(address, name, f'"{name}" <{address}>' if name else address))
        self.address.clear()
        self.result_label.clear()
        self._show()
        return True

    def remove_chosen(self) -> None:
        rows = {self.list.row(item) for item in self.list.selectedItems()}
        self.people = [person for row, person in enumerate(self.people) if row not in rows]
        self._show()

    def _finish(self) -> None:
        if self.add_typed():  # an address typed and not added yet is what the person meant
            self.accept()
