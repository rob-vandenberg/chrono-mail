from PySide6.QtWidgets import QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget

from chrono_mail.engine.commands_invitations import ChoicesResult
from chrono_mail.ui import forms
from chrono_mail.ui.forms import part
from chrono_mail.ui.infomark import InfoMark

# --- Version ------------------------------------------------------------
__version__ = "invitationcalendar.py 4.4.512"

# --- Version history ----------------------------------------------------
# v4.4.512: a calendar that holds the meeting already is greyed with its (i); a shared calendar does not start ticked for busy times (D-310)
# v4.4.507: a read-only task list says so; InvitationBusyDialog: the calendars your busy times are made from, with tick boxes (D-310 step 4)
# v4.4.505: Initial: the calendar an accepted invitation goes into: every calendar of every account with a radio button, grouped by account,
#           a read-only one greyed with its (i); the receiving account's first own calendar chosen, else none; OK greyed until one is (D-310);
#           the Delegate window: the address of the person who goes in your place


class InvitationCalendarDialog(QDialog):
    """Which calendar (task list) an accepted invitation goes into. After OK: `chosen`."""

    def __init__(self, choices: ChoicesResult, task: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a task list" if task else "Choose a calendar")
        form = forms.fill(self, "invitation_calendar")  # D-276
        if task:
            part(form, QLabel, "intro").setText("Choose the task list for this task:")
        self.buttons: QDialogButtonBox = part(form, QDialogButtonBox, "buttons")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        rows = part(form, QWidget, "rows")
        layout = rows.layout()
        assert isinstance(layout, QVBoxLayout)  # the form's own layout
        self.group = QButtonGroup(self)
        self.radios: dict[str, QRadioButton] = {}
        shown_account = ""
        for choice in choices.calendars:
            if choice.account != shown_account:
                shown_account = choice.account
                heading = QLabel(choice.account_name, rows)
                bold = heading.font()
                bold.setBold(True)
                heading.setFont(bold)
                layout.addWidget(heading)
            row = QWidget(rows)
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            radio = QRadioButton(choice.name, row)
            radio.setEnabled(not choice.read_only and not choice.holds)
            line.addWidget(radio)
            if not radio.isEnabled():  # greyed for a reason: its (i) says it (D-294)
                mark = InfoMark(row)
                home, item = ("task list", "task") if task else ("calendar", "meeting")
                mark.explain(choice.name, f"This {home} is read-only: nothing can be added to it." if choice.read_only else f"This {item} is in this {home} already.", radio)
                line.addWidget(mark)
            line.addStretch(1)
            layout.addWidget(row)
            self.group.addButton(radio)
            self.radios[choice.collection] = radio
        layout.addStretch(1)
        start = self.radios.get(choices.chosen)
        if start is not None and start.isEnabled():
            start.setChecked(True)
        self.group.buttonToggled.connect(self._usable)
        part(form, QScrollArea, "list").setMinimumHeight(self.fontMetrics().height() * 8)
        forms.smallest(self, 380)
        self._usable()

    def _ok(self) -> QPushButton | None:
        return self.buttons.button(QDialogButtonBox.StandardButton.Ok)

    def _usable(self) -> None:
        button = self._ok()
        if button is not None:
            button.setEnabled(self.group.checkedButton() is not None)

    @property
    def chosen(self) -> str:
        return next((collection for collection, radio in self.radios.items() if radio.isChecked()), "")


class InvitationBusyDialog(QDialog):
    """Which calendars your busy times are made from (Send my busy times…, D-310 step 4). It starts on the own calendars of the account that
    received the request; a read-only calendar can count too, since it is only read. After OK: `chosen`."""

    def __init__(self, choices: ChoicesResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Send my busy times")
        form = forms.fill(self, "invitation_calendar")  # D-276
        part(form, QLabel, "intro").setText("Your busy times are made from the calendars ticked here. Only the times go out, never what the items are:")
        self.buttons: QDialogButtonBox = part(form, QDialogButtonBox, "buttons")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        rows = part(form, QWidget, "rows")
        layout = rows.layout()
        assert isinstance(layout, QVBoxLayout)  # the form's own layout
        self.boxes: dict[str, QCheckBox] = {}
        shown_account = ""
        for choice in choices.calendars:
            if choice.account != shown_account:
                shown_account = choice.account
                heading = QLabel(choice.account_name, rows)
                bold = heading.font()
                bold.setBold(True)
                heading.setFont(bold)
                layout.addWidget(heading)
            box = QCheckBox(choice.name, rows)
            box.setChecked(choice.account == choices.receiving and not choice.read_only and not choice.public and not choice.shared)
            box.toggled.connect(self._usable)
            layout.addWidget(box)
            self.boxes[choice.collection] = box
        layout.addStretch(1)
        part(form, QScrollArea, "list").setMinimumHeight(self.fontMetrics().height() * 8)
        forms.smallest(self, 380)
        self._usable()

    def _usable(self) -> None:
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None:
            button.setEnabled(bool(self.chosen))

    @property
    def chosen(self) -> tuple[str, ...]:
        return tuple(collection for collection, box in self.boxes.items() if box.isChecked())


class InvitationDelegateDialog(QDialog):
    """The address of the person who goes in your place (Delegate…, D-310). After OK: `address`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Delegate")
        form = forms.fill(self, "invitation_delegate")  # D-276
        self.address_field: QLineEdit = part(form, QLineEdit, "address")
        self.buttons: QDialogButtonBox = part(form, QDialogButtonBox, "buttons")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.address_field.textChanged.connect(self._usable)
        forms.smallest(self, 380)
        self._usable()

    def _usable(self) -> None:
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None:
            text = self.address_field.text().strip()
            button.setEnabled("@" in text and " " not in text)

    @property
    def address(self) -> str:
        return self.address_field.text().strip()
