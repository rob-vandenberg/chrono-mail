import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.engine import commands_updates
from chrono_mail.ui import importdialog, settingsdialog
from chrono_mail.ui.backend import Backend
from chrono_mail.ui.exportdialog import ExportDialog
from chrono_mail.ui.importdialog import ImportDialog
from chrono_mail.ui.indexrebuilds import BACKGROUND_TIP, RebuildWindow
from chrono_mail.ui.infomark import InfoMark
from chrono_mail.ui.prefs import Remembered
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from tests.enginefactory import make_engine
from tests.ui.world import Dialogs, World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_infomark.py 4.2.424"

# --- Version history ----------------------------------------------------
# v4.2.424: Initial: the (i) after a greyed option says why, at a hover and in a small window; the eight places that have one (D-294)


def mark(qtbot: QtBot, reason: str) -> InfoMark:
    made = InfoMark()
    qtbot.addWidget(made)
    made.explain("Automatically check for updates", reason)
    return made


def test_the_mark_shows_only_while_there_is_a_reason_and_is_never_greyed(qtbot: QtBot) -> None:
    made = mark(qtbot, "")
    assert made.isHidden()
    made.explain("Automatically check for updates", "Why it is greyed")
    assert not made.isHidden() and made.isEnabled() and made.toolTip() == "Why it is greyed"
    assert made.focusPolicy() & Qt.FocusPolicy.TabFocus  # Tab reaches it
    made.explain("Automatically check for updates", "")
    assert made.isHidden()


def test_a_click_enter_or_space_opens_the_reason_in_a_small_window(qtbot: QtBot, dialogs: Dialogs) -> None:
    made = mark(qtbot, "This copy is running from the project folder.")
    made.click()
    assert dialogs.shown == [("information", "Automatically check for updates", "This copy is running from the project folder.")]
    qtbot.keyClick(made, Qt.Key.Key_Return)
    qtbot.keyClick(made, Qt.Key.Key_Space)
    assert len(dialogs.shown) == 3


def test_a_hover_shows_the_reason_at_once(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []

    def show_text(position: object, text: str, widget: object = None) -> None:
        shown.append(text)

    monkeypatch.setattr("chrono_mail.ui.infomark.QToolTip.showText", show_text)
    made = mark(qtbot, "Why it is greyed")
    QApplication.sendEvent(made, QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))
    assert shown == ["Why it is greyed"]


def general_page(world: World, qtbot: QtBot, tray_available: bool) -> SettingsDialog:
    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, SettingsContext(tray_available=tray_available, shortcut_actions=window._shortcut_registry()), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    return dialog


def info(parent: QWidget, name: str) -> InfoMark:
    found = parent.findChild(InfoMark, name)
    assert found is not None
    return found


def test_the_update_tick_of_a_copy_run_from_its_project_folder_says_why(world: World, qtbot: QtBot) -> None:
    dialog = general_page(world, qtbot, tray_available=True)
    update = info(dialog, "updateCheckInfo")
    wait(qtbot, lambda: update.reason == commands_updates.NOT_FROM_AN_INSTALLER)
    assert update.reason == "This copy is running from the project folder. It cannot be updated via the installer."  # the owner's words
    assert not update.isHidden() and update.isEnabled()
    assert all(info(dialog, name).isHidden() for name in ("trayInfo", "closeToTrayInfo", "notificationsInfo"))


def test_without_a_notification_area_its_three_ticks_say_why_and_the_line_is_gone(world: World, qtbot: QtBot) -> None:
    dialog = general_page(world, qtbot, tray_available=False)
    for name in ("trayInfo", "closeToTrayInfo", "notificationsInfo"):
        assert info(dialog, name).reason == settingsdialog.NO_TRAY and not info(dialog, name).isHidden()
    assert dialog.findChild(QLabel, "noTrayNote") is None


@pytest.fixture
def backend(tmp_path: Path) -> Generator[Backend]:
    made = Backend(make_engine(tmp_path / "profile"))
    yield made
    made.wait_for_done(5000)


def test_export_s_rules_say_why_they_are_greyed(qtbot: QtBot, backend: Backend, tmp_path: Path) -> None:
    dialog = ExportDialog(backend, memory=Remembered(QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)))
    qtbot.addWidget(dialog)
    wait(qtbot, lambda: dialog.account_list.count() > 0 or dialog.rules.toolTip() != "")
    rules = info(dialog, "rulesInfo")
    assert rules.reason == ("" if dialog.rules.isEnabled() else dialog.rules.toolTip())
    assert rules.isHidden() is dialog.rules.isEnabled()


@pytest.mark.skipif(sys.platform == "win32", reason="these are read on Windows itself")
def test_import_s_windows_choices_and_the_waiting_import_say_why(qtbot: QtBot, backend: Backend) -> None:
    dialog = ImportDialog(backend, lambda: None)
    qtbot.addWidget(dialog)
    for name in ("dataFileAccountsInfo", "dataFileRulesInfo", "dataFileSignaturesInfo"):
        assert info(dialog, name).reason == importdialog.WINDOWS_ONLY and not info(dialog, name).isHidden()
    dialog._reading("Reading", importdialog.WAIT_FOR_EXPORTS)  # noqa: SLF001 - a reading of the exports starts
    assert info(dialog, "importInfo").reason == importdialog.WAIT_FOR_EXPORTS and not info(dialog, "importInfo").isHidden()
    dialog._read()  # noqa: SLF001
    assert info(dialog, "importInfo").isHidden()


def test_run_in_background_says_why_it_is_greyed(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    window = RebuildWindow(host)
    window.show_step(1, 1, "Work", 10, 100, usable=False)
    assert window.background_info.reason == BACKGROUND_TIP and not window.background_info.isHidden()
    window.show_step(1, 1, "Work", 20, 100, usable=True)
    assert window.background_info.isHidden()


def test_the_calendar_s_own_colour_says_why_it_cannot_be_deleted(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.paletteeditor import OWN_COLOUR_STAYS, PaletteEditor
    from tests.ui.test_ui_calendar import calendar_id, open_calendar

    page = open_calendar(world, qtbot)
    calendar = page.panel.info(calendar_id(world))
    assert calendar is not None
    editor = PaletteEditor(world.backend, calendar)
    qtbot.addWidget(editor)
    own, other = editor.rows[0], editor.rows[1]
    assert own.delete_info.reason == OWN_COLOUR_STAYS and not own.delete_info.isHidden()
    assert other.delete_info.isHidden()
    policy = other.delete_info.sizePolicy()
    assert policy.retainSizeWhenHidden()  # the rows stay lined up



def test_a_read_only_calendar_s_colours_say_why_and_their_marks_stay_usable(world: World, qtbot: QtBot) -> None:
    import dataclasses

    from chrono_mail.ui.paletteeditor import READ_ONLY_COLOURS, PaletteEditor
    from tests.ui.test_ui_calendar import calendar_id, open_calendar

    page = open_calendar(world, qtbot)
    calendar = page.panel.info(calendar_id(world))
    assert calendar is not None
    editor = PaletteEditor(world.backend, dataclasses.replace(calendar, read_only=True))
    qtbot.addWidget(editor)
    assert info(editor, "addInfo").reason == READ_ONLY_COLOURS and not info(editor, "addInfo").isHidden()
    assert not editor.add_button.isEnabled() and not editor.rows[1].delete_button.isEnabled() and not editor.rows[1].name.isEnabled()
    assert editor.rows[0].delete_info.isEnabled()  # never greyed with the row
