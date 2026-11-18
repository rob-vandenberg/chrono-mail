import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_accounts import AccountSummary
from chrono_mail.ui import appearance, scale
from chrono_mail.ui.composer import ComposeOptions, ComposeWindow
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.test_ui_preferences import edit_settings, settings_of
from tests.ui.world import World, add_window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_compose_font.py 4.4.623"

# --- Version history ----------------------------------------------------
# v4.4.623: Initial tests (also: OK keeps an unchosen font unchosen): Settings > Writing > Default font is written in and sent with the message; the slider only enlarges what is shown (D-354)


def composer(world: World, qtbot: QtBot, options: ComposeOptions) -> ComposeWindow:
    accounts = [account for account in world.call("account.list") if isinstance(account, AccountSummary)]
    window = ComposeWindow(world.backend, accounts, world.account_id, rich=True, options=options)
    add_window(qtbot, window)
    window.show()
    return window


def test_the_default_font_and_its_size_reach_the_composer_and_the_slider_only_enlarges_what_is_shown(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def change(dialog: SettingsDialog) -> None:
        dialog.compose_font.setCurrentFont(QFont("DejaVu Sans"))
        dialog.compose_font_points.setValue(12)
        dialog.reading_slider.setValue(scale.slider_position(1.5))

    edit_settings(world, qtbot, monkeypatch, change)
    options = world.window.composing.compose_options()  # noqa: SLF001
    family = QFont("DejaVu Sans").family()  # the name Qt gives it on this computer
    assert (options.font_family, options.font_points, options.font_size) == (family, 12, 18.0)


def test_a_message_carries_its_font_and_size_and_is_written_in_it(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot, ComposeOptions(font_size=18.0, font_family="DejaVu Sans", font_points=12))
    window.rich.edit.insertPlainText("Hello")
    html = window.rich.outgoing_html()
    assert "<body style=\"font-family:'DejaVu Sans', sans-serif; font-size:12pt;\">" in html and "18pt" not in html
    assert window.rich.edit.font().family() == QFont("DejaVu Sans").family() and window.rich.edit.font().pointSizeF() == 18.0


def test_a_font_name_cannot_break_out_of_the_style(world: World, qtbot: QtBot) -> None:
    window = composer(world, qtbot, ComposeOptions(font_family="Evil'; color:red\"><script>", font_points=11))
    html = window.rich.outgoing_html()
    assert "<script" not in html and "color:red\">" not in html and "font-size:11pt" in html


def test_without_a_chosen_font_the_program_s_own_font_and_the_system_size_are_used() -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    assert appearance.compose_family("") == appearance.compose_family("  ") != ""
    assert appearance.compose_points(0) == appearance.system_font_size(app) and appearance.compose_points(14) == 14
    assert appearance.compose_family("Noto Serif") == "Noto Serif"


def test_ok_in_settings_keeps_an_unchosen_font_unchosen_so_it_follows_the_system(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def change(dialog: SettingsDialog) -> None:
        dialog.warn_no_subject.setChecked(False)  # something else changed; the font fields left as they show

    edit_settings(world, qtbot, monkeypatch, change)
    chosen = world.window.composing.compose_options()  # noqa: SLF001
    saved = settings_of(world)
    assert saved.value("compose_font_family", "") in ("", None) and int(str(saved.value("compose_font_points", 0) or 0)) == 0
    assert (chosen.font_family, chosen.font_points) == (appearance.compose_family(""), appearance.compose_points(0))
