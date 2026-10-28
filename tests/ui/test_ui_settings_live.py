import pytest
from PySide6.QtWidgets import QComboBox, QDialog
from pytestqt.qtbot import QtBot

from chrono_mail.ui import scale
from chrono_mail.ui.delegate import COLUMN_ROW_HEIGHTS
from chrono_mail.ui.mainwindow import MainWindow
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.world import World, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_settings_live.py 4.2.401"

# --- Version history ----------------------------------------------------
# v4.2.401: Settings > Message list is shown at once while it is changed; Cancel puts it back (also the reading pane's width), OK
#           keeps it, nothing saved before OK, a Row height keeps the size the slider tries (D-104)


def choose(box: QComboBox, value: str) -> None:
    box.setCurrentIndex(box.findData(value))


def change_every_list_setting(dialog: SettingsDialog) -> None:
    choose(dialog.layout_box, "cards")
    choose(dialog.pane_box, "bottom")
    choose(dialog.date_box, "iso")
    choose(dialog.density_box, "roomy")
    dialog.conversations.setChecked(not dialog.conversations.isChecked())
    dialog.tag_text_colour.setChecked(not dialog.tag_text_colour.isChecked())


def shown(window: MainWindow) -> tuple[str, str, str, int, bool, bool]:
    """What the main window shows of the six settings now."""
    return (window.columns.layout, window.reading.place, window.columns.date_style, window.messages.column_delegate.row_height,
            window.act_conversations.isChecked(), window.list_model.tag_text)


def tried_then(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, answer: QDialog.DialogCode) -> list[tuple[str, str, str, int, bool, bool]]:
    """Open Settings, change the six settings and close it with the answer: what was shown while it was open, and before."""
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    seen: list[tuple[str, str, str, int, bool, bool]] = [shown(window)]

    def change_and_close(self: SettingsDialog) -> QDialog.DialogCode:
        change_every_list_setting(self)
        seen.append(shown(window))
        assert window.prefs.saved().list_layout == "columns"  # nothing saved before OK
        return answer

    monkeypatch.setattr(SettingsDialog, "exec", change_and_close)
    window.open_settings("Message list")
    return seen


def test_the_message_list_settings_show_at_once_and_cancel_puts_them_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    before, during = tried_then(world, qtbot, monkeypatch, QDialog.DialogCode.Rejected)
    assert during == ("cards", "bottom", "iso", COLUMN_ROW_HEIGHTS["roomy"], not before[4], not before[5])
    assert shown(world.window) == before
    assert world.window.prefs.saved().list_layout == "columns" and world.window.prefs.trying is None


def test_ok_keeps_what_the_message_list_settings_showed(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    _, during = tried_then(world, qtbot, monkeypatch, QDialog.DialogCode.Accepted)
    assert shown(world.window) == during
    saved = world.window.prefs.saved()
    assert (saved.list_layout, saved.reading_pane, saved.date_style, saved.list_density) == ("cards", "bottom", "iso", "roomy")
    assert world.window.prefs.trying is None


def test_a_row_height_tried_keeps_the_size_the_slider_tries(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    seen: list[float] = []

    def size_then_row_height(self: SettingsDialog) -> QDialog.DialogCode:
        self.list_slider.setValue(scale.slider_position(1.5))
        choose(self.density_box, "roomy")
        seen.append(scale.message_list())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SettingsDialog, "exec", size_then_row_height)
    window.open_settings("Message list")
    assert seen == [1.5]
    assert scale.message_list() == 1.0 and window.messages.column_delegate.row_height == COLUMN_ROW_HEIGHTS["normal"]  # Cancel: both back


def test_cancel_gives_the_reading_pane_back_its_width(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    window.show()
    splitter = window.reading._splitter  # noqa: SLF001 - the list and the reading pane side by side
    total = sum(splitter.sizes())
    splitter.setSizes([total - 300, 300])  # the person dragged the pane narrower
    wait(qtbot, lambda: splitter.sizes()[1] == 300)

    def try_below(self: SettingsDialog) -> QDialog.DialogCode:
        choose(self.pane_box, "bottom")
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SettingsDialog, "exec", try_below)
    window.open_settings("Message list")
    assert window.reading.place == "right" and splitter.sizes()[1] == 300
