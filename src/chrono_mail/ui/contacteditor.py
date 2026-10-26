import html
from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chrono_mail.engine.commands_addressbook import AddressInfo
from chrono_mail.engine.commands_pim import CollectionSummary
from chrono_mail.core import ChronoMailError
from chrono_mail.engine.commands_addressbook import CONTACT_ADD, CONTACT_EDIT, ContactInfo
from chrono_mail.engine.commands_addressbook import AddParameters as ContactAdd
from chrono_mail.engine.commands_addressbook import EditParameters as ContactEdit
from chrono_mail.ui import forms
from chrono_mail.ui.backend import Backend, carried
from chrono_mail.ui.colours import red
from chrono_mail.ui.forms import part
from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "contacteditor.py 4.0.378"

# --- Version history ----------------------------------------------------
# v4.0.378: the contact editor, a list of addresses or numbers, one entry of it and one address are built from their Qt Designer forms
#           (ui/forms/contact_editor.ui, contact_list.ui, contact_entry.ui, contact_address.ui, D-276); Tab goes through the entries and
#           addresses where they are on screen (they came after Save and Cancel); the sizes follow the size factor
# v3.6.350: an error text inside markup is escaped: a server's or a file's words are never drawn as markup (D-259)
# v3.2.342: a list of e-mail addresses, phone numbers, web or postal addresses is sent only when it changed; an entry without a kind keeps none (RV-20)
# v3.0.339: a birthday without a year is shown (month and day) and kept; the birthday is sent only when it was changed (RV-16)
# v1.9.275: R-19 round 5: the engine's answers kept as their real types and read by their fields (Pyrefly checks them); a getattr stays only with its reason (# getattr:)
# v1.9.273: R-19: parameters gives the typed edit (EditParameters); add and edit are typed calls; the contact is a ContactInfo
# v0.0.62: Initial editor for a contact

EMAIL_TYPES: tuple[str, ...] = ("work", "home", "other")
PHONE_TYPES: tuple[str, ...] = ("cell", "work", "home", "fax", "other")
URL_TYPES: tuple[str, ...] = ("work", "home", "other")
ADDRESS_TYPES: tuple[str, ...] = ("work", "home", "other")


class LabeledRow(QWidget):
    """One e-mail address, phone number or web address with its kind."""

    def __init__(self, types: tuple[str, ...], placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = forms.fill(self, "contact_entry")  # the kind, the value and ✕ (D-276)
        self.kind: QComboBox = part(form, QComboBox, "kind")
        self.kind.addItems([kind.capitalize() for kind in types])
        self.kind.setEditable(True)
        self.value: QLineEdit = part(form, QLineEdit, "value")
        self.value.setPlaceholderText(placeholder)
        self.remove_button: QPushButton = part(form, QPushButton, "remove")
        self.remove_button.setFixedWidth(wpx(30))

    def tab_parts(self) -> list[QWidget]:
        """Its parts in the order Tab goes through them."""
        return [self.kind, self.value, self.remove_button]

    def set_entry(self, types: tuple[str, ...], value: str) -> None:
        first = next((kind for kind in types if kind != "internet"), "")
        if not first and value:  # a kept entry without a kind stays without one (it became Work or Mobile, RV-20)
            self.kind.setCurrentIndex(-1)
            self.kind.setEditText("")
        if first:
            index = self.kind.findText(first, Qt.MatchFlag.MatchFixedString)
            if index >= 0:
                self.kind.setCurrentIndex(index)
            else:
                self.kind.setEditText(first.capitalize())
        self.value.setText(value)

    def entry(self) -> str:
        """'work|john@example.com' as the commands take it; an empty row gives an empty text."""
        value = self.value.text().strip()
        kind = ",".join(part.strip().lower().replace("|", "").replace(",", "") for part in [self.kind.currentText()] if part.strip())
        return f"{kind}|{value}" if value else ""


class LabeledList(QGroupBox):
    """A list of rows with an Add button."""

    changed = Signal()  # a row came or went: the window puts it in its Tab order

    def __init__(self, title: str, types: tuple[str, ...], placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._types: tuple[str, ...] = types
        self._placeholder: str = placeholder
        self.rows: list[LabeledRow] = []
        form = forms.fill(self, "contact_list")  # the rows and Add (D-276)
        self._layout: QVBoxLayout = part(form, QVBoxLayout, "layout")
        self.add_button: QPushButton = part(form, QPushButton, "add")
        self.add_button.clicked.connect(lambda: self.add_row())

    def add_row(self, types: tuple[str, ...] = (), value: str = "") -> LabeledRow:
        row = LabeledRow(self._types, self._placeholder, self)
        row.set_entry(types, value)
        self.rows.append(row)
        self._layout.insertWidget(len(self.rows) - 1, row)
        row.remove_button.clicked.connect(lambda: self._remove(row))
        self.changed.emit()
        return row

    def _remove(self, row: LabeledRow) -> None:
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self.changed.emit()

    def tab_parts(self) -> list[QWidget]:
        """Its rows' parts and Add, in the order Tab goes through them."""
        return [*(widget for row in self.rows for widget in row.tab_parts()), self.add_button]

    def entries(self) -> list[str]:
        return [entry for entry in (row.entry() for row in self.rows) if entry]


class AddressRow(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        form = forms.fill(self, "contact_address")  # one address (D-276)
        self.kind: QComboBox = part(form, QComboBox, "kind")
        self.kind.addItems([kind.capitalize() for kind in ADDRESS_TYPES])
        self.kind.setEditable(True)
        self.street: QLineEdit = part(form, QLineEdit, "street")
        self.locality: QLineEdit = part(form, QLineEdit, "locality")
        self.region: QLineEdit = part(form, QLineEdit, "region")
        self.postal_code: QLineEdit = part(form, QLineEdit, "postalCode")
        self.country: QLineEdit = part(form, QLineEdit, "country")
        self.remove_button: QPushButton = part(form, QPushButton, "remove")

    def tab_parts(self) -> list[QWidget]:
        """Its parts in the order Tab goes through them."""
        return [self.kind, self.street, self.locality, self.region, self.postal_code, self.country, self.remove_button]

    def entry(self) -> str:
        parts = [self.street.text(), self.locality.text(), self.region.text(), self.postal_code.text(), self.country.text()]
        if not any(part.strip() for part in parts):
            return ""
        kind = self.kind.currentText().strip().lower().replace("|", "").replace(",", "")
        return "|".join([kind, *(part.strip().replace("|", " ") for part in parts)])


class ContactEditor(QDialog):
    """Makes a contact or changes one. Nothing is saved before OK; the commands do the saving."""

    saved = Signal(str)  # the id of the contact

    def __init__(
        self, backend: Backend, address_books: list[CollectionSummary], known_tags: list[str], contact: ContactInfo | None = None, collection: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._backend: Backend = backend
        self._original: ContactInfo | None = contact
        self._item: str | None = None if contact is None else contact.item
        self.setWindowTitle("New contact" if contact is None else f"Contact: {contact.name}")
        lists = {
            "emails": ("E-mail addresses", EMAIL_TYPES, "name@example.com"), "phones": ("Phone numbers", PHONE_TYPES, "+31 6 12345678"),
            "urls": ("Web addresses", URL_TYPES, "https://example.com"),
        }
        form = forms.fill(self, "contact_editor", {name: (lambda parent, made=made: LabeledList(*made, parent)) for name, made in lists.items()})  # D-276
        self.book: QComboBox = part(form, QComboBox, "book")
        for info in address_books:
            self.book.addItem(f"{info.account_name} — {info.name}", info.collection)
        self.book.setCurrentIndex(max(0, self.book.findData(collection)))
        self.book.setEnabled(contact is None and self.book.count() > 1)
        self.prefix: QLineEdit = part(form, QLineEdit, "prefix")
        self.given: QLineEdit = part(form, QLineEdit, "given")
        self.additional: QLineEdit = part(form, QLineEdit, "additional")
        self.family: QLineEdit = part(form, QLineEdit, "family")
        self.suffix: QLineEdit = part(form, QLineEdit, "suffix")
        self.display_name: QLineEdit = part(form, QLineEdit, "displayName")
        self.nickname: QLineEdit = part(form, QLineEdit, "nickname")
        self.organization: QLineEdit = part(form, QLineEdit, "organization")
        self.department: QLineEdit = part(form, QLineEdit, "department")
        self.title: QLineEdit = part(form, QLineEdit, "jobTitle")
        self.emails: LabeledList = part(form, LabeledList, "emails")
        self.phones: LabeledList = part(form, LabeledList, "phones")
        self.urls: LabeledList = part(form, LabeledList, "urls")
        self.address_rows: list[AddressRow] = []
        self.address_box: QVBoxLayout = part(form, QVBoxLayout, "addresses")
        self.add_address_button: QPushButton = part(form, QPushButton, "addAddress")
        self.add_address_button.clicked.connect(lambda: self.add_address())
        self.has_birthday: QCheckBox = part(form, QCheckBox, "hasBirthday")
        self.birthday: QDateEdit = part(form, QDateEdit, "birthday")
        self.birthday.setCalendarPopup(True)
        self.birthday.setDisplayFormat("yyyy-MM-dd")
        self.birthday.setDate(QDate(1980, 1, 1))
        self.birthday.setEnabled(False)
        self.has_birthday.toggled.connect(self.birthday.setEnabled)
        self._birthday_without_year = False  # a birthday kept without a year (--0415): shown and saved as month and day
        self._birthday_before = ""  # the birthday as it was: it is sent only when the person changed it (RV-16)
        self._lists_before: dict[str, tuple[str, ...]] = {}  # the lists as the contact had them: a list is sent only when it changed (RV-20)
        self.note: QPlainTextEdit = part(form, QPlainTextEdit, "note")
        self.note.setMinimumHeight(wpx(70))
        self.tags: QLineEdit = part(form, QLineEdit, "tags")
        completer = QCompleter(known_tags, self.tags)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.tags.setCompleter(completer)
        self.result_label: QLabel = part(form, QLabel, "result")
        buttons = part(form, QDialogButtonBox, "buttons")
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        for group in (self.emails, self.phones, self.urls):
            group.changed.connect(self._tab_order)
        self.setMinimumHeight(wpx(600))
        forms.smallest(self, 560)  # its texts are never cut off, also at the largest size
        if contact is not None:
            self._fill(contact)
        else:
            self.emails.add_row()
            self.phones.add_row()

    # --- filling ------------------------------------------------------------------------------

    def add_address(self, values: AddressInfo | None = None) -> AddressRow:
        row = AddressRow(self)
        if values is not None:
            kind = next(iter(values.types), "")
            if kind:
                row.kind.setEditText(kind.capitalize())
            else:  # an address without a kind stays without one (RV-20)
                row.kind.setCurrentIndex(-1)
                row.kind.setEditText("")
            row.street.setText(values.street)
            row.locality.setText(values.locality)
            row.region.setText(values.region)
            row.postal_code.setText(values.postal_code)
            row.country.setText(values.country)
        self.address_rows.append(row)
        self.address_box.addWidget(row)
        row.remove_button.clicked.connect(lambda: self._remove_address(row))
        self._tab_order()
        return row

    def _remove_address(self, row: AddressRow) -> None:
        self.address_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._tab_order()

    def _tab_order(self) -> None:
        """Tab goes through the lists and addresses where they are on screen, after the names and before the birthday (rows are made after
        the window, so they would otherwise come after Save and Cancel)."""
        anchor: QWidget = self.title
        for group in (*(group.tab_parts() for group in (self.emails, self.phones, self.urls)), *(row.tab_parts() for row in self.address_rows), [self.add_address_button]):
            for widget in group:
                QWidget.setTabOrder(anchor, widget)
                anchor = widget

    def _fill(self, contact: ContactInfo) -> None:
        self.prefix.setText(contact.prefix)
        self.given.setText(contact.given_name)
        self.additional.setText(contact.additional_names)
        self.family.setText(contact.family_name)
        self.suffix.setText(contact.suffix)
        self.display_name.setText(contact.display_name)
        self.nickname.setText(", ".join(contact.nicknames))
        self.organization.setText(contact.organization)
        self.department.setText(contact.department)
        self.title.setText(contact.title)
        for group, entries in ((self.emails, contact.emails), (self.phones, contact.phones), (self.urls, contact.urls)):
            for entry in entries:
                group.add_row(tuple(entry.types), entry.value)
        for address in contact.addresses:
            self.add_address(address)
        birthday = contact.birthday
        if len(birthday) == 10:
            self.has_birthday.setChecked(True)
            self.birthday.setDate(QDate.fromString(birthday, "yyyy-MM-dd"))
        elif len(birthday) == 6 and birthday.startswith("--") and birthday[2:].isascii() and birthday[2:].isdecimal():
            self._birthday_without_year = True  # a phone keeps birthdays without a year: shown as month and day, never lost (RV-16)
            self.birthday.setDisplayFormat("MM-dd")
            self.birthday.setDate(QDate(2000, int(birthday[2:4]), int(birthday[4:6])))  # 2000 is a leap year: 29 February fits
            self.has_birthday.setChecked(True)
        self._birthday_before = self._birthday_text()
        self._lists_before = self._lists()
        self.note.setPlainText(contact.note)
        self.tags.setText(", ".join(contact.tags))

    # --- the command --------------------------------------------------------------------------

    def _lists(self) -> dict[str, tuple[str, ...]]:
        return {
            "email": tuple(self.emails.entries()), "phone": tuple(self.phones.entries()), "url": tuple(self.urls.entries()),
            "address": tuple(entry for entry in (row.entry() for row in self.address_rows) if entry),
        }

    def parameters(self) -> ContactEdit:
        """What is in the editor, as contact.add and contact.edit take it: the fields, and the lists emptied here (clear). A list the person did
        not change is not sent: what the window does not show of its entries (more kinds, the preferred mark, a P.O. box) stays (RV-20)."""
        original = self._original
        nicknames = tuple(part.strip() for part in self.nickname.text().split(",") if part.strip())
        lists = {name: entries if original is None or entries != self._lists_before.get(name) else None for name, entries in self._lists().items()}
        emails, phones, urls, addresses = (lists[name] or () for name in ("email", "phone", "url", "address"))
        tags = tuple(part.strip() for part in self.tags.text().split(",") if part.strip())
        had = {
            "nickname": original is not None and bool(original.nicknames), "email": original is not None and bool(original.emails),
            "phone": original is not None and bool(original.phones), "url": original is not None and bool(original.urls),
            "address": original is not None and bool(original.addresses), "tag": original is not None and bool(original.tags),
        }
        given = {"nickname": nicknames, "email": emails, "phone": phones, "url": urls, "address": addresses, "tag": tags}
        return ContactEdit(
            prefix=self.prefix.text().strip(), given=self.given.text().strip(), additional=self.additional.text().strip(), family=self.family.text().strip(),
            suffix=self.suffix.text().strip(), name=self.display_name.text().strip(), organization=self.organization.text().strip(),
            department=self.department.text().strip(), title=self.title.text().strip(), note=self.note.toPlainText().strip(),
            birthday=None if self._birthday_text() == self._birthday_before else self._birthday_text(),  # unchanged: left as it is
            nickname=nicknames, email=emails, phone=phones, url=urls, address=addresses, tag=tags,
            item=self._item or "", clear=tuple(name for name, entries in given.items() if not entries and had[name] and lists.get(name, ()) is not None),
        )

    def _birthday_text(self) -> str:
        if not self.has_birthday.isChecked():
            return ""
        return f"--{self.birthday.date().toString('MMdd')}" if self._birthday_without_year else self.birthday.date().toString("yyyy-MM-dd")

    def _save(self) -> None:
        values = self.parameters()
        if not (values.given or values.family or values.name or values.organization or values.email):
            self.result_label.setText(f"<span style='color:{red()}'>Give the contact a name, a company or an e-mail address.</span>")
            return
        self.save_button.setEnabled(False)
        self.result_label.setText("Saving…")

        def done(result: ContactInfo) -> None:
            self.saved.emit(result.item)
            self.accept()

        def failed(error: ChronoMailError) -> None:
            self.save_button.setEnabled(True)
            self.result_label.setText(f"<span style='color:{red()}'>{html.escape(str(error))}</span>")

        if self._item is None:
            self._backend.ask(CONTACT_ADD, carried(values, ContactAdd, collection=str(self.book.currentData())), done, failed, owner=self)
        else:
            self._backend.ask(CONTACT_EDIT, values, done, failed, owner=self)
