from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QColor, QImage, QTextDocument
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.engine.commands_mail import ComposeResult
from chrono_mail.mime.pictures import data_address
from chrono_mail.ui import richeditor
from chrono_mail.ui.composer import ROLE_PATH, ComposeWindow
from chrono_mail.ui.richeditor import RichEditor
from tests.ui.test_ui_composer import new_composer

from tests.ui.world import World, add_window, answer_open_file

# --- Version ------------------------------------------------------------
__version__ = "test_ui_pictures.py 4.6.634"

# --- Version history ----------------------------------------------------
# v4.6.634: In plain text a dropped file and a pasted picture become attachments, nothing typed (D-353)
# v4.6.633: A reply switched to plain text has no pictures at all, also as a draft opened again; the question says so (D-353)
# v4.6.631: The answer gives Chrono Mail's own lines and the original apart (D-360)
# v4.4.622: A reply in rich text shows the quoted picture; a plain-text forward names the original's pictures
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


def answer(kind: str, png: bytes, attachments: tuple[str, ...] = ()) -> ComposeResult:
    original = f'<p>Look at <b>our</b> garden</p><img src="{data_address("image/png", "garden.png", png)}" />'
    return ComposeResult(kind, ("ann@example.com",), (), "Re: Our garden", "\n\nOn Tue, Ann wrote:\n> Look at our garden\n", None, (), attachments, None,
                         "<p>On Tue, Ann wrote:</p>", original)


def rich_answer(world: World, qtbot: QtBot, draft: ComposeResult, rich: bool = True) -> ComposeWindow:
    """A reply or forward window as the program opens it with Settings > Writing on Rich text (or Plain text)."""
    window = ComposeWindow(world.backend, [account for account in world.call("account.list") if isinstance(account, AccountSummary)], world.account_id, draft, ("Inbox", "1"), rich=rich)
    add_window(qtbot, window)
    window.show()
    return window


def test_a_reply_in_rich_text_shows_the_quoted_original_with_its_picture_and_sends_it(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    png = picture_file(tmp_path, "garden.png").read_bytes()
    window = rich_answer(world, qtbot, answer("reply", png))
    assert window.is_rich()
    assert "[picture: garden.png]" in window.rich.toPlainText() and "Look at our garden" in window.rich.toPlainText()
    assert data_address("image/png", "garden.png", png) in window.rich.outgoing_html() and window.attachments.count() == 0


def yes_to_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    def yes(*arguments: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", yes)


def picture_attachments(window: ComposeWindow) -> list[str]:
    return [str(window.attachments.item(row).data(ROLE_PATH)) for row in range(window.attachments.count())]


def test_a_forward_in_plain_text_lists_the_pictures_of_the_original_as_attachments_once(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    png = picture_file(tmp_path, "garden.png").read_bytes()
    window = rich_answer(world, qtbot, answer("forward", png, ("plan.pdf",)), rich=False)
    assert picture_attachments(window) == [data_address("image/png", "garden.png", png)] and window.include_original.text().endswith("(plan.pdf)")
    switched = rich_answer(world, qtbot, answer("forward", png, ("plan.pdf",)))  # opened in rich text, then switched
    yes_to_questions(monkeypatch)
    switched.plain_action.trigger()
    assert picture_attachments(switched) == [data_address("image/png", "garden.png", png)] and switched.include_original.text().endswith("(plan.pdf)")


def test_a_reply_switched_to_plain_text_has_no_pictures_at_all_its_own_neither(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    png = picture_file(tmp_path, "garden.png").read_bytes()
    window = rich_answer(world, qtbot, answer("reply", png))
    window.rich.edit.moveCursor(window.rich.edit.textCursor().MoveOperation.Start)
    mine = picture_file(tmp_path, "mine.png", width=12)
    drop_files(window.rich, mine)
    yes_to_questions(monkeypatch)
    window.plain_action.trigger()
    assert picture_attachments(window) == []  # the owner reversed D-353's "keeps its own"


def test_a_reply_saved_as_a_draft_and_switched_to_plain_text_has_no_pictures(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    png = picture_file(tmp_path, "garden.png").read_bytes()
    window = rich_answer(world, qtbot, answer("reply", png))
    saved = world.call("draft.save", account=world.account_id, subject="Re: Our garden", body=window.rich.toPlainText(), html=window.rich.outgoing_html(), kind="reply")
    opened = world.call("draft.open", account=world.account_id, message=getattr(saved, "message_id"))  # getattr: the test reads the answer as given
    again = ComposeWindow(world.backend, [account for account in world.call("account.list") if isinstance(account, AccountSummary)], world.account_id,
                          opened, None, (str(getattr(saved, "folder")), str(getattr(saved, "message_id"))))  # type: ignore[arg-type]  # getattr: as the program opens a draft
    add_window(qtbot, again)
    again.show()
    assert again.is_rich() and again.rich.pictures_in_text()
    yes_to_questions(monkeypatch)
    again.plain_action.trigger()
    assert picture_attachments(again) == []


def test_the_question_of_a_reply_says_its_pictures_are_left_out(world: World, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    png = picture_file(tmp_path, "garden.png").read_bytes()
    window = rich_answer(world, qtbot, answer("reply", png))
    asked: list[str] = []

    def no(parent: object, title: str, text: str, *rest: object) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    window.plain_action.trigger()
    assert asked and "The pictures are left out." in asked[0] and "attachments" not in asked[0] and window.is_rich()


def plain_composer(world: World, qtbot: QtBot) -> ComposeWindow:
    window = new_composer(world, qtbot)
    if window.is_rich():
        window.plain_action.trigger()
    return window


def test_files_dropped_onto_plain_text_are_attached_and_no_path_is_typed(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = plain_composer(world, qtbot)
    window.body.setPlainText("Hello")
    picture = picture_file(tmp_path)
    document = tmp_path / "plan.pdf"
    document.write_bytes(b"%PDF made-up")
    dropped = QMimeData()
    dropped.setUrls([QUrl.fromLocalFile(str(picture)), QUrl.fromLocalFile(str(document))])
    window.body.insertFromMimeData(dropped)
    assert picture_attachments(window) == [str(picture), str(document)] and window.body.toPlainText() == "Hello"


def test_a_picture_pasted_into_plain_text_is_attached_as_a_png_and_nothing_is_typed(world: World, qtbot: QtBot) -> None:
    window = plain_composer(world, qtbot)
    window.body.setPlainText("Hello")
    image = QImage(20, 10, QImage.Format.Format_RGB32)
    image.fill(QColor("#3366cc"))
    copied = QMimeData()
    copied.setImageData(image)
    window.body.insertFromMimeData(copied)
    assert len(picture_attachments(window)) == 1 and picture_attachments(window)[0].startswith("data:image/png;name=picture-1.png;base64,")
    assert window.body.toPlainText() == "Hello"


def test_text_pasted_into_plain_text_is_typed_as_before(world: World, qtbot: QtBot) -> None:
    window = plain_composer(world, qtbot)
    copied = QMimeData()
    copied.setText("pasted words")
    window.body.insertFromMimeData(copied)
    assert window.body.toPlainText() == "pasted words" and picture_attachments(window) == []


def test_cells_copied_with_a_picture_of_them_are_pasted_as_text(world: World, qtbot: QtBot) -> None:
    window = plain_composer(world, qtbot)
    image = QImage(20, 10, QImage.Format.Format_RGB32)
    image.fill(QColor("#3366cc"))
    copied = QMimeData()  # a spreadsheet puts the cells as text and HTML, and a picture of them
    copied.setImageData(image)
    copied.setHtml("<table><tr><td>Apples</td><td>3</td></tr></table>")
    copied.setText("Apples\t3")
    window.body.insertFromMimeData(copied)
    assert "Apples" in window.body.toPlainText() and picture_attachments(window) == []


def test_a_picture_copied_from_a_web_page_is_attached(world: World, qtbot: QtBot) -> None:
    window = plain_composer(world, qtbot)
    image = QImage(20, 10, QImage.Format.Format_RGB32)
    image.fill(QColor("#3366cc"))
    copied = QMimeData()  # a web page's picture comes as the picture and HTML of only that picture
    copied.setImageData(image)
    copied.setHtml('<img src="https://example.com/garden.png" alt="">')
    window.body.insertFromMimeData(copied)
    assert len(picture_attachments(window)) == 1 and window.body.toPlainText() == ""


def test_a_folder_dropped_onto_plain_text_types_nothing(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = plain_composer(world, qtbot)
    dropped = QMimeData()
    dropped.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    window.body.insertFromMimeData(dropped)
    assert window.body.toPlainText() == "" and picture_attachments(window) == []


def test_each_pasted_picture_gets_a_name_no_attachment_has(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = plain_composer(world, qtbot)
    document = tmp_path / "plan.pdf"
    document.write_bytes(b"%PDF made-up")
    window.add_attachment(str(document))
    image = QImage(20, 10, QImage.Format.Format_RGB32)
    image.fill(QColor("#3366cc"))
    copied = QMimeData()
    copied.setImageData(image)
    for _ in range(2):
        window.body.insertFromMimeData(copied)
    window.attachments.takeItem(1)  # picture-1.png removed
    window.body.insertFromMimeData(copied)
    names = [window.attachments.item(row).text().split("  (", 1)[0] for row in range(window.attachments.count())]
    assert names == ["plan.pdf", "picture-2.png", "picture-1.png"]
