from PySide6.QtCore import QModelIndex, Qt
from pytestqt.qtbot import QtBot

from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.messagelistview import EMPTY_TEXT
from chrono_mail.config import LOCAL_FOLDER_KINDS
from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.engine.commands_folders import FolderInfo
from chrono_mail.ui.models import ACCOUNT_ROLE, KIND_ROLE, FolderTreeModel, standard_kind
from tests.ui.test_ui_window import started
from tests.ui.world import REGISTRY, World, dialogs, wait, world  # noqa: F401

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tree_rows.py 2.0.289"

# --- Version history ----------------------------------------------------
# v2.0.289: the owner's order: Unread, Inbox, Flagged, Drafts, Outbox, Sent, Snoozed, Scheduled, Recovered, Archive, Junk, Trash (D-232)
# v2.0.288: every standard folder (Snoozed and Recovered too) before every folder the person made, in one order, in every kind of account (D-232)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.1.193: The reload test is gone: since D-146 a reload never empties the list, so "no messages" can no longer show while it reloads
# v1.1.188: The standard folders stay together above the own folders; the Empty item names Junk or Trash
# v0.5.125: A list with messages that is reloaded is not settled until its messages are back
# v0.5.123: Initial tests: an account or group row stays selected and shows its Inbox, a heading is never selected, an empty list says so (D-119)


def rows_of(window: MainWindow, kind: str, parent: QModelIndex | None = None) -> list[QModelIndex]:
    """Every row of a kind, anywhere in the tree."""
    found: list[QModelIndex] = []

    def walk(under: QModelIndex) -> None:
        for row in range(window.tree_model.rowCount(under)):
            index = window.tree_model.index(row, 0, under)
            if index.data(KIND_ROLE) == kind:
                found.append(index)
            walk(index)

    walk(parent or QModelIndex())
    return found


def account_row(window: MainWindow, account_id: str) -> QModelIndex:
    return next(index for index in rows_of(window, "account") if index.data(ACCOUNT_ROLE) == account_id)


def click(qtbot: QtBot, window: MainWindow, index: QModelIndex) -> None:
    window.tree.scrollTo(index)
    rect = window.tree.visualRect(index)
    qtbot.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())


def quiet(qtbot: QtBot, world: World) -> None:
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001


def second_account(world: World) -> str:
    added = REGISTRY.run(world.engine, "account.add", dict(
        display_name="Other", email_address="other@example.com", username="other", password="secret",
        incoming_host="127.0.0.1", incoming_port=world.pop.port, incoming_security="none",
        outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none",
    ))
    return str(getattr(added, "account_id"))


def test_a_click_on_another_account_selects_it_and_shows_its_inbox_never_the_old_list(world: World, qtbot: QtBot) -> None:
    other = second_account(world)  # its Inbox is empty
    started(qtbot, world)
    window = world.window
    assert window.mail_state.view.account == world.account_id and window.list_model.rowCount() == 3  # noqa: SLF001
    row = account_row(window, other)
    click(qtbot, window, row)
    quiet(qtbot, world)
    assert window.tree.currentIndex() == row and window.tree.selectionModel().isSelected(row)  # the account row stays selected
    assert window.mail_state.view.account == other and window.mail_state.view.folder == "Inbox" and window.list_model.rowCount() == 0  # noqa: SLF001 - not the 3 messages of the other account
    assert window.list_model.settled and window.windowTitle().startswith("Inbox")


def test_with_the_keys_an_account_row_shows_its_inbox_and_keeps_the_highlight(world: World, qtbot: QtBot) -> None:
    other = second_account(world)
    started(qtbot, world)
    window = world.window
    window.tree.setCurrentIndex(rows_of(window, "folder", account_row(window, other))[0])
    quiet(qtbot, world)
    assert window.list_model.rowCount() == 0
    mine = account_row(window, world.account_id)
    window.tree.setCurrentIndex(mine)  # what an arrow key does
    wait(qtbot, lambda: window.list_model.rowCount() == 3)
    assert window.tree.currentIndex() == mine  # the keys are not thrown back into the Inbox
    assert window.mail_state.view.account == world.account_id and window.mail_state.view.folder == "Inbox"  # noqa: SLF001
    assert window.windowTitle().startswith("Inbox (3)")


def test_a_click_on_the_heading_folds_it_and_keeps_the_folder(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    inbox = window.tree.currentIndex()
    heading = rows_of(window, "section")[0]
    assert window.tree.isExpanded(heading)
    click(qtbot, window, heading)
    assert not window.tree.isExpanded(heading) and window.tree.currentIndex() == inbox and window.list_model.rowCount() == 3
    click(qtbot, window, heading)
    assert window.tree.isExpanded(heading) and window.tree.currentIndex() == inbox
    assert not window.tree.selectionModel().isSelected(heading)


def test_a_click_on_a_group_opens_its_combined_inbox(world: World, qtbot: QtBot) -> None:
    other = second_account(world)
    REGISTRY.run(world.engine, "group.add", {"name": "Work", "account": (world.account_id, other)})
    started(qtbot, world)  # the first Inbox is found inside the group
    window = world.window
    (group,) = rows_of(window, "group")
    window.tree.setCurrentIndex(account_row(window, other))
    quiet(qtbot, world)
    click(qtbot, window, group)
    wait(qtbot, lambda: window.list_model.rowCount() == 3)
    assert window.tree.currentIndex() == group and window.mail_state.view.combined == "inboxes"  # noqa: SLF001 - the group row stays selected, its All Inboxes shown


def test_an_account_without_an_inbox_shows_an_empty_list(world: World, qtbot: QtBot) -> None:
    other = second_account(world)
    started(qtbot, world)
    window = world.window
    window.mail_state.folders[other] = [info for info in window.mail_state.folders[other] if str(getattr(info, "role")) != "inbox"]  # noqa: SLF001
    window.folders.lay_out()  # noqa: SLF001
    window.tree.setCurrentIndex(account_row(window, other))
    assert window.list_model.rowCount() == 0 and window.list_model.settled and window.mail_state.view.account is None  # noqa: SLF001
    assert not window.act_mark_all_read.isEnabled() and not window.act_empty_folder.isEnabled()


def test_an_empty_list_says_so_once_it_has_loaded(world: World, qtbot: QtBot) -> None:
    other = second_account(world)
    started(qtbot, world)
    window = world.window
    view = window.list_view
    window.tree.setCurrentIndex(account_row(window, other))
    assert not window.list_model.settled  # loading: nothing is said yet
    quiet(qtbot, world)
    assert window.list_model.settled and window.list_model.rowCount() == 0
    with_text = view.viewport().grab().toImage()
    window.list_model.settled = False  # the same empty list, as while it loads
    without_text = view.viewport().grab().toImage()
    window.list_model.settled = True
    assert with_text != without_text, EMPTY_TEXT
    differs = [y for y in range(with_text.height()) if any(with_text.pixelColor(x, y) != without_text.pixelColor(x, y) for x in range(with_text.width()))]
    assert differs and max(differs) < 60  # one line of text at the top of the list


def test_the_standard_folders_stay_together_above_the_own_folders(world: World, qtbot: QtBot) -> None:
    for name in ("Aardvark", "Zebra"):  # the Outbox exists already
        world.call("folder.create", account=world.account_id, folder=name)
    started(qtbot, world)
    window = world.window
    wait(qtbot, lambda: window.folders.find_item((world.account_id, "folder", "Zebra")) is not None)  # noqa: SLF001
    top = account_row(window, world.account_id)
    names = [str(window.tree_model.index(row, 0, top).data(Qt.ItemDataRole.DisplayRole)).split("  ")[0] for row in range(window.tree_model.rowCount(top))]
    own = names.index("Aardvark")
    assert names[:3] == ["Unread", "Inbox", "Flagged"] and names[own:] == ["Aardvark", "Zebra"]
    standard = names[3:own]
    assert standard.index("Outbox") < standard.index("Sent") < standard.index("Trash")  # the owner's order (D-232); Junk and Trash before the own folders


def test_the_empty_item_says_which_folder_it_empties(world: World, qtbot: QtBot) -> None:
    started(qtbot, world)
    window = world.window
    for folder, text in (("Trash", "Empty Trash…"), ("Inbox", "Empty folder…")):
        found = window.folders.find_item((world.account_id, "folder", folder))  # noqa: SLF001
        assert found is not None
        window.tree.setCurrentIndex(found)
        wait(qtbot, lambda: window.mail_state.view.folder == folder)  # noqa: SLF001
        assert window.act_empty_folder.text() == text and window.act_empty_folder.isEnabled() == (folder == "Trash")



# Every standard folder as each kind of account has it: by its role (from the server or made here), or by its place (made by the program, no role)
STANDARD = [  # the owner's order (D-232)
    ("Inbox", "inbox"), ("Drafts", "drafts"), ("Outbox", "other"), ("Sent", "sent"), ("Snoozed", "other"), ("Scheduled", "other"),
    ("Recovered", "other"), ("Archive", "archive"), ("Junk", "junk"), ("Trash", "trash"),
]


def test_every_standard_folder_is_known_by_the_one_definition() -> None:
    """A standard folder added to LOCAL_FOLDER_KINDS without a way to know it would sort among the person's folders again (D-232)."""
    assert [standard_kind(path, role) for path, role in STANDARD] == list(LOCAL_FOLDER_KINDS)
    assert standard_kind("Aaa", "other") == standard_kind("Snoozed/Old", "other") == ""


def test_every_standard_folder_comes_before_every_folder_of_the_person_in_every_kind_of_account(qtbot: QtBot) -> None:
    """The owner's rule (D-232): the standard folders first, in one order, then the person's own folders by name; Snoozed was among them."""
    accounts = [
        AccountSummary("1" * 32, "Local Folders", "", "local", True, "", "", "keep"),
        AccountSummary("a" * 32, "Me", "me@example.com", "pop3", True, "", "", "keep"),
        AccountSummary("b" * 32, "Me", "me@example.org", "imap", True, "", "", "keep"),
    ]
    mixed = [("Zzz", "other"), *reversed(STANDARD), ("Aaa", "other"), ("Taxes", "other")]
    model = FolderTreeModel()
    model.show_favorites = False
    model.populate(accounts, {account.account_id: [FolderInfo(path, role, 0, 0) for path, role in mixed] for account in accounts})
    for row in range(model.rowCount(QModelIndex())):
        top = model.index(row, 0, QModelIndex())
        names = [str(model.index(number, 0, top).data(Qt.ItemDataRole.DisplayRole)).split("  ")[0] for number in range(model.rowCount(top))]
        assert names == ["Unread", "Inbox", "Flagged", *(path for path, _ in STANDARD[1:]), "Aaa", "Taxes", "Zzz"]
