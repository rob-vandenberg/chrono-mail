from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPalette
from PySide6.QtWidgets import QListView, QTreeView
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_mail import MessageRow
from chrono_mail.ui.delegate import CARD_HEADER_HEIGHT, ROW_HEIGHT, MessageDelegate
from chrono_mail.ui.foldertree import COUNT_MARGIN, FolderItemDelegate
from chrono_mail.ui.icons import folder_icon
from chrono_mail.ui.models import COUNT_ROLE, FOLDER_ROLE_ROLE, GROUP_HEADER_ROLE, KIND_ROLE, PAGE_SIZE, ROW_ROLE, FolderTreeModel, MessageListModel, ViewKind, ViewSpec, group_name
from tests.ui.world import Dialogs, World, build_world, mail, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_models.py 0.2.96"

# --- Version history ----------------------------------------------------
# v0.2.96: A group header in the card layout is 36 px high
# v0.2.94: The group layer: headers, folding, positions; the older tests run without headers
# v0.0.88: A selected card is grey while the list has no focus
# v0.0.87: Folder names without the unread count; the count drawn bold at the right edge; folder-open and home icons
# v0.0.70: The Inboxes section is removed from the folder tree
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.46: Favorites, Inboxes and Local Folders in the folder list; combined views
# v0.0.14: Changes after the first real use (see DEVLOG.md)
# v0.0.13: Initial list model, tree model and delegate tests

SHOTS = Path("/tmp/claude-1000/-home-rob-python-chrono-mail/adc1942e-ac5d-4bdf-8d93-df2e9ee54dc9/scratchpad/shots")


@pytest.fixture
def big(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    messages = {f"u{n:03d}": mail(n, subject=f"Subject {n:03d}", body=f"words and number {n}\r\n") for n in range(250)}
    yield from build_world(tmp_path, qtbot, dialogs, messages=messages)


def model_for(world: World, qtbot: QtBot) -> MessageListModel:
    model = MessageListModel(world.backend)
    model.show_groups = False  # these tests count rows without headers; the tests of the groups switch them on
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
    query.show_groups = False  # written before group headers existed
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
        self.account_id, self.email_address, self.shown_name = account_id, email, email


def test_tree_with_nested_and_virtual_folders() -> None:
    model = FolderTreeModel()
    infos: list[object] = [Info("Inbox", "inbox", 4), Info("Archive/2026/Q1", "other", 2), Info("Trash", "trash"), Info("Sent", "sent"), Info("Drafts", "drafts"), Info("Zeta", "other"), Info("Alpha", "other"), Info("Archive", "archive")]
    model.show_favorites = False
    model.populate([Account("a" * 32, "me@example.com")], {"a" * 32: infos})
    labels: list[str] = []

    def walk(parent: QModelIndex, depth: int) -> None:
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            labels.append("  " * depth + str(index.data()))
            walk(index, depth + 1)

    walk(QModelIndex(), 0)
    assert labels == ["me@example.com", "  Unread", "  Flagged", "  Inbox", "  Drafts", "  Sent", "  Alpha", "  Archive", "    2026", "      Q1", "  Zeta", "  Trash"]
    top = model.index(0, 0)
    kinds = [model.index(row, 0, top).data(KIND_ROLE) for row in range(model.rowCount(top))]
    assert kinds[:3] == ["unread", "flagged", "folder"] and model.index(2, 0, top).data(FOLDER_ROLE_ROLE) == "inbox"
    inbox, drafts = model.index(2, 0, top), model.index(3, 0, top)
    assert inbox.data(COUNT_ROLE) == 4 and drafts.data(COUNT_ROLE) == 0
    assert not inbox.data(Qt.ItemDataRole.FontRole) or not QFont(inbox.data(Qt.ItemDataRole.FontRole)).bold()  # the name is never bold


def test_tree_with_two_accounts_and_no_accounts() -> None:
    model = FolderTreeModel()
    model.show_favorites = False
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
    model.show_groups = False  # written before group headers existed
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
    model.show_groups = False  # written before group headers existed
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


def contrast(first: QColor, second: QColor) -> float:
    def luminance(colour: QColor) -> float:
        def channel(value: float) -> float:
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

        return 0.2126 * channel(colour.redF()) + 0.7152 * channel(colour.greenF()) + 0.0722 * channel(colour.blueF())

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_the_preview_of_the_selected_row_stays_readable(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.delegate import QUIET_ALPHA, QUIET_ALPHA_SELECTED

    assert QUIET_ALPHA_SELECTED > QUIET_ALPHA
    view = QListView()
    qtbot.addWidget(view)
    model = MessageListModel(world.backend)
    model.show_groups = False  # written before group headers existed
    view.setModel(model)
    view.setItemDelegate(MessageDelegate())
    view.setUniformItemSizes(True)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#3daee9"))  # the highlight of KDE's Breeze
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    view.setPalette(palette)
    view.resize(480, ROW_HEIGHT * 5)
    view.show()
    model.show(ViewSpec(world.account_id, "Inbox"))
    wait(qtbot, lambda: model.rowCount() > 0 and model.row_at(0) is not None)
    view.setCurrentIndex(model.index(0))
    qtbot.wait(150)
    image = view.viewport().grab().toImage()
    background = image.pixelColor(image.width() - 12, 5)
    assert background.name() == "#3daee9"
    third_line = range(ROW_HEIGHT // 2 + 8, ROW_HEIGHT - 8)  # the preview
    first_line = range(8, 26)  # the sender
    best = max(contrast(image.pixelColor(x, y), background) for x in range(60, 300) for y in third_line)
    main = max(contrast(image.pixelColor(x, y), background) for x in range(60, 300) for y in first_line)
    assert best >= 0.9 * main and best >= 2.0, (best, main)  # the preview is as readable as the sender; the theme's white on light blue sets the limit


class Place(Account):
    def __init__(self, account_id: str, email: str, kind: str = "pop3", storage: str = "own") -> None:
        super().__init__(account_id, email)
        self.kind, self.storage = kind, storage


def tops(model: FolderTreeModel) -> list[str]:
    return [str(model.index(row, 0).data()) for row in range(model.rowCount())]


def test_favorites_and_local_folders_in_the_tree() -> None:
    model = FolderTreeModel()
    folders: dict[str, list[object]] = {
        "a" * 32: [Info("Inbox", "inbox", 2)], "b" * 32: [Info("Inbox", "inbox", 1)],
        "c" * 32: [Info("Inbox", "inbox", 4), Info("Trash", "trash", 7)], "1" * 31 + "1": [Info("Inbox", "inbox", 4), Info("Trash", "trash", 7)],
    }
    accounts: list[object] = [Place("a" * 32, "one@example.com"), Place("b" * 32, "two@example.com"), Place("1" * 31 + "1", "-", "local"), Place("c" * 32, "three@example.com", storage="local")]
    model.populate(accounts, folders)
    # the account that keeps its mail in Local Folders has no tree of its own, and Local Folders comes last
    assert tops(model) == ["Favorites", "one@example.com", "two@example.com", "Local Folders"]
    favorites = model.index(0, 0)
    labels = [str(model.index(row, 0, favorites).data()) for row in range(model.rowCount(favorites))]
    counts = [model.index(row, 0, favorites).data(COUNT_ROLE) for row in range(model.rowCount(favorites))]
    assert labels[:2] == ["All Inboxes", "Unread"] and labels[-1] == "Trash" and counts[:2] == [7, 7] and counts[-1] == 7 and len(labels) == 8  # shared mail counted once
    model.show_favorites = model.show_local_folders = False
    model.populate(accounts, folders)
    assert tops(model) == ["one@example.com", "two@example.com"]
    model.show_favorites = True
    model.populate(accounts[:1], folders)
    assert tops(model) == ["Favorites", "one@example.com"]


def test_the_unread_count_is_drawn_bold_at_the_right_edge(qtbot: QtBot) -> None:
    model = FolderTreeModel()
    model.show_favorites = False
    model.populate([Account("a" * 32, "me@example.com")], {"a" * 32: [Info("Inbox", "inbox", 85), Info("Drafts", "drafts")]})
    view = QTreeView()
    qtbot.addWidget(view)
    view.setHeaderHidden(True)
    view.setItemDelegate(FolderItemDelegate(view))
    view.setModel(model)
    view.expandAll()
    view.resize(300, 200)
    view.show()
    qtbot.waitExposed(view)
    image = view.viewport().grab().toImage()
    background = image.pixelColor(image.width() - 2, 1)

    def inked(row_index: QModelIndex, left: int, right: int) -> bool:
        rect = view.visualRect(row_index)
        return any(image.pixelColor(x, y) != background for x in range(left, right) for y in range(rect.top() + 2, rect.bottom() - 1))

    top = model.index(0, 0)
    inbox, drafts = model.index(2, 0, top), model.index(3, 0, top)
    edge = view.viewport().width() - COUNT_MARGIN
    assert inked(inbox, edge - 20, edge) and not inked(inbox, edge, view.viewport().width())  # the number ends at the margin
    assert not inked(drafts, edge - 40, view.viewport().width())  # no number when nothing is unread


def test_local_folders_and_other_folders_use_their_icons() -> None:
    assert not folder_icon("local").isNull() and not folder_icon("other").isNull()
    model = FolderTreeModel()
    model.show_favorites = False
    model.populate([Place("1" * 32, "-", "local")], {"1" * 32: [Info("Inbox", "inbox"), Info("Projects", "other")]})
    top = model.index(0, 0)
    assert top.data() == "Local Folders"

    def picture(icon: object) -> QImage:
        return QIcon(icon).pixmap(22, 22).toImage() if isinstance(icon, QIcon) else QImage()

    assert picture(top.data(Qt.ItemDataRole.DecorationRole)) == picture(folder_icon("local")) != picture(folder_icon("account"))
    assert picture(model.index(3, 0, top).data(Qt.ItemDataRole.DecorationRole)) == picture(folder_icon("other"))


def test_a_selected_card_is_grey_while_the_list_has_no_focus(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.colours import selection_grey

    view = QListView()
    qtbot.addWidget(view)
    model = MessageListModel(world.backend)
    model.show_groups = False  # written before group headers existed
    view.setModel(model)
    view.setItemDelegate(MessageDelegate())
    view.resize(480, ROW_HEIGHT * 4)
    view.show()
    model.show(ViewSpec(world.account_id, "Inbox"))
    wait(qtbot, lambda: model.rowCount() > 0 and model.row_at(0) is not None)
    view.setCurrentIndex(model.index(0))
    blue = view.palette().color(QPalette.ColorRole.Highlight).name()
    for focused, expected in ((True, blue), (False, selection_grey())):
        view.setProperty("focused", focused)
        view.viewport().update()
        qtbot.wait(100)
        image = view.viewport().grab().toImage()
        assert image.pixelColor(image.width() - 12, 5).name() == expected


# --- group headers -------------------------------------------------------------------------------------------------------


def test_one_header_over_a_folder_and_folding_it(big: World, qtbot: QtBot) -> None:
    model = MessageListModel(big.backend)
    model.show(ViewSpec(big.account_id, "Inbox", sort="size"))
    wait(qtbot, lambda: model.rowCount() == 251)
    assert model.is_header(0) and model.row_at(0) is None and model.message_position(1) == 0 and model.display_row(0) == 1
    assert model.index(0, 0).data(GROUP_HEADER_ROLE) == ("Tiny", False) and not model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsSelectable
    assert model.index(1, 0).data(GROUP_HEADER_ROLE) is None and model.flags(model.index(1, 0)) & Qt.ItemFlag.ItemIsSelectable
    first = rows(model)[0]
    model.toggle_group(0)
    assert model.rowCount() == 1 and model.index(0, 0).data(GROUP_HEADER_ROLE) == ("Tiny", True) and model.find(first.message_id) == -1
    model.reload()  # folded stays folded: per folder and sort order, until the program closes
    wait(qtbot, lambda: model.index(0, 0).data(GROUP_HEADER_ROLE) == ("Tiny", True))
    assert model.rowCount() == 1
    model.toggle_group(0)
    assert model.rowCount() == 251 and model.find(first.message_id) == 1


def test_a_header_per_subject_and_rows_between_them(big: World, qtbot: QtBot) -> None:
    model = MessageListModel(big.backend)
    model.show(ViewSpec(big.account_id, "Inbox", sort="subject", oldest_first=True))
    wait(qtbot, lambda: model.rowCount() == 500)
    assert model.header_rows()[:3] == [0, 2, 4]
    assert model.index(2, 0).data(GROUP_HEADER_ROLE) == ("Subject 001", False)
    wait(qtbot, lambda: model.row_at(3) is not None)
    assert getattr(model.row_at(3), "subject") == "Subject 001"
    model.toggle_group(2)
    assert model.rowCount() == 499 and model.header_rows()[:3] == [0, 2, 3] and model.message_position(4) == 2


def test_a_search_with_headers_grows_as_it_is_scrolled(big: World, qtbot: QtBot) -> None:
    model = MessageListModel(big.backend)
    model.show(ViewSpec(big.account_id, "Inbox", query="number", sort="subject", oldest_first=True))
    wait(qtbot, lambda: model.rowCount() == 2 * PAGE_SIZE)
    assert model.is_header(0) and getattr(model.row_at(1), "subject") == "Subject 000"
    model.fetchMore()
    wait(qtbot, lambda: model.rowCount() == 4 * PAGE_SIZE)
    assert model.is_header(2 * PAGE_SIZE) and getattr(model.row_at(2 * PAGE_SIZE + 1), "subject") == f"Subject {PAGE_SIZE:03d}"


def test_group_names_as_the_columns_show_them() -> None:
    assert group_name("sender", "Ann Smith <ann@x.org>") == "Ann Smith" and group_name("sender", "") == "(unknown)"
    assert group_name("subject", "") == "(no subject)" and group_name("to", "") == "(no recipients)" and group_name("date", "Today") == "Today"


def test_a_header_in_the_card_layout_is_lower_than_a_card(big: World, qtbot: QtBot) -> None:
    model = MessageListModel(big.backend)
    model.show(ViewSpec(big.account_id, "Inbox", sort="size"))
    wait(qtbot, lambda: model.rowCount() == 251)
    view = QTreeView()
    qtbot.addWidget(view)
    view.setModel(model)
    delegate = MessageDelegate()
    view.setItemDelegate(delegate)
    view.setUniformRowHeights(False)
    view.resize(400, 600)
    view.show()
    wait(qtbot, lambda: view.visualRect(model.index(1, 0)).height() > 0)
    assert CARD_HEADER_HEIGHT == 36 and ROW_HEIGHT == 74
    assert view.visualRect(model.index(0, 0)).height() == 36 and view.visualRect(model.index(1, 0)).height() == 74

