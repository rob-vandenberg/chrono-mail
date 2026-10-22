from pathlib import Path

import pytest
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrintDialog
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.ui.printing import print_document, print_message
from chrono_mail.ui.sourcedialog import SourceDialog
from tests.ui.world import World, answer_save_file, row_of, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_export.py 0.5.164"

# --- Version history ----------------------------------------------------
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.0.22: Initial save as, view source and print tests


def started(qtbot: QtBot, world: World) -> World:
    world.window.start()
    settle(qtbot, world, 3)
    return world


def select(world: World, row: int) -> None:
    world.window.list_view.setCurrentIndex(world.window.list_model.index(row, 0))


def test_save_as_writes_an_eml_file_named_after_the_subject(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    select(world, 0)
    target = tmp_path / "copy.eml"
    asked: list[str] = []

    def get_save_file_name(parent: object, title: str, start: str, *rest: object) -> tuple[str, str]:
        asked.append(start)
        return str(target), ""

    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(get_save_file_name))
    world.window.act_save_as.trigger()
    wait(qtbot, lambda: target.exists())
    assert asked[0].endswith(".eml") and "Message" in Path(asked[0]).name
    raw = target.read_bytes()
    assert b"Subject: Message" in raw
    wait(qtbot, lambda: str(target) in world.window.status_label.text())


def test_cancelling_save_as_does_nothing(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    select(world, 0)
    answer_save_file(monkeypatch, Path(""))
    world.window.act_save_as.trigger()
    qtbot.wait(150)
    assert list(tmp_path.glob("*.eml")) == []


def test_a_file_that_cannot_be_written_is_reported(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    select(world, 0)
    answer_save_file(monkeypatch, tmp_path / "no" / "such" / "folder.eml")
    world.window.act_save_as.trigger()
    wait(qtbot, lambda: world.window.status_label.text().startswith("Could not save the message: The file cannot be written"))


def test_view_source_shows_the_raw_message(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    select(world, 0)
    seen: list[SourceDialog] = []

    def fake_exec(self: SourceDialog) -> QDialog.DialogCode:
        seen.append(self)
        assert "Subject: Message" in self.view.toPlainText() and self.view.isReadOnly()
        self.copy_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SourceDialog, "exec", fake_exec)
    world.window.act_source.trigger()
    wait(qtbot, lambda: len(seen) == 1)
    from PySide6.QtWidgets import QApplication

    assert "Subject: Message" in QApplication.clipboard().text()
    assert "bytes" in seen[0].windowTitle() or seen[0].windowTitle().startswith("Source: Message")


def test_print_makes_a_pdf_with_the_message(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    select(world, 0)
    pdf = tmp_path / "printed.pdf"

    def fake_exec(self: QPrintDialog) -> QDialog.DialogCode:
        printer = self.printer()
        printer.setOutputFormat(printer.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(pdf))
        printer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait, QMarginsF(15, 15, 15, 15)))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QPrintDialog, "exec", fake_exec)
    world.window.act_print.trigger()
    wait(qtbot, lambda: pdf.exists() and pdf.stat().st_size > 500)
    assert pdf.read_bytes().startswith(b"%PDF")
    wait(qtbot, lambda: world.window.status_label.text() == "Sent to the printer")


def test_cancelling_the_print_dialog_prints_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    select(world, 0)

    def fake_exec(self: QPrintDialog) -> QDialog.DialogCode:
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QPrintDialog, "exec", fake_exec)
    text = world.call("message.text", account=world.account_id, folder="Inbox", message=row_of(world.window, 0).message_id)
    assert not print_message(None, text)


def test_the_print_document_has_only_our_markup_and_escapes_everything(world: World) -> None:
    class Evil:
        subject = "<script>alert(1)</script> & <img src='http://tracker.example/x.png'>"
        sender = ("<b>Mallory</b> <m@x.org>",)
        to = ("you@example.com",)
        cc: tuple[str, ...] = ()
        date = "Monday"
        text = "<img src='file:///etc/passwd'> plain & simple\n<iframe src='http://x'>"
        attachments = ("a<b>.txt",)

    document = print_document(Evil())
    plain = document.toPlainText()
    assert "<script>alert(1)</script>" in plain and "<img src='file:///etc/passwd'>" in plain and "<b>Mallory</b>" in plain  # shown as text, not interpreted
    html = document.toHtml()
    assert "<script" not in html.lower() and "<iframe" not in html.lower() and "<img" not in html.lower()


def test_actions_without_a_selection_do_nothing(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(-1, 0))
    for action in (world.window.act_save_as, world.window.act_source, world.window.act_print):
        action.trigger()
    qtbot.wait(100)
    assert world.dialogs.shown == []
