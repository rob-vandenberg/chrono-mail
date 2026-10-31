"""The small box that asks for one line of text, opened at a width the owner gave: Qt's own text box, which getText cannot give a width."""

from PySide6.QtWidgets import QDialog, QInputDialog, QWidget

from chrono_mail.ui.scale import wpx

# --- Version ------------------------------------------------------------
__version__ = "textbox.py 4.2.454"

# --- Version history ----------------------------------------------------
# v4.2.454: Initial version: the text box that opens at a given width (Rename folder, the owner)


class TextBox(QInputDialog):
    """Qt's own text box (its title, text, field, OK and Cancel), opened at a width at the standard size."""

    @staticmethod
    def ask(parent: QWidget | None, title: str, label: str, text: str, width: int) -> tuple[str, bool]:
        """(the text typed, whether OK was chosen), as QInputDialog.getText answers. The one door every window uses (the tests answer it)."""
        box = make_text_box(parent, title, label, text, width)
        accepted = box.exec() == QDialog.DialogCode.Accepted
        typed = box.textValue() if accepted else ""  # Cancel answers no text, as QInputDialog.getText does
        box.deleteLater()
        return typed, accepted


def make_text_box(parent: QWidget | None, title: str, label: str, text: str, width: int) -> TextBox:
    """The box, filled in and sized, not shown yet: it opens width wide at the standard size (it follows the screen's scale and the size
    slider), never narrower than its own parts need and never wider than its screen; it is not held at that width afterwards."""
    box = TextBox(parent)
    box.setWindowTitle(title)
    box.setInputMode(QInputDialog.InputMode.TextInput)
    box.setLabelText(label)
    box.setTextValue(text)
    screen = box.screen()
    room = screen.availableGeometry().width() if screen is not None else wpx(width)
    box.resize(max(box.minimumSizeHint().width(), min(wpx(width), room)), box.sizeHint().height())
    return box
