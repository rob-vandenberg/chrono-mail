from pathlib import Path

from pytestqt.qtbot import QtBot

from chrono_mail.ui.models import ROW_ROLE
from tests.ui.world import World, settle

# --- Version ------------------------------------------------------------
__version__ = "test_ui_smoke.py 0.5.164"

# --- Version history ----------------------------------------------------
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.0.13: Initial UI smoke test

SHOTS = Path("/tmp/claude-1000/-home-rob-python-chrono-mail/adc1942e-ac5d-4bdf-8d93-df2e9ee54dc9/scratchpad/shots")


def test_window_shows_the_account_the_messages_and_the_first_message(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    model = world.window.list_model
    assert [model.index(row, 0).data(ROW_ROLE).subject for row in range(3)] != []
    world.window.resize(1280, 800)
    world.window.list_view.setCurrentIndex(model.index(0, 0))
    qtbot.waitUntil(lambda: world.window.reader.isVisible() and "Body of message" in world.window.reader.text_view.toPlainText(), timeout=8000)
    world.window.grab().save(str(SHOTS / "smoke.png"))
