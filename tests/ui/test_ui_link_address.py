import pytest
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_dav import FindParameters
from tests.ui.test_ui_dav import link_window
from tests.ui.world import World

# --- Version ------------------------------------------------------------
__version__ = "test_ui_link_address.py 4.8.662"

# --- Version history ----------------------------------------------------
# v4.8.662: Initial (Q28, D-298): Link to fills in the mail account's server and leaves it open to change; Find asks that address


def test_link_to_fills_in_the_address_and_find_asks_the_one_changed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    fields = link_window(world, qtbot).calendar
    fields.target.setCurrentIndex(1)  # a mail account
    assert fields.url.isEnabled() and fields.url.text().startswith("https://")  # filled in from it
    assert not fields.username.isEnabled() and not fields.password.isEnabled()  # its login stays
    fields.url.setText("https://dav.example.com:8443/")  # the calendar server on a port of its own
    asked: list[object] = []
    def note(command: object, parameters: object, *rest: object, **named: object) -> None:
        asked.append(parameters)  # what Find asks, never sent

    monkeypatch.setattr(world.backend, "ask", note)
    fields.find_button.click()
    assert isinstance(asked[0], FindParameters) and asked[0].url == "https://dav.example.com:8443/"
