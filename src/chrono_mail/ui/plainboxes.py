import html
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPushButton, QWidget

# --- Version ------------------------------------------------------------
__version__ = "plainboxes.py 3.6.350"

# --- Version history ----------------------------------------------------
# v3.6.350: plain_tip: a hover text from mail is shown as text, never as markup (D-259)
# v3.6.349: a ready-made box is deleted once answered; its default button is the one Qt's own would have
# v3.5.348: no event filter on the whole program any more (the kind that crashed PySide on 2026-10-21): the four ready-made message boxes
#           are made with plain text from the start, set up once at start; a box made by hand sets plain text where it is made
# v3.4.344: Initial: every message box shows plain text unless it says otherwise (RV-167, D-259)

def plain_tip(text: str) -> str:
    """A hover text Qt always shows as the text itself: Qt guesses markup in a hover text with "<...>", while it often is a subject, a
    sender or a folder name (hostile input, D-259); such a text is given escaped inside markup, any other as it is."""
    return f"<p style='white-space:pre'>{html.escape(text)}</p>" if "<" in text else text


Buttons = QMessageBox.StandardButton
Shown = Callable[..., QMessageBox.StandardButton]


def plain_box(icon: QMessageBox.Icon, parent: QWidget | None, title: str, text: str, buttons: Buttons, default: Buttons) -> QMessageBox:
    """A message box whose text is plain text. Qt's own boxes guess (AutoText) and take text with "<...>" for markup, while it often holds a
    folder name, a subject or a server's reply: hostile input that could change what the box shows (RV-167)."""
    box = QMessageBox(icon, title, text, buttons, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    if default != Buttons.NoButton:
        box.setDefaultButton(default)
    else:  # as Qt's own: the first button that accepts is the one Enter presses (also where buttons take no focus)
        first = next((button for button in box.buttons() if isinstance(button, QPushButton) and box.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole), None)
        if first is not None:
            box.setDefaultButton(first)
    return box


def _ready_made(icon: QMessageBox.Icon, standard: Buttons) -> Shown:
    """QMessageBox.question, warning, information or critical, the same call and answer, with plain text."""

    def show(parent: QWidget | None, title: str, text: str, buttons: Buttons = standard, defaultButton: Buttons = Buttons.NoButton) -> Buttons:  # noqa: N803 - Qt's own keyword
        box = plain_box(icon, parent, title, text, buttons, defaultButton)
        box.exec()
        clicked = box.clickedButton()
        answer = box.standardButton(clicked) if clicked is not None else Buttons.NoButton
        box.deleteLater()  # its parent would keep it until the program ends
        return answer

    return show


READY_MADE: dict[str, Shown] = {
    "question": _ready_made(QMessageBox.Icon.Question, Buttons.Yes | Buttons.No),
    "warning": _ready_made(QMessageBox.Icon.Warning, Buttons.Ok),
    "information": _ready_made(QMessageBox.Icon.Information, Buttons.Ok),
    "critical": _ready_made(QMessageBox.Icon.Critical, Buttons.Ok),
}


def plain_message_boxes() -> None:
    """Once, at start: the program's ready-made message boxes show plain text. Nothing watches the program's events."""
    for name, show in READY_MADE.items():
        setattr(QMessageBox, name, staticmethod(show))
