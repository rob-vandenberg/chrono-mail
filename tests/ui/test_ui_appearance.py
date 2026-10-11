from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPalette
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.ui import appearance, icons
from chrono_mail.ui.colours import contrast, green, is_dark, red
from chrono_mail.ui.formatting import AVATAR_HUES, avatar_colour
from chrono_mail.ui.icons import BUNDLED_ICONS, FOLDER_ICONS, RESOURCES, bundled_icon, theme_icon
from tests.ui.test_ui_preferences import edit_settings, settings_of
from tests.ui.world import World, menu_named

# --- Version ------------------------------------------------------------
__version__ = "test_ui_appearance.py 0.0.73"

# --- Version history ----------------------------------------------------
# v0.0.73: The menus are inside one Menu button
# v0.0.56: Menu colour, Refresh, no Add account in the File menu
# v0.0.51: Initial colour scheme, style, colours and icons tests


@pytest.fixture
def app(qapp: QApplication, tmp_path: Path) -> Generator[QApplication]:
    """The application with a scheme and style of our own; what the test changes is put back."""
    saved_style, saved_state = qapp.style().objectName(), (appearance._state.platform_style, appearance._state.style_given, appearance._state.settings)  # noqa: SLF001
    yield qapp
    qapp.styleHints().setColorScheme(Qt.ColorScheme.Unknown)
    qapp.setStyle(saved_style)
    qapp.setPalette(QPalette())
    appearance._state.platform_style, appearance._state.style_given, appearance._state.settings = saved_state  # noqa: SLF001


def settings(tmp_path: Path, **values: object) -> QSettings:
    store = QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat)
    for key, value in values.items():
        store.setValue(key, value)
    return store


def test_the_style_follows_the_choice_and_dark_needs_a_style_that_can_draw_it() -> None:
    assert appearance.style_to_use("default", False, "windowsvista") == "windowsvista"  # light: the platform's own style
    assert appearance.style_to_use("default", True, "windowsvista") == "Fusion"  # dark: the classic Windows style cannot
    assert appearance.style_to_use("default", True, "Windows") == "Fusion"
    assert appearance.style_to_use("default", True, "breeze") == "breeze"  # a style that follows the system is left alone
    assert appearance.style_to_use("default", True, "windows11") == "windows11"
    assert appearance.style_to_use("fusion", False, "breeze") == "Fusion"


def test_a_style_on_the_command_line_wins() -> None:
    assert appearance.style_given(["chrono-mail", "-style", "windows"]) and appearance.style_given(["x", "-style=fusion"]) and appearance.style_given(["x", "--style", "fusion"])
    assert not appearance.style_given(["chrono-mail", "--other"])


def test_the_scheme_and_the_style_are_applied_at_once_and_given_back(app: QApplication, tmp_path: Path) -> None:
    appearance._state.platform_style, appearance._state.style_given = "windows", False  # noqa: SLF001 - pretend the platform has a style that cannot do dark
    app.setStyle("windows")
    appearance.apply(app, settings(tmp_path, color_scheme="dark"))
    assert app.style().objectName().lower() == "fusion" and is_dark()  # dark with a style that cannot do dark: Fusion
    appearance.apply(app, settings(tmp_path, color_scheme="light"))
    assert app.style().objectName().lower() == "windows" and not is_dark()  # the platform's style is back
    appearance.apply(app, settings(tmp_path, color_scheme="light", style="fusion"))
    assert app.style().objectName().lower() == "fusion"
    appearance.apply(app, settings(tmp_path, color_scheme="system", style="default"))
    assert app.style().objectName().lower() == "windows"


def test_a_style_given_on_the_command_line_is_not_changed(app: QApplication, tmp_path: Path) -> None:
    appearance._state.platform_style, appearance._state.style_given = "windows", True  # noqa: SLF001
    app.setStyle("windows")
    appearance.apply(app, settings(tmp_path, color_scheme="dark", style="fusion"))
    assert app.style().objectName().lower() == "windows" and is_dark()  # the scheme still follows the setting


def test_the_settings_window_has_the_choices_and_the_window_applies_them(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, app: QApplication) -> None:
    def choose(dialog: object) -> None:
        from chrono_mail.ui.settingsdialog import SettingsDialog

        assert isinstance(dialog, SettingsDialog)
        assert dialog.scheme_box.currentData() == "system" and dialog.style_box.currentData() == "default"  # the standard
        assert [dialog.scheme_box.itemData(i) for i in range(3)] == ["system", "light", "dark"]
        dialog.scheme_box.setCurrentIndex(dialog.scheme_box.findData("dark"))
        dialog.style_box.setCurrentIndex(dialog.style_box.findData("fusion"))

    edit_settings(world, qtbot, monkeypatch, choose)
    assert settings_of(world).value("color_scheme") == "dark" and settings_of(world).value("style") == "fusion"
    assert is_dark() and app.style().objectName().lower() == "fusion"


@pytest.mark.parametrize("scheme", [Qt.ColorScheme.Light, Qt.ColorScheme.Dark])
def test_marks_and_avatars_can_be_read_in_both_schemes(app: QApplication, scheme: Qt.ColorScheme, tmp_path: Path) -> None:
    appearance.apply(app, settings(tmp_path, color_scheme="dark" if scheme == Qt.ColorScheme.Dark else "light", style="fusion"))
    palette = QGuiApplication.palette()
    assert is_dark(palette) is (scheme == Qt.ColorScheme.Dark)
    for base_role in (QPalette.ColorRole.Base, QPalette.ColorRole.Window):
        background = palette.color(base_role)
        assert contrast(QColor(red(palette)), background) >= 4.5, (scheme, base_role)
        assert contrast(QColor(green(palette)), background) >= 4.5, (scheme, base_role)
    assert contrast(palette.color(QPalette.ColorRole.Text), palette.color(QPalette.ColorRole.Base)) >= 7
    for hue in range(AVATAR_HUES):  # white initials on the avatar: the same in both schemes
        assert contrast(QColor("white"), avatar_colour(f"sender{hue}@example.com")) >= 4.5


def test_every_icon_name_the_program_uses_has_an_icon_of_its_own_and_follows_the_text_colour(app: QApplication, tmp_path: Path) -> None:
    wanted = {name for names in FOLDER_ICONS.values() for name in names} | {"application-exit", "appointment-soon", "document-edit", "document-open", "document-print", "document-save-as", "edit-clear", "edit-delete", "list-add", "mail-archive", "mail-forward", "mail-mark-important", "mail-mark-junk", "mail-mark-notjunk", "mail-mark-read", "mail-message-new", "mail-receive", "mail-reply-all", "mail-reply-sender", "view-filter", "view-refresh", "configure"}
    assert all(not bundled_icon(name).isNull() for name in wanted - {"internet-mail"}), wanted - set(BUNDLED_ICONS)
    assert (RESOURCES / "icons" / "LICENSE-lucide.txt").exists() and theme_icon("name-that-no-theme-has").isNull()
    for scheme in ("light", "dark"):
        appearance.apply(app, settings(tmp_path, color_scheme=scheme, style="fusion"))
        text = QGuiApplication.palette().color(QPalette.ColorRole.ButtonText)
        image: QImage = bundled_icon("mail-reply-sender").pixmap(48, 48).toImage()
        drawn = [image.pixelColor(x, y) for x in range(48) for y in range(48) if image.pixelColor(x, y).alpha() == 255]
        assert drawn and all(abs(c.red() - text.red()) + abs(c.green() - text.green()) + abs(c.blue() - text.blue()) < 12 for c in drawn), scheme


def test_web_pages_are_kept_light(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--other-flag")
    appearance.keep_web_pages_light()
    appearance.keep_web_pages_light()  # twice: still once
    flags = __import__("os").environ["QTWEBENGINE_CHROMIUM_FLAGS"].split()
    assert flags == ["--other-flag", appearance.LIGHT_PAGES_FLAG]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_dropdown_menus_have_the_colour_of_the_menu_bar_and_not_that_of_the_lists(world: World, qtbot: QtBot, app: QApplication, scheme: str) -> None:
    from PySide6.QtCore import QPoint

    world.window.settings.setValue("color_scheme", scheme)
    world.window.settings.setValue("style", "fusion")
    world.window.apply_preferences()
    world.window.resize(900, 500)
    world.window.show()
    qtbot.wait(200)
    palette = QGuiApplication.palette()
    menu = world.window.menuBar().actions()[0].menu()
    assert isinstance(menu, QMenu)
    menu.popup(world.window.mapToGlobal(QPoint(10, world.window.menuBar().height())))
    qtbot.wait(200)
    shown = menu.grab().toImage().pixelColor(3, 3)
    bar = world.window.grab().toImage().pixelColor(400, 5)
    window = palette.color(QPalette.ColorRole.Window)
    assert all(abs(a - b) <= 2 for a, b in ((shown.red(), window.red()), (shown.green(), window.green()), (shown.blue(), window.blue())))  # the colour of the menu bar
    assert shown != palette.color(QPalette.ColorRole.Base) and bar == window
    menu.hide()
    world.window.settings.setValue("style", "default")  # a style that draws its menus well itself is left alone
    world.window.apply_preferences()


def test_refresh_is_a_circle_of_two_arrows_and_the_file_menu_has_no_add_account(world: World) -> None:
    window = world.window
    assert window.act_refresh.text() == "Refresh" and window.act_refresh.shortcut().toString() == "F5" and not window.act_refresh.icon().isNull()
    file_menu = menu_named(window, "File")
    assert isinstance(file_menu, QMenu)
    texts = [action.text() for action in file_menu.actions()]
    assert "Refresh" in texts and "Get mail" not in texts and not any("account" in text.lower() for text in texts)
    assert window.act_refresh in window.toolbar.actions()
    assert (icons.RESOURCES / "icons" / "refresh-cw.svg").exists() and icons.BUNDLED_ICONS["view-refresh"] == "refresh-cw"
