from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QColor, QImage, QTextDocument
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.mime.pictures import data_address
from chrono_mail.ui import richeditor
from chrono_mail.ui.composer import ROLE_PATH, ComposeWindow
from chrono_mail.ui.richeditor import RichEditor
from tests.ui.test_ui_composer import new_composer
from tests.ui.world import World, answer_open_file

# --- Version ------------------------------------------------------------
__version__ = "test_ui_pictures.py 4.4.621"

# --- Version history ----------------------------------------------------
# v4.4.621: Initial tests: pictures dragged, pasted or chosen into the text, sent unaltered; a dropped file attached (D-353)


def picture_file(folder: Path, name: str = "beach.png", width: int = 40) -> Path:
    image = QImage(width, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("#3366cc"))
    path = folder / name
    assert image.save(str(path))
    return path


def rich_composer(world: World, qtbot: QtBot) -> ComposeWindow:
    window = new_composer(world, qtbot)
    if not window.is_rich():
        window.rich_action.trigger()
    return window


def drop_files(editor: RichEditor, *paths: Path) -> None:
    dropped = QMimeData()
    dropped.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    editor.edit.insertFromMimeData(dropped)


def test_a_picture_file_dragged_onto_the_text_stands_there_and_goes_out_unaltered(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = rich_composer(world, qtbot)
    path = picture_file(tmp_path)
    window.rich.edit.insertPlainText("Look: ")
    drop_files(window.rich, path)
    assert "file://" not in window.rich.toPlainText() and window.rich.toPlainText() == "Look: [picture: beach.png]"
    assert data_address("image/png", "beach.png", path.read_bytes()) in window.rich.outgoing_html()  # the file's own bytes
    assert window.attachments.count() == 0


def test_a_dropped_file_that_is_not_a_picture_is_attached_and_no_path_is_typed(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = rich_composer(world, qtbot)
    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4 a made-up report")
    drop_files(window.rich, report)
    assert window.rich.toPlainText() == "" and window.attachments.count() == 1
    assert window.attachments.item(0).data(ROLE_PATH) == str(report)


def test_a_copied_picture_is_pasted_into_the_text(world: World, qtbot: QtBot) -> None:
    window = rich_composer(world, qtbot)
    copied = QMimeData()
    image = QImage(20, 10, QImage.Format.Format_RGB32)
    image.fill(QColor("#cc3333"))
    copied.setImageData(image)
    window.rich.edit.insertFromMimeData(copied)
    assert window.rich.toPlainText() == "[picture: picture-1.png]" and "data:image/png;name=picture-1.png;base64," in window.rich.outgoing_html()


def test_picture_in_the_formatting_bar_puts_the_chosen_picture_at_the_cursor(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = rich_composer(world, qtbot)
    path = picture_file(tmp_path, "logo.png")
    answer_open_file(monkeypatch, path)
    window.rich.picture.trigger()
    assert window.rich.toPlainText() == "[picture: logo.png]"


def test_a_wide_picture_is_shown_no_wider_than_the_text_but_sent_at_its_own_size(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = rich_composer(world, qtbot)
    path = picture_file(tmp_path, "wide.png", width=4000)
    drop_files(window.rich, path)
    (address,) = window.rich.pictures
    shown = window.rich.edit.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(address))
    assert isinstance(shown, QImage) and shown.width() < 4000 and shown.width() <= max(200, window.rich.edit.viewport().width())
    assert data_address("image/png", "wide.png", path.read_bytes()) in window.rich.outgoing_html()


def test_a_picture_too_large_is_refused_calmly_and_the_text_stays_as_it_was(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = rich_composer(world, qtbot)
    monkeypatch.setattr(richeditor, "MAXIMUM_PICTURE_BYTES", 10)
    drop_files(window.rich, picture_file(tmp_path))
    assert window.rich.toPlainText() == "" and "larger than" in world.dialogs.shown[-1][2]


def test_going_to_plain_text_says_so_and_makes_the_pictures_attachments(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window = rich_composer(world, qtbot)
    path = picture_file(tmp_path)
    drop_files(window.rich, path)
    asked: list[str] = []

    def question(parent: object, title: str, text: str, *rest: object) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", question)
    window.plain_action.trigger()
    assert not window.is_rich() and "pictures become attachments" in asked[0]
    assert window.attachments.count() == 1 and window.attachments.item(0).data(ROLE_PATH) == data_address("image/png", "beach.png", path.read_bytes())


def test_pasted_text_with_a_broken_picture_is_pasted_without_that_picture(world: World, qtbot: QtBot) -> None:
    window = rich_composer(world, qtbot)
    copied = QMimeData()
    copied.setHtml('<p>Our new office <img src="data:image/png;name=x.png;base64,@@cut off" /> opens Monday</p>')
    window.rich.edit.insertFromMimeData(copied)
    assert window.rich.toPlainText() == "Our new office opens Monday" and window.rich.pictures == {}


def test_the_shown_picture_follows_the_width_of_the_text_area(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = rich_composer(world, qtbot)
    drop_files(window.rich, picture_file(tmp_path, "wide.png", width=4000))
    (address,) = window.rich.pictures

    def shown_width() -> int:
        shown = window.rich.edit.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(address))
        assert isinstance(shown, QImage)
        return shown.width()

    window.resize(500, 600)
    qtbot.waitUntil(lambda: shown_width() <= max(200, window.rich.edit.viewport().width()))
    narrow = shown_width()
    window.resize(1200, 600)
    qtbot.waitUntil(lambda: shown_width() > narrow)
    assert shown_width() <= window.rich.edit.viewport().width()


def test_pasted_text_with_a_picture_that_cannot_be_shown_is_pasted_without_it(world: World, qtbot: QtBot) -> None:
    window = rich_composer(world, qtbot)
    copied = QMimeData()
    copied.setHtml(f'<p>Logo <img src="{data_address("image/png", "logo.png", b"not a picture at all")}" /> here</p>')
    window.rich.edit.insertFromMimeData(copied)
    assert window.rich.pictures == {} and "<img" not in window.rich.outgoing_html()
