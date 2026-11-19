from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolBar, QToolButton
from pytestqt.qtbot import QtBot

from chrono_mail.ui.messagewindow import MessageWindow
from tests.ui.test_ui_reader import shown
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_message_window.py 4.6.638"

# --- Version history ----------------------------------------------------
# v4.6.638: Initial: Reply, Reply all and Forward show their text beside the icon, Forward without an arrow (D-362)


def test_reply_reply_all_and_forward_show_their_text_beside_the_icon(world: World, qtbot: QtBot) -> None:
    window = MessageWindow(world.backend, world.account_id, "Inbox", "a" * 32, shown(text="Hallo"), lambda *args: None, lambda url: None, lambda reader: None)
    qtbot.addWidget(window)
    toolbar = window.findChild(QToolBar)
    assert toolbar is not None
    assert toolbar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    buttons = [button for button in toolbar.findChildren(QToolButton) if button.defaultAction() is not None]
    assert [button.text() for button in buttons] == ["Reply", "Reply all", "Forward"]
    assert all(not button.icon().isNull() for button in buttons)
    assert all(button.menu() is None and button.defaultAction().menu() is None for button in buttons)
