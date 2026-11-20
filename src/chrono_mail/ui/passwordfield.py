"""A password field with an eye at its right end: a click shows the typed text, another hides it again (UR-03, D-296). The eye is there only
while the person edits the field; a password or secret that is stored shows as 16 dots, which are never sent (D-365)."""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit, QWidget

from chrono_mail.ui.icons import bundled_icon

# --- Version ------------------------------------------------------------
__version__ = "passwordfield.py 4.6.648"

# --- Version history ----------------------------------------------------
# v4.6.648: the eye only while the person edits the field; show_stored: 16 dots for what is stored; typed: what was typed, never only dots (D-365)
# v4.2.437: Initial version: the eye in every password field and the password box that replaces QInputDialog.getText for passwords

SHOW_TEXT: str = "Show password"
HIDE_TEXT: str = "Hide password"
DOT: str = "\u25cf"
STORED: str = DOT * 16  # what is stored: always 16 dots, whatever its length (the owner)
DOTS: frozenset[str] = frozenset({DOT, "\u2022"})  # a text of only these, of any length, is never a password: as if not typed (the owner)
EDITED: str = "passwordEdited"  # the field's property: the person typed or pasted in it
EYE: str = "passwordEye"


def only_dots(text: str) -> bool:
    return bool(text) and set(text) <= DOTS


def typed(field: QLineEdit) -> str:
    """What the person put in the field; "" when it is empty or holds only dots (the stored one stays)."""
    text = field.text()
    return "" if only_dots(text) else text


def eye_wanted(field: QLineEdit) -> bool:
    """The eye is there only while the person edits the field: once typed in, and while it holds what was typed."""
    return bool(field.property(EDITED)) and bool(field.text()) and not only_dots(field.text())


def _eye(field: QLineEdit) -> QAction | None:
    return next((action for action in field.actions() if action.objectName() == EYE), None)


def show_stored(field: QLineEdit, stored: bool) -> None:
    """The field as nobody touched it: 16 dots when a password or secret is stored, empty when none is; no eye."""
    field.setProperty(EDITED, False)
    field.setText(STORED if stored else "")
    eye = _eye(field)
    if eye is not None:
        eye.setVisible(False)


def make_password_field(field: QLineEdit) -> QAction:
    """Hide what is typed in the field and give it the eye, there only while the person edits the field; every field starts hidden. The
    eye greys with its field. The first key over the 16 dots of what is stored takes them all away."""
    field.setEchoMode(QLineEdit.EchoMode.Password)
    eye = field.addAction(bundled_icon("view-visible"), QLineEdit.ActionPosition.TrailingPosition)
    eye.setObjectName(EYE)
    eye.setToolTip(SHOW_TEXT)
    eye.setVisible(False)

    def toggled() -> None:
        hidden = field.echoMode() == QLineEdit.EchoMode.Password
        field.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        eye.setIcon(bundled_icon("view-hidden" if hidden else "view-visible"))
        eye.setToolTip(HIDE_TEXT if hidden else SHOW_TEXT)

    eye.triggered.connect(toggled)

    def edited(text: str) -> None:  # only what the person types or pastes, never what the program puts in
        if not field.property(EDITED) and DOT in text:  # the first edit over the stored dots: they all go, what was typed stays
            field.setText(text.replace(DOT, ""))
        field.setProperty(EDITED, True)
        wanted = eye_wanted(field)
        if not wanted and field.echoMode() != QLineEdit.EchoMode.Password:
            toggled()  # an emptied field hides again: what is typed next is never shown unasked
        eye.setVisible(wanted)

    field.textEdited.connect(edited)
    return eye


class PasswordBox(QInputDialog):
    """The small box that asks for one password or passphrase: Qt's own text box, with the eye in its field."""

    @staticmethod
    def ask(parent: QWidget | None, title: str, label: str) -> tuple[str, bool]:
        """(the text typed, whether OK was chosen), as QInputDialog.getText answers. The one door every window uses (the tests answer it)."""
        return run_password_box(parent, title, label)


def run_password_box(parent: QWidget | None, title: str, label: str) -> tuple[str, bool]:
    """Show the password box and wait for OK or Cancel."""
    box = PasswordBox(parent)
    box.setWindowTitle(title)
    box.setInputMode(QInputDialog.InputMode.TextInput)
    box.setLabelText(label)
    box.setTextEchoMode(QLineEdit.EchoMode.Password)
    field = box.findChild(QLineEdit)
    if field is not None:
        make_password_field(field)
    accepted = box.exec() == QDialog.DialogCode.Accepted
    text = box.textValue() if accepted else ""  # Cancel answers no text, as QInputDialog.getText does
    box.deleteLater()
    return text, accepted
