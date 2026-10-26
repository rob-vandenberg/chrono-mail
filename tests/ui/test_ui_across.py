from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.core import LOCAL_ACCOUNT_ID
from chrono_mail.engine.local import ensure_local_folders
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui.dragdata import messages_mime_data
from tests.mailfactory import raw_message
from tests.ui.test_ui_dragdrop import drop, folder_point, move_probe
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_across.py 3.6.367"

# --- Version history ----------------------------------------------------
# v3.6.367: a drag of messages of several accounts goes to one folder
# v3.6.366: Initial: Move to offers the other accounts' folders, and messages dropped on a folder of another account go there (D-272)


def with_local_folders(world: World, qtbot: QtBot) -> None:
    ensure_local_folders(world.engine)
    world.window.start()
    settle(qtbot, world, 3)
    wait(qtbot, lambda: LOCAL_ACCOUNT_ID in world.window.mail_state.folders)


def ids_in(world: World, account: str) -> set[str]:
    return {row.message_id for row in world.call("message.list", account=account, folder="Inbox").messages}


def test_move_to_offers_the_other_accounts_folders_and_moves_there(world: World, qtbot: QtBot) -> None:
    with_local_folders(world, qtbot)
    actions = world.window.mail_actions
    actions.fill_move_menu()
    submenus = [menu for action in actions.move_menu.actions() if isinstance(menu := action.menu(), QMenu)]
    assert submenus and any(action.text() == "Inbox" for menu in submenus for action in menu.actions())
    moved = sorted(ids_in(world, world.account_id))[:1]
    world.window.list_view.setCurrentIndex(world.window.list_model.index(world.window.list_model.find(moved[0]), 0))
    actions.move_selected("Inbox", LOCAL_ACCOUNT_ID)
    wait(qtbot, lambda: world.backend._pending == {} and moved[0] in ids_in(world, LOCAL_ACCOUNT_ID))  # noqa: SLF001
    assert moved[0] not in ids_in(world, world.account_id) and world.window.list_model.rowCount() == 2


def test_messages_dropped_on_another_accounts_folder_go_there(world: World, qtbot: QtBot) -> None:
    with_local_folders(world, qtbot)
    dragged = sorted(ids_in(world, world.account_id))[:2]
    world.window.mail_actions.messages_dropped(LOCAL_ACCOUNT_ID, "Inbox", [("Inbox", message) for message in dragged], -1, world.account_id)
    wait(qtbot, lambda: world.backend._pending == {} and set(dragged) <= ids_in(world, LOCAL_ACCOUNT_ID))  # noqa: SLF001
    assert not set(dragged) & ids_in(world, world.account_id)


def test_the_folder_list_takes_a_drag_only_from_an_account_it_holds(world: World, qtbot: QtBot) -> None:
    with_local_folders(world, qtbot)
    assert world.window.tree._known(world.account_id) and world.window.tree._known(LOCAL_ACCOUNT_ID)  # noqa: SLF001
    assert not world.window.tree._known("someone-else")  # noqa: SLF001 - another program's drag, or an account removed meanwhile


def test_a_drag_of_messages_of_several_accounts_goes_to_one_folder(world: World, qtbot: QtBot) -> None:
    """A Favorites view mixes accounts: the drag names each message's account, and the folder takes them all (D-272)."""
    with_local_folders(world, qtbot)
    with open_mailbox(world.engine, LOCAL_ACCOUNT_ID) as mailbox:
        local = mailbox.deliver(mailbox.folder("Inbox"), raw_message(subject="Local", body="b\r\n", message_id="<mixed@x>"), frozenset()).store_id
    own = sorted(ids_in(world, world.account_id))[0]
    data = messages_mime_data("", [("Inbox", own), ("Inbox", local)], sources=[world.account_id, LOCAL_ACCOUNT_ID])
    assert move_probe(world, folder_point(world, "Junk"), data)
    assert drop(world, folder_point(world, "Junk"), data)
    junk = lambda: {row.message_id for row in world.call("message.list", account=world.account_id, folder="Junk").messages}  # noqa: E731
    wait(qtbot, lambda: world.backend._pending == {} and junk() == {own, local})  # noqa: SLF001
    assert local not in ids_in(world, LOCAL_ACCOUNT_ID)
