import base64
from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QSettings, QUrl
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.ui.accountdialog import AccountDialog
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.models import FOLDER_PATH_ROLE, KIND_ROLE, ROW_ROLE
from tests.ui.world import Dialogs, World, answer_input, answer_save_file, build_world, mail, record_opened_urls, row_of, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_window.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial main window tests

SHOTS = Path("/tmp/claude-1000/-home-rob-python-chrono-mail/adc1942e-ac5d-4bdf-8d93-df2e9ee54dc9/scratchpad/shots")


def started(qtbot: QtBot, world: World, rows: int = 3) -> World:
    world.window.start()
    settle(qtbot, world, rows)
    return world


def subjects(window: MainWindow) -> list[str]:
    model = window.list_model
    return [model.index(row).data(ROW_ROLE).subject for row in range(model.rowCount())]


def select(window: MainWindow, row: int) -> None:
    window.list_view.setCurrentIndex(window.list_model.index(row))


def tree_labels(window: MainWindow) -> list[str]:
    labels: list[str] = []

    def walk(parent: QModelIndex, depth: int) -> None:
        for row in range(window.tree_model.rowCount(parent)):
            index = window.tree_model.index(row, 0, parent)
            labels.append("  " * depth + str(index.data()))
            walk(index, depth + 1)

    walk(QModelIndex(), 0)
    return labels


def seen_count(world: World) -> int:
    """How many messages of the Inbox are marked as read, according to the engine."""
    return sum(1 for row in world.call("message.list", account=world.account_id).messages if "seen" in row.flags)


def flag_sets(world: World) -> list[tuple[str, ...]]:
    return [tuple(row.flags) for row in world.call("message.list", account=world.account_id).messages]


@pytest.fixture
def empty_world(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> Generator[World]:
    yield from build_world(tmp_path, qtbot, dialogs, with_account=False)


# --- first start -------------------------------------------------------------------------------------------------


def test_first_start_without_an_account_asks_for_one(empty_world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    welcome: list[str] = []

    def fake_exec(self: AccountDialog) -> int:
        welcome.append(" ".join(label.text() for label in self.findChildren(QLabel)))
        return int(QDialog.DialogCode.Rejected)

    monkeypatch.setattr(AccountDialog, "exec", fake_exec)
    empty_world.window.start()
    qtbot.waitUntil(lambda: bool(welcome), timeout=5000)
    assert "Welcome to Chrono Mail" in welcome[0]
    assert tree_labels(empty_world.window) == []


# --- folders and the list ------------------------------------------------------------------------------------------


def test_tree_shows_the_account_and_its_folders_with_unread_counts(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    assert tree_labels(world.window) == ["me@example.com", "  Unread", "  Flagged", "  Inbox  (3)", "  Drafts", "  Sent", "  Outbox", "  Junk", "  Trash"]
    assert world.window.count_label.text() == "3 messages, 3 unread"
    assert world.window.sort_button.text() == "Sorted by Date ▾"
    assert world.window.windowTitle() == "Chrono Mail"


def test_the_inbox_is_selected_at_the_start(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    assert world.window.tree.currentIndex().data() == "Inbox  (3)"
    assert world.window._folder == "Inbox" and world.window._account_id == world.account_id  # noqa: SLF001


def test_opening_a_message_shows_it_and_then_marks_it_read(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    wait(qtbot, lambda: "Body of message" in world.window.reader.text_view.toPlainText())
    assert world.window.reader.isVisible()
    wait(qtbot, lambda: world.window.count_label.text() == "3 messages, 2 unread" or "2 unread" in world.window.count_label.text(), timeout=8000)
    wait(qtbot, lambda: any(label.startswith("  Inbox  (2)") for label in tree_labels(world.window)))
    assert "seen" in row_of(world.window, 0).flags
    assert seen_count(world) == 1


def test_switching_quickly_between_messages_shows_the_last_one(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    for row in (0, 1, 2, 1):
        select(world.window, row)
    wait(qtbot, lambda: world.window._shown is not None)  # noqa: SLF001
    expected = row_of(world.window, 1).message_id
    wait(qtbot, lambda: world.window._shown is not None and world.window._shown[2] == expected)  # noqa: SLF001
    wait(qtbot, lambda: "Message 2" in world.window.reader._subject.text())  # noqa: SLF001


def test_a_big_folder_is_loaded_page_by_page(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> None:
    messages = {f"u{n:03d}": mail(n) for n in range(250)}
    for world in build_world(tmp_path, qtbot, dialogs, messages=messages):
        world.window.start()
        wait(qtbot, lambda: world.window.list_model.rowCount() == 250)
        model = world.window.list_model
        assert model.row_at(0) is not None and model.row_at(99) is not None
        assert model.row_at(150) is None and model.row_at(249) is None  # pages 1 and 2 have not been asked for
        assert model.index(150).data(ROW_ROLE) is None  # asking starts the fetch
        wait(qtbot, lambda: model.row_at(150) is not None)
        assert model.row_at(249) is None
        wait(qtbot, lambda: model.index(249).data(ROW_ROLE) is not None)
        assert model.row_at(249) is not None
        assert len({row_of(world.window, row).message_id for row in (0, 99, 150, 249)}) == 4
        world.window.list_view.scrollToBottom()
        qtbot.wait(100)


def test_sorting_changes_the_order(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window._set_sort("subject", True)  # noqa: SLF001
    wait(qtbot, lambda: subjects(world.window) == ["Message 1", "Message 2", "Message 3"])
    assert world.window.sort_button.text() == "Sorted by Subject ▾"
    world.window._set_sort("subject", False)  # noqa: SLF001
    wait(qtbot, lambda: subjects(world.window) == ["Message 3", "Message 2", "Message 1"])


def test_quick_filters(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    first = row_of(world.window, 0)
    world.call("message.flag", account=world.account_id, folder="Inbox", message=first.message_id, seen=True, flagged=True)
    world.window.list_model.reload()
    settle(qtbot, world, 3)
    world.window.filter_unread.setChecked(True)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2 and world.window.count_label.text().startswith("2"))
    world.window.filter_unread.setChecked(False)
    world.window.filter_flagged.setChecked(True)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert subjects(world.window) == [first.subject]
    world.window.filter_flagged.setChecked(False)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)


def test_the_tree_has_unread_and_flagged_views(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    first = row_of(world.window, 0)
    world.call("message.flag", account=world.account_id, folder="Inbox", message=first.message_id, flagged=True)
    for row in range(world.window.tree_model.rowCount(world.window.tree_model.index(0, 0))):
        index = world.window.tree_model.index(row, 0, world.window.tree_model.index(0, 0))
        if index.data(KIND_ROLE) == "flagged":
            world.window.tree.setCurrentIndex(index)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1)
    assert world.window._folder is None  # noqa: SLF001 - a view across the folders


def test_search_in_the_folder_and_in_all_folders(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.search_box.setText("number 2")
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: subjects(world.window) == ["Message 2"])
    world.window.search_box.clear()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.call("message.move", account=world.account_id, folder="Inbox", message=row_of(world.window, 0).message_id, target="Junk")
    world.window.search_box.setText("Message")
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    world.window.scope.setCurrentIndex(1)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
    world.window.search_box.setText("zzzzzz")
    world.window.search_box.returnPressed.emit()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 0)


# --- actions on messages ----------------------------------------------------------------------------------------------


def test_mark_flag_and_unflag(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 1)
    world.window.act_flag.trigger()
    wait(qtbot, lambda: "flagged" in row_of(world.window, 1).flags)
    world.window.act_read.trigger()
    wait(qtbot, lambda: "seen" in row_of(world.window, 1).flags)
    world.window.act_unread.trigger()
    wait(qtbot, lambda: "seen" not in row_of(world.window, 1).flags)
    world.window.act_unflag.trigger()
    wait(qtbot, lambda: "flagged" not in row_of(world.window, 1).flags)
    assert flag_sets(world).count(()) == 3


def test_delete_moves_to_trash_and_deleting_in_trash_asks_first(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_delete.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    assert world.window.status_label.text() == "Moved 1 message(s) to Trash"
    assert world.call("message.list", account=world.account_id, folder="Trash").total == 1
    trash = next(world.window._find_tree_item((world.account_id, "folder", "Trash")) for _ in [0])  # noqa: SLF001
    assert trash is not None
    world.window.tree.setCurrentIndex(trash)
    wait(qtbot, lambda: world.window.list_model.rowCount() == 1 and world.window._folder == "Trash")  # noqa: SLF001
    select(world.window, 0)
    world.dialogs.answer = QMessageBox.StandardButton.No
    world.window.act_delete.trigger()
    qtbot.wait(300)
    assert world.window.list_model.rowCount() == 1 and any("for good" in text for _, _, text in world.dialogs.shown)
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    world.window.act_delete.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 0)
    assert world.call("message.list", account=world.account_id, folder="Trash").total == 0


def test_delete_several_messages_at_once(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.list_view.selectAll()
    world.window.act_delete.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 0)
    assert world.call("message.list", account=world.account_id, folder="Trash").total == 3
    assert world.window.reader._header.isHidden()  # noqa: SLF001


def test_move_to_another_folder(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.move_menu.aboutToShow.emit()
    titles = [action.text() for action in world.window.move_menu.actions()]
    assert "Junk" in titles and "Inbox" not in titles and "Outbox" not in titles
    world.window.move_selected("Junk")
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    assert world.call("message.list", account=world.account_id, folder="Junk").total == 1


def test_actions_without_a_selection_do_nothing(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    for action in (world.window.act_delete, world.window.act_flag, world.window.act_reply, world.window.act_forward, world.window.act_read):
        action.trigger()
    qtbot.wait(200)
    assert world.window.list_model.rowCount() == 3 and world.window._composers == []  # noqa: SLF001


# --- mail --------------------------------------------------------------------------------------------------------------------


def test_get_mail_downloads_new_messages_and_shows_them(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.pop.messages["uid9"] = mail(9)
    world.window.act_refresh.trigger()
    assert not world.window.act_refresh.isEnabled()  # a check is running
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)
    wait(qtbot, lambda: world.window.act_refresh.isEnabled())
    assert world.window.status_label.text() == "Mail checked: 1 new message(s)"
    wait(qtbot, lambda: any(label.startswith("  Inbox  (4)") for label in tree_labels(world.window)))
    world.window.act_refresh.trigger()
    wait(qtbot, lambda: world.window.status_label.text() == "Mail checked: nothing new")


def test_a_failing_mail_check_shows_the_problem(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.engine.passwords.set(world.account_id, ServerRole.INCOMING, "wrong")
    world.window.act_refresh.trigger()
    wait(qtbot, lambda: world.window.status_label.text().startswith("Mail check:"))
    assert "login" in world.window.status_label.text() and "wrong" not in world.window.status_label.text().replace("wrong password", "")
    assert "c0392b" in world.window.status_label.styleSheet()
    wait(qtbot, lambda: world.window.act_refresh.isEnabled())


def test_automatic_checking_can_be_turned_off(world: World, qtbot: QtBot) -> None:
    assert world.window._timer.isActive()  # noqa: SLF001
    world.window.act_auto.setChecked(False)
    assert not world.window._timer.isActive() and world.window.settings.value("auto_check") in (False, "false")  # noqa: SLF001
    world.window.act_auto.setChecked(True)
    assert world.window._timer.isActive()  # noqa: SLF001


# --- folders --------------------------------------------------------------------------------------------------------------------


def test_create_rename_and_delete_a_folder(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    answer_input(monkeypatch, [("Projects", True), ("Projects/Alpha", True)])
    world.window.new_folder()
    wait(qtbot, lambda: "  Projects" in tree_labels(world.window))
    world.window.new_folder()
    wait(qtbot, lambda: "    Alpha" in tree_labels(world.window))
    projects = world.window._find_tree_item((world.account_id, "folder", "Projects"))  # noqa: SLF001
    assert projects is not None
    world.window.tree.setCurrentIndex(projects)
    answer_input(monkeypatch, [("Work", True)])
    world.window.rename_folder()
    wait(qtbot, lambda: "  Work" in tree_labels(world.window) and "    Alpha" in tree_labels(world.window))
    assert "  Projects" not in tree_labels(world.window)
    work = world.window._find_tree_item((world.account_id, "folder", "Work"))  # noqa: SLF001
    assert work is not None
    world.window.tree.setCurrentIndex(work)
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    world.window.delete_folder()
    wait(qtbot, lambda: "  Work" not in tree_labels(world.window))


def test_a_folder_that_cannot_be_created_reports_why(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    answer_input(monkeypatch, [("Inbox", True)])
    world.window.new_folder()
    wait(qtbot, lambda: "already exists" in world.window.status_label.text())
    assert "c0392b" in world.window.status_label.styleSheet()


# --- composing, attachments, links -------------------------------------------------------------------------------------------


def test_reply_opens_a_prefilled_composer(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_reply.trigger()
    wait(qtbot, lambda: len(world.window._composers) == 1)  # noqa: SLF001
    composer = world.window._composers[0]  # noqa: SLF001
    assert composer.to.text() == "Alice <alice@example.com>" and composer.subject.text().startswith("Re: Message")
    assert "wrote:" in composer.body.toPlainText()
    composer.body.document().setModified(False)
    composer._finished = True  # noqa: SLF001
    composer.close()


def test_forward_and_reply_all_and_new_message(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_forward.trigger()
    world.window.act_reply_all.trigger()
    world.window.act_new.trigger()
    wait(qtbot, lambda: len(world.window._composers) == 3)  # noqa: SLF001
    subjects_seen = sorted(composer.subject.text()[:4] for composer in world.window._composers)  # noqa: SLF001
    assert subjects_seen == ["", "Fwd:", "Re: "]
    for composer in list(world.window._composers):  # noqa: SLF001
        composer._finished = True  # noqa: SLF001
        composer.close()


def test_an_attachment_can_be_saved(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = (b"From: A <a@x.org>\r\nTo: me@example.com\r\nSubject: Report\r\nMessage-ID: <r@x>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
           b"--B\r\nContent-Type: text/plain\r\n\r\nSee attached\r\n--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"report.pdf\"\r\n"
           b"Content-Transfer-Encoding: base64\r\n\r\n" + base64.encodebytes(b"%PDF-1.4 content") + b"--B--\r\n")
    for world in build_world(tmp_path, qtbot, dialogs, messages={"r": raw}):
        world.window.start()
        settle(qtbot, world, 1)
        select(world.window, 0)
        wait(qtbot, lambda: not world.window.reader._attachments.isHidden())  # noqa: SLF001
        buttons = world.window.reader._attachments.findChildren(type(world.window.reader._load_remote))  # noqa: SLF001
        assert len(buttons) == 1 and "report.pdf" in buttons[0].text()
        target = tmp_path / "saved-report.pdf"
        answer_save_file(monkeypatch, target)
        buttons[0].click()
        wait(qtbot, lambda: target.exists())
        assert target.read_bytes() == b"%PDF-1.4 content"
        wait(qtbot, lambda: world.window.status_label.text().startswith("Saved"))


def test_links_ask_before_they_open(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    started(qtbot, world)
    opened = record_opened_urls(monkeypatch)
    world.dialogs.answer = QMessageBox.StandardButton.No
    world.window._open_link(QUrl("https://example.com/page"))  # noqa: SLF001
    assert opened == [] and "https://example.com/page" in world.dialogs.shown[-1][2]
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    world.window._open_link(QUrl("https://example.com/page"))  # noqa: SLF001
    assert opened == ["https://example.com/page"]
    world.window._open_link(QUrl("file:///etc/passwd"))  # noqa: SLF001
    world.window._open_link(QUrl("javascript:alert(1)"))  # noqa: SLF001
    world.window._open_link(QUrl("ftp://example.com/x"))  # noqa: SLF001
    assert opened == ["https://example.com/page"]  # nothing but web addresses is ever opened
    world.window._open_link(QUrl("mailto:friend@example.org"))  # noqa: SLF001
    assert len(world.window._composers) == 1 and world.window._composers[0].to.text() == "friend@example.org"  # noqa: SLF001
    world.window._composers[0]._finished = True  # noqa: SLF001
    world.window._composers[0].close()


# --- settings ------------------------------------------------------------------------------------------------------------------------


def test_sort_order_and_selected_folder_are_remembered(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> None:
    ini = None
    for world in build_world(tmp_path, qtbot, dialogs):
        world.window.start()
        settle(qtbot, world, 3)
        world.window._set_sort("sender", True)  # noqa: SLF001
        qtbot.wait(200)
        world.window.close()
        ini = world.settings_path
        settings = QSettings(str(ini), QSettings.Format.IniFormat)
        assert settings.value("sort") == "sender" and settings.value("last_folder") == "Inbox"
        reopened = MainWindow(world.backend, settings)
        qtbot.addWidget(reopened)
        assert reopened._sort == "sender" and reopened._oldest_first is True  # noqa: SLF001
        reopened.close()
    assert ini is not None


def test_a_screenshot_for_review(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    world.window.resize(1280, 800)
    world.call("message.flag", account=world.account_id, folder="Inbox", message=row_of(world.window, 1).message_id, flagged=True, seen=True)
    world.window.list_model.reload()
    settle(qtbot, world, 3)
    select(world.window, 0)
    wait(qtbot, lambda: "Body of message" in world.window.reader.text_view.toPlainText())
    qtbot.wait(900)
    world.window.grab().save(str(SHOTS / "window.png"))


def test_deleting_right_after_clicking_does_not_report_a_missing_message(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 0)
    world.window.act_delete.trigger()  # the message is moved while its content is still being fetched
    wait(qtbot, lambda: world.window.list_model.rowCount() == 2)
    qtbot.wait(600)
    assert world.window.status_label.text() == "Moved 1 message(s) to Trash"
    assert "c0392b" not in world.window.status_label.styleSheet()


def test_mail_is_checked_when_the_program_starts(tmp_path: Path, qtbot: QtBot, dialogs: Dialogs) -> None:
    for world in build_world(tmp_path, qtbot, dialogs, fetch=False):
        world.window.settings.setValue("check_on_start", True)
        world.window.start()
        wait(qtbot, lambda: world.window.list_model.rowCount() == 3)
        wait(qtbot, lambda: world.window.status_label.text() == "Mail checked: 3 new message(s)")
        assert world.window.act_refresh.isEnabled()


def test_a_background_check_keeps_the_message_being_read_selected(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    select(world.window, 1)
    wait(qtbot, lambda: world.window._shown is not None)  # noqa: SLF001
    assert world.window._shown is not None  # noqa: SLF001
    reading = world.window._shown[2]  # noqa: SLF001
    world.pop.messages["uid9"] = mail(9, date=None)
    world.window.act_refresh.trigger()
    wait(qtbot, lambda: world.window.list_model.rowCount() == 4)
    wait(qtbot, lambda: world.window.list_view.currentIndex().isValid() and row_of(world.window, world.window.list_view.currentIndex().row()).message_id == reading)
    assert "Message" in world.window.reader._subject.text()  # noqa: SLF001
