from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui.plainboxes import READY_MADE, plain_box, plain_message_boxes
from chrono_mail.ui.statusreport import StatusReport

# --- Version ------------------------------------------------------------
__version__ = "test_ui_plain_text.py 3.6.349"

# --- Version history ----------------------------------------------------
# v3.6.349: every message box made by hand sets its text format where it is made (guard; the reviewer of 3.5.348)
# v3.5.348: the boxes are made plain where they are made; no event filter on the whole program
# v3.4.344: Initial: text from mail and servers is never markup in the status line and the message boxes (RV-167, D-259)


def test_the_status_line_shows_a_folder_name_as_it_is(qtbot: QtBot) -> None:
    """RV-167: a folder name or a server reply with "<...>" was shown as markup."""
    parent = QWidget()
    qtbot.addWidget(parent)
    status = StatusReport(parent, lambda account: account)
    status.show("Moved to <b>Archive</b><img src='x'>")
    assert status.label.textFormat() == Qt.TextFormat.PlainText and status.label.text() == "Moved to <b>Archive</b><img src='x'>"


def test_a_message_box_shows_plain_text(qtbot: QtBot) -> None:
    """RV-167: a message box guessed, and took a server's reply with "<...>" for markup."""
    box = plain_box(QMessageBox.Icon.Warning, None, "Remove account", "The server said: <b>no</b>", QMessageBox.StandardButton.Ok, QMessageBox.StandardButton.NoButton)
    qtbot.addWidget(box)
    assert box.textFormat() == Qt.TextFormat.PlainText and box.text() == "The server said: <b>no</b>"


def test_the_ready_made_boxes_are_plain_and_nothing_watches_every_event() -> None:
    """3.5.348: the four ready-made boxes are replaced once at start by plain ones; no event filter on the whole program (2026-10-21)."""
    before = {name: QMessageBox.__dict__[name] for name in READY_MADE}
    try:
        plain_message_boxes()
        assert all(QMessageBox.__dict__[name].__func__ is show for name, show in READY_MADE.items())
    finally:
        for name, function in before.items():
            setattr(QMessageBox, name, function)  # the run's own stand-ins again (tests/ui/conftest.py)


def test_every_message_box_made_by_hand_says_how_its_text_is_shown() -> None:
    """A message box made by hand guesses (AutoText) and takes a folder name or a server's reply with "<...>" for markup. Since 3.5.348
    nothing watches the program's events to correct that, so each one sets its text format where it is made (RV-167, D-259)."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src" / "chrono_mail" / "ui"
    unsure: list[str] = []
    for path in sorted(source.glob("*.py")):
        lines = path.read_text("utf-8").splitlines()
        for number, line in enumerate(lines):
            if "QMessageBox(" in line and "plain_box" not in line and path.name != "plainboxes.py" and not any("setTextFormat" in later for later in lines[number : number + 4]):
                unsure.append(f"{path.name}:{number + 1}")
    assert unsure == []


def test_a_plain_box_has_the_default_button_qt_s_own_would_have(qtbot: QtBot) -> None:
    """The second review of 3.5.348: the plain boxes had no default button where Qt's own make the first accepting one the default, so
    Enter did nothing where buttons take no focus."""
    buttons = QMessageBox.StandardButton
    for chosen, expected in (((buttons.Ok), buttons.Ok), ((buttons.Open | buttons.Cancel), buttons.Open), ((buttons.Save | buttons.Discard | buttons.Cancel), buttons.Save)):
        box = plain_box(QMessageBox.Icon.Warning, None, "Open", "Open it?", chosen, buttons.NoButton)
        qtbot.addWidget(box)
        default = box.defaultButton()
        assert default is not None and box.standardButton(default) == expected
    box = plain_box(QMessageBox.Icon.Question, None, "Close", "Save?", buttons.Save | buttons.Discard | buttons.Cancel, buttons.Cancel)
    qtbot.addWidget(box)
    default = box.defaultButton()
    assert default is not None and box.standardButton(default) == buttons.Cancel  # a chosen default is kept

