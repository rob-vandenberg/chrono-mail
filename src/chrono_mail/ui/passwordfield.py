"""A password field with an eye at its right end: a click shows the typed text, another hides it again (UR-03, D-296)."""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit, QWidget

from chrono_mail.ui.icons import bundled_icon

# --- Version ------------------------------------------------------------
__version__ = "passwordfield.py 4.2.437"

# --- Version history ----------------------------------------------------
# v4.2.437: Initial version: the eye in every password field and the password box that replaces QInputDialog.getText for passwords

SHOW_TEXT: str = "Show password"
HIDE_TEXT: str = "Hide password"


def make_password_field(field: QLineEdit) -> QAction:
    """Hide what is typed in the field and give it the eye; every field starts hidden. The eye greys with its field."""
    field.setEchoMode(QLineEdit.EchoMode.Password)
    eye = field.addAction(bundled_icon("view-visible"), QLineEdit.ActionPosition.TrailingPosition)
    eye.setObjectName("passwordEye")
    eye.setToolTip(SHOW_TEXT)

    def toggled() -> None:
        hidden = field.echoMode() == QLineEdit.EchoMode.Password
        field.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        eye.setIcon(bundled_icon("view-hidden" if hidden else "view-visible"))
        eye.setToolTip(HIDE_TEXT if hidden else SHOW_TEXT)

    eye.triggered.connect(toggled)
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
