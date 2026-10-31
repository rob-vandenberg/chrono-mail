from pytestqt.qtbot import QtBot

from chrono_mail.ui.operations import ERRORS_TAB, TABS
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_operations_errors_tab.py 4.2.448"

# --- Version history ----------------------------------------------------
# v4.2.448: Initial version: the first error of a send and receive shows the Errors tab, once per round (B-25)


def test_the_first_error_of_a_round_shows_the_errors_tab_once(world: World, qtbot: QtBot) -> None:
    operations = world.window.checking.operations
    operations.begin_round()
    operations.add(world.account_id, "receive")
    operations.finish(world.account_id, "receive")
    assert operations.tabs.currentIndex() == TABS.index("Operations")  # a round without errors stays
    operations.begin_round()
    operations.add(world.account_id, "receive")
    operations.finish(world.account_id, "receive", "The server refused the login")
    assert operations.tabs.currentIndex() == ERRORS_TAB
    operations.tabs.setCurrentIndex(TABS.index("Operations"))  # the person goes back
    operations.add_error("Outbox", "A second error in the same round")
    assert operations.tabs.currentIndex() == TABS.index("Operations")
    operations.begin_round()
    operations.add(world.account_id, "receive")
    operations.finish(world.account_id, "receive", "The next round's first error")
    assert operations.tabs.currentIndex() == ERRORS_TAB
