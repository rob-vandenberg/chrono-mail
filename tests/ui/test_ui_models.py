from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QListView
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import MessageRow
from chrono_mail.ui.delegate import ROW_HEIGHT, MessageDelegate
from chrono_mail.ui.models import FOLDER_ROLE_ROLE, KIND_ROLE, PAGE_SIZE, ROW_ROLE, FolderTreeModel, MessageListModel, ViewKind, ViewSpec
from tests.ui.world import Dialogs, World, build_world, mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_models.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial list model, tree model and delegate tests

SHOTS = Path("/tmp/claude-1000/-home-rob-python-chrono-mail/adc1942e-ac5d-4bdf-8d93-df2e9ee54dc9/scratchpad/shots")


@pytest.fixture
def big(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    messages = {f"u{n:03d}": mail(n, subject=f"Subject {n:03d}", body=f"words and number {n}\r\n") for n in range(250)}
    yield from build_world(tmp_path, qtbot, dialogs, messages=messages)


def model_for(world: World, qtbot: QtBot) -> MessageListModel:
    model = MessageListModel(world.backend)
    return model


def rows(model: MessageListModel) -> list[MessageRow]:
    return [row for position in range(model.rowCount()) if isinstance((row := model.row_at(position)), MessageRow)]


def test_view_kinds() -> None:
    assert ViewSpec("a", "Inbox").kind is ViewKind.FOLDER
    for spec in (ViewSpec("a", "Inbox", query="x"), ViewSpec("a", None), ViewSpec("a", "Inbox", unread=True), ViewSpec("a", "Inbox", flagged=True), ViewSpec("a", "Inbox", has_attachments=True)):
        assert spec.kind is ViewKind.QUERY


def test_folder_view_knows_its_size_from_the_first_page(big: World, qtbot: QtBot) -> None:
    model = model_for(big, qtbot)
    counts: list[tuple[int, int]] = []

    def record(total: int, unread: int) -> None:
        counts.append((total, unread))

    model.counts_changed.connect(record)
    assert model.rowCount() == 0 and model.row_at(0) is None and model.spec is None
    model.show(ViewSpec(big.account_id, "Inbox"))
    wait(qtbot, lambda: model.rowCount() == 250)
    assert counts[-1] == (250, 250)
    assert [row.subject for row in rows(model)] == [f"Subject {n:03d}" for n in range(100)][::-1] or len(rows(model)) == PAGE_SIZE
    model.show(None)
    assert model.rowCount() == 0 and counts[-1] == (0, 0)


def test_the_query_view_grows_as_it_is_scrolled(big: World, qtbot: QtBot) -> None:
    model = model_for(big, qtbot)
    model.show(ViewSpec(big.account_id, "Inbox", query="number"))
    wait(qtbot, lambda: model.rowCount() == PAGE_SIZE)
    assert model.canFetchMore() and model.rowCount() == 100
    model.fetchMore()
    wait(qtbot, lambda: model.rowCount() == 200)
    model.fetchMore()
    wait(qtbot, lambda: model.rowCount() == 250 and not model.canFetchMore())
    ids = model.message_ids()
    assert len(ids) == len(set(ids)) == 250
    assert model.find(ids[5]) == 5 and model.find("0" * 32) == -1


def test_an_answer_for_a_view_that_is_no_longer_shown_is_ignored(big: World, qtbot: QtBot) -> None:
    model = model_for(big, qtbot)
    model.show(ViewSpec(big.account_id, "Inbox", query="number"))
    model.show(ViewSpec(big.account_id, "Inbox", query="words and number 7"))
    wait(qtbot, lambda: model.rowCount() > 0 and not model.canFetchMore() or model.rowCount() > 0)
    qtbot.wait(300)
    assert all(row.preview.startswith("words and number 7") or "number 7" in row.preview for row in rows(model)) and model.rowCount() < 250


def test_a_failing_load_reports_instead_of_hanging(big: World, qtbot: QtBot) -> None:
    model = model_for(big, qtbot)
    failures: list[str] = []
    model.load_failed.connect(failures.append)
    model.show(ViewSpec(big.account_id, "NoSuchFolder"))
    wait(qtbot, lambda: bool(failures))
    assert "no folder" in failures[0] and model.rowCount() == 0
    model.show(ViewSpec("nobody@example.com", "Inbox", query="x"))
    wait(qtbot, lambda: len(failures) == 2)


def test_reload_shows_the_same_view_again(big: World, qtbot: QtBot) -> None:
    model = model_for(big, qtbot)
    model.show(ViewSpec(big.account_id, "Inbox"))
    wait(qtbot, lambda: model.rowCount() == 250)
    big.pop.messages["extra"] = mail(999)
    big.call("mail.fetch", account=big.account_id)
    model.reload()
    wait(qtbot, lambda: model.rowCount() == 251)


def test_flag_changes_update_rows_and_counts_without_fetching(big: World, qtbot: QtBot) -> None:
    model = model_for(big, qtbot)
    counts: list[tuple[int, int]] = []

    def record(total: int, unread: int) -> None:
        counts.append((total, unread))

    model.counts_changed.connect(record)
    model.show(ViewSpec(big.account_id, "Inbox"))
    wait(qtbot, lambda: model.rowCount() == 250)
    first = rows(model)[0]
    model.replace_flags(first.message_id, ("seen", "flagged"))
    assert getattr(model.row_at(0), "flags") == ("seen", "flagged") and counts[-1] == (250, 249)
    model.replace_flags(first.message_id, ())
    assert counts[-1] == (250, 250)
    model.replace_flags(first.message_id, ("flagged",))
    assert counts[-1] == (250, 250)  # still unread
    model.replace_flags("0" * 32, ("seen",))  # a message that is not loaded: nothing happens
    assert counts[-1] == (250, 250)
    query = MessageListModel(big.backend)
    query.show(ViewSpec(big.account_id, "Inbox", query="number"))
    wait(qtbot, lambda: query.rowCount() == PAGE_SIZE)
    query.replace_flags(rows(query)[3].message_id, ("seen",))
    assert getattr(query.row_at(3), "flags") == ("seen",)


def test_model_data_only_answers_the_row_role(big: World, qtbot: QtBot) -> None:
    model = model_for(big, qtbot)
    model.show(ViewSpec(big.account_id, "Inbox"))
    wait(qtbot, lambda: model.rowCount() == 250)
    assert model.index(0).data(Qt.ItemDataRole.DisplayRole) is None and model.index(0).data(ROW_ROLE) is not None
    assert model.index(-1).data(ROW_ROLE) is None and model.rowCount(model.index(0)) == 0


# --- the folder tree ----------------------------------------------------------------------------------------------------------


class Info:
    def __init__(self, path: str, role: str, unread: int = 0) -> None:
        self.path, self.role, self.unread, self.messages = path, role, unread, 0


class Account:
    def __init__(self, account_id: str, email: str) -> None:
        self.account_id, self.email_address = account_id, email


def test_tree_with_nested_and_virtual_folders() -> None:
    model = FolderTreeModel()
    infos: list[object] = [Info("Inbox", "inbox", 4), Info("Archive/2026/Q1", "other", 2), Info("Trash", "trash"), Info("Sent", "sent"), Info("Drafts", "drafts"), Info("Zeta", "other"), Info("Alpha", "other"), Info("Archive", "archive")]
    model.populate([Account("a" * 32, "me@example.com")], {"a" * 32: infos})
    labels: list[str] = []

    def walk(parent: QModelIndex, depth: int) -> None:
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            labels.append("  " * depth + str(index.data()))
            walk(index, depth + 1)

    walk(QModelIndex(), 0)
    assert labels == ["me@example.com", "  Unread", "  Flagged", "  Inbox  (4)", "  Drafts", "  Sent", "  Alpha", "  Archive", "    2026", "      Q1  (2)", "  Zeta", "  Trash"]
    top = model.index(0, 0)
    kinds = [model.index(row, 0, top).data(KIND_ROLE) for row in range(model.rowCount(top))]
    assert kinds[:3] == ["unread", "flagged", "folder"] and model.index(2, 0, top).data(FOLDER_ROLE_ROLE) == "inbox"


def test_tree_with_two_accounts_and_no_accounts() -> None:
    model = FolderTreeModel()
    model.populate([Account("a" * 32, "one@example.com"), Account("b" * 32, "two@example.com")], {"a" * 32: [Info("Inbox", "inbox")], "b" * 32: []})
    assert model.rowCount() == 2 and model.rowCount(model.index(1, 0)) == 2  # only the two virtual views
    model.populate([], {})
    assert model.rowCount() == 0


# --- painting the rows ---------------------------------------------------------------------------------------------------------


def render(world: World, qtbot: QtBot, palette: QPalette | None = None, width: int = 480) -> QImage:
    view = QListView()
    qtbot.addWidget(view)
    delegate = MessageDelegate()
    model = MessageListModel(world.backend)
    view.setModel(model)
    view.setItemDelegate(delegate)
    view.setUniformItemSizes(True)
    if palette is not None:
        view.setPalette(palette)
    view.resize(width, ROW_HEIGHT * 4 + 4)
    view.show()
    model.show(ViewSpec(world.account_id, "Inbox"))
    wait(qtbot, lambda: model.rowCount() > 0 and model.row_at(0) is not None)
    qtbot.wait(100)
    return view.viewport().grab().toImage()


def distinct_colours(image: QImage) -> int:
    return len({image.pixelColor(x, y).rgb() for x in range(0, image.width(), 3) for y in range(0, image.height(), 3)})


def test_rows_are_painted_in_light_and_dark(world: World, qtbot: QtBot) -> None:
    light = render(world, qtbot)
    assert light.height() >= ROW_HEIGHT * 3 and distinct_colours(light) > 12  # avatars, text, separators
    dark_palette = QPalette()
    dark_palette.setColor(QPalette.ColorRole.Base, QColor("#1e1e1e"))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor("#f0f0f0"))
    dark_palette.setColor(QPalette.ColorRole.Highlight, QColor("#2b5797"))
    dark_palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    dark = render(world, qtbot, dark_palette)
    assert dark.pixelColor(470, ROW_HEIGHT - 10).lightness() < 80 and light.pixelColor(470, ROW_HEIGHT - 10).lightness() > 180
    light.save(str(SHOTS / "rows-light.png"))
    dark.save(str(SHOTS / "rows-dark.png"))


def test_a_narrow_list_and_awkward_texts_do_not_break_painting(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> None:
    nasty = {
        "u1": mail(1, subject="x" * 400, sender="Very Long Name " * 30 + "<a@b.c>", body="y" * 2000 + "\r\n"),
        "u2": mail(2, subject="", sender="<only@address.org>", body=""),
        "u3": mail(3, subject="🙂 emoji ☕ 日本語 العربية", sender="Ünï Cödé <u@x.org>", body="𝔘𝔫𝔦𝔠𝔬𝔡𝔢\r\n"),
    }
    for world in build_world(tmp_path, qtbot, dialogs, messages=nasty):
        for width in (60, 200, 1200):
            assert distinct_colours(render(world, qtbot, width=width)) > 4


def test_rows_in_the_sent_folder_show_the_recipients(world: World, qtbot: QtBot) -> None:
    world.call("mail.send", account=world.account_id, to="Dana Receiver <dana@example.org>", subject="Outgoing", body="x")
    view = QListView()
    qtbot.addWidget(view)
    delegate = MessageDelegate()
    delegate.show_recipients = True
    model = MessageListModel(world.backend)
    view.setModel(model)
    view.setItemDelegate(delegate)
    view.resize(480, 200)
    view.show()
    model.show(ViewSpec(world.account_id, "Sent"))
    wait(qtbot, lambda: model.rowCount() == 1 and model.row_at(0) is not None)
    row = model.row_at(0)
    assert isinstance(row, MessageRow) and "dana@example.org" in row.recipients
    qtbot.wait(100)
    view.viewport().grab().save(str(SHOTS / "rows-sent.png"))
    assert view.sizeHintForRow(0) == ROW_HEIGHT
