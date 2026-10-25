from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui.plainboxes import READY_MADE, plain_box, plain_message_boxes
from chrono_mail.ui.statusreport import StatusReport
from tests.ui.test_ui_columnsetup import open_folder
from tests.ui.world import World, mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_plain_text.py 3.6.355"

# --- Version history ----------------------------------------------------
# v3.6.355: The owner rule each check of the project enforces is named (OR-42)
# v3.6.350: a hover text from mail is the text itself (the list's rows, plain_tip); "&" in a folder name shows in Move to
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
    # owner rule OR-40 (D-259): message boxes are plain text
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



def test_a_hover_text_from_mail_is_shown_as_the_text_itself(qtbot: QtBot) -> None:
    """The final review of 3.6.350 (D-259): Qt guesses markup in a hover text; a subject such as "<font size=7>…" was drawn large."""
    from PySide6.QtGui import QTextDocument

    from chrono_mail.ui.plainboxes import plain_tip

    for text in ("<font size=7 color=red>Your account is locked</font>", "<img src='file:///etc/x.png'>", "Tom & Jerry", "Plain words"):
        document = QTextDocument()
        document.setHtml(plain_tip(text)) if "<" in text else document.setPlainText(plain_tip(text))
        assert document.toPlainText() == text
    assert plain_tip("Plain words") == "Plain words"  # an ordinary hover text stays as it was


def test_the_message_list_gives_a_subject_with_markup_as_plain_text(world: World, qtbot: QtBot) -> None:
    """The final review of 3.6.350 (D-259): the hover text of a row was its subject as it came, so markup in it was drawn."""
    from PySide6.QtGui import QTextDocument

    from chrono_mail.engine.mailbox import open_mailbox
    from chrono_mail.ui.plainboxes import plain_tip

    subject = "<font size=7 color=red>Your account is locked</font>"
    with open_mailbox(world.engine, world.account_id) as mailbox:
        mailbox.deliver(("Inbox",), mail(9, subject=subject, message_id="<hostile@x>"), frozenset())
    window = world.window
    window.start()
    model = window.list_model

    def shown() -> list[int]:
        return [row for row in range(model.rowCount()) if (found := model.row_at(row)) is not None and found.subject == subject]

    wait(qtbot, lambda: bool(shown()))
    row = shown()[0]
    column = model.columns.index("subject")
    tip = model.index(row, column).data(Qt.ItemDataRole.ToolTipRole)
    document = QTextDocument()
    document.setHtml(str(tip))
    assert tip == plain_tip(subject) and document.toPlainText() == subject


def test_a_folder_name_with_an_ampersand_shows_whole_in_the_move_menu(world: World, qtbot: QtBot) -> None:
    """The final review of 3.6.350: Qt reads "&" as a shortcut mark, so "R&D" showed as "RD" in Move to."""
    world.call("folder.create", account=world.account_id, folder="R&D")
    window = world.window
    window.start()
    open_folder(qtbot, world, "Inbox")
    wait(qtbot, lambda: "R&D" in [info.path for info in window.mail_state.folders.get(world.account_id, [])])
    window.mail_actions.fill_move_menu()
    assert "R&&D" in [action.text() for action in window.mail_actions.move_menu.actions()]


def test_no_error_text_is_put_into_markup_as_it_came() -> None:
    # owner rule OR-40 (D-259): no markup from mail or errors
    """The final review of 3.6.350 (D-259): the account windows put a server's error text into a red <span> as it came, so markup in it
    was drawn. An error inside markup is escaped."""
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "src" / "chrono_mail" / "ui"
    raw = re.compile(r"<[a-z][^>]*>[^\"']*\{(error|problem|exc|reason)\}")
    found: list[str] = []
    for path in sorted(source.glob("*.py")):
        text = path.read_text("utf-8")
        for number, line in enumerate(text.splitlines()):
            match = raw.search(line)
            if match and not re.search(rf"\b{match.group(1)} = (html\.)?escape\(", text):  # escaped where it was made (certificatedialog)
                found.append(f"{path.name}:{number + 1}")
    assert found == []
