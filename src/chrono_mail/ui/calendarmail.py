from enum import Enum

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QWidget

from chrono_mail.ui import forms
from chrono_mail.ui.forms import part
from chrono_mail.ui.infomark import InfoMark, info_marks
from chrono_mail.ui.plainboxes import plain_tip

# --- Version ------------------------------------------------------------
__version__ = "calendarmail.py 4.4.555"

# --- Version history ----------------------------------------------------
# v4.4.555: The organizer's address has Cancel, Don't send stores it, and a mail the server sends carries no message (D-310)
# v4.4.532: Initial: the one dialog of every calendar mail, in place of the composer (D-310)


class Choice(Enum):
    SEND = "send"  # done and mailed
    QUIET = "quiet"  # done, no mail
    BACK = "back"  # closed: nothing happens


EXPLANATION = "Send does it and mails it, with your message if you wrote one. Don't send does it without a mail. Closing this window leaves everything as it was."
MISSING_ORGANIZER = "This invitation names no organizer. The address below is kept as its organizer, and Send sends your answer to it. Empty the field to have no organizer."


class CalendarMailDialog(QDialog):
    """One question for every calendar mail (D-310): Send, Don't send, and closing it takes the action back. `address`: this mail has no address yet, so it is
    asked for (an empty one greys Send), Cancel takes the action back too; None: the mail knows who it goes to. server_mails: the server sends it, so it can carry no message."""

    def __init__(self, title: str, question: str, explanation: str, parent: QWidget | None = None, address: str | None = None, server_mails: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        form = forms.fill(self, "calendar_mail", info_marks("info"))  # D-276
        part(form, QLabel, "question").setText(question)
        part(form, InfoMark, "info").explain(title, explanation)
        self.to_field: QLineEdit = part(form, QLineEdit, "to")
        self.note_field: QPlainTextEdit = part(form, QPlainTextEdit, "note")
        self.note_field.setTabChangesFocus(True)
        if server_mails:
            self.note_field.setEnabled(False)
            self.note_field.setToolTip(plain_tip("The calendar server sends this answer, so it cannot carry a message."))
        if address is None:
            self.to_field.hide()
            part(form, QLabel, "toLabel").hide()
        else:
            self.to_field.setText(address)
        buttons = part(form, QDialogButtonBox, "buttons")
        self.send_button: QPushButton = buttons.addButton("Send", QDialogButtonBox.ButtonRole.AcceptRole)
        self.quiet_button: QPushButton = buttons.addButton("Don't send", QDialogButtonBox.ButtonRole.ActionRole)
        if address is not None:
            buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(self.reject)
        self.send_button.setDefault(True)
        self.choice: Choice = Choice.BACK
        self.send_button.clicked.connect(lambda: self._chosen(Choice.SEND))
        self.quiet_button.clicked.connect(lambda: self._chosen(Choice.QUIET))
        self.to_field.textChanged.connect(self._usable)
        forms.smallest(self, 420)
        self._usable()

    def _chosen(self, choice: Choice) -> None:
        self.choice = choice
        self.accept()

    def _usable(self) -> None:
        text = self.to_field.text().strip()
        written = "@" in text and not any(character.isspace() or character in '<>,;"' for character in text)
        self.send_button.setEnabled(self.to_field.isHidden() or written)
        self.quiet_button.setEnabled(self.to_field.isHidden() or written or not text)  # an empty field: no organizer

    def ask(self) -> Choice:
        self.exec()
        return self.choice

    @property
    def note(self) -> str:
        return self.note_field.toPlainText().strip()

    @property
    def address(self) -> str:
        return self.to_field.text().strip()
