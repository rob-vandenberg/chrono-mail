from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui.plainboxes import plain_message_boxes
from chrono_mail.ui.statusreport import StatusReport

# --- Version ------------------------------------------------------------
__version__ = "test_ui_plain_text.py 3.4.344"

# --- Version history ----------------------------------------------------
# v3.4.344: Initial: text from mail and servers is never markup in the status line and the message boxes (RV-167, D-259)


def test_the_status_line_shows_a_folder_name_as_it_is(qtbot: QtBot) -> None:
    """RV-167: a folder name or a server reply with "<...>" was shown as markup."""
    parent = QWidget()
    qtbot.addWidget(parent)
    status = StatusReport(parent, lambda account: account)
    status.show("Moved to <b>Archive</b><img src='x'>")
    assert status.label.textFormat() == Qt.TextFormat.PlainText and status.label.text() == "Moved to <b>Archive</b><img src='x'>"


def test_a_message_box_shows_plain_text_unless_it_asks_for_markup(qtbot: QtBot) -> None:
    """RV-167: a message box guessed, and took a server's reply with "<...>" for markup."""
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    boxes = plain_message_boxes(app)
    try:
        guessing = QMessageBox(QMessageBox.Icon.Warning, "Remove account", "The server said: <b>no</b>")
        asking = QMessageBox(QMessageBox.Icon.Warning, "Certificate", "<b>Chosen markup</b>")
        asking.setTextFormat(Qt.TextFormat.RichText)
        for box in (guessing, asking):
            qtbot.addWidget(box)
            box.show()
        assert guessing.textFormat() == Qt.TextFormat.PlainText and asking.textFormat() == Qt.TextFormat.RichText
    finally:
        app.removeEventFilter(boxes)
        boxes.deleteLater()
