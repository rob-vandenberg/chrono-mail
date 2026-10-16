from collections.abc import Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPalette
from PySide6.QtWidgets import QApplication, QListWidget, QMenu
from pytestqt.qtbot import QtBot

from chrono_mail.ui import appearance, icons, scale
from chrono_mail.ui.colours import BLUE_ON_DARK, BLUE_ON_LIGHT, ON_BLUE, contrast, green, is_dark, red, with_program_blue
from chrono_mail.ui.formatting import AVATAR_HUES, avatar_colour
from chrono_mail.ui.icons import FOLDER_ICONS, RESOURCES, bundled_icon, folder_icon, theme_icon
from tests.ui.test_ui_preferences import edit_settings, settings_of
from tests.ui.world import World, menu_named, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_appearance.py 0.5.149"

# --- Version history ----------------------------------------------------
# v0.5.149: The blue is the program's own in every scheme and window state, never the system's accent colour
# v0.5.144: Four sliders: Main window, Message list, Other windows and dialogs, Text of messages; the program's text and the Settings window follow Other windows and dialogs, the main window and its menus follow Main window
# v0.3.115: The rows of the page list of Settings are 2 points higher than their text needs
# v0.3.113: The page list of Settings is 2 points larger than the desktop's size, times the factor
# v0.3.108: The size of messages is a slider for reading and writing: conversion, at once, Cancel
# v0.3.107: The ticks of the size slider stand above its track
# v0.3.106: The size of the interface is a factor with a slider on the Appearance page: keys apply each step at once, a drag when it ends
# v0.0.85: The icons are the program's own Breeze files
# v0.0.82: Tests of the text size of the interface
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


GROUPS: tuple[QPalette.ColorGroup, ...] = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled)


class GreyAccent(QPalette):
    """The palette of a system whose accent colour is dark grey (as Windows gives it to Qt: the accent as the highlight,
    near-black for windows that are not active)."""

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        if not args:
            self.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight, QColor("#4c4a48"))
            self.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, QColor("#1e1e1e"))
            self.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor("#4c4a48"))


def test_the_system_scheme_draws_the_programs_own_blue_never_the_systems_accent(app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appearance, "QPalette", GreyAccent)  # the system gives its grey accent (a light system: Qt without a screen cannot report a dark one)
    appearance.apply(app, settings(tmp_path, color_scheme="system", style="fusion"))
    palette = app.palette()
    for group in GROUPS:
        assert palette.color(group, QPalette.ColorRole.Highlight).name() == BLUE_ON_LIGHT, group
        assert palette.color(group, QPalette.ColorRole.HighlightedText).name() == ON_BLUE, group


@pytest.mark.parametrize(("dark", "blue"), [(True, BLUE_ON_DARK), (False, BLUE_ON_LIGHT)])
def test_the_programs_blue_replaces_a_grey_accent_in_every_window_state(dark: bool, blue: str) -> None:
    palette = with_program_blue(GreyAccent(), dark)
    assert all(palette.color(group, QPalette.ColorRole.Highlight).name() == blue for group in GROUPS)
    assert all(palette.color(group, QPalette.ColorRole.HighlightedText).name() == ON_BLUE for group in GROUPS)


@pytest.mark.parametrize(("scheme", "blue"), [("dark", BLUE_ON_DARK), ("light", BLUE_ON_LIGHT)])
def test_the_dark_and_light_schemes_keep_their_blue_in_every_window_state(app: QApplication, tmp_path: Path, scheme: str, blue: str) -> None:
    appearance.apply(app, settings(tmp_path, color_scheme=scheme, style="fusion"))
    assert all(app.palette().color(group, QPalette.ColorRole.Highlight).name() == blue for group in GROUPS)


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
    assert all(not folder_icon(kind).isNull() for kind in FOLDER_ICONS)
    wanted = {"application-exit", "clock", "document-edit", "document-open", "document-print", "document-save-as", "edit-clear", "edit-delete", "list-add", "archive-insert", "mail-forward", "mail-mark-important", "mail-mark-junk", "mail-mark-notjunk", "mail-mark-read", "mail-message-new", "mail-receive", "mail-reply-all", "mail-reply-sender", "view-filter", "view-refresh", "configure"}
    assert all(not bundled_icon(name).isNull() for name in wanted), {name for name in wanted if bundled_icon(name).isNull()}
    assert (RESOURCES / "icons" / "LICENSE-breeze.txt").exists() and theme_icon("name-that-no-theme-has").isNull()
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
    assert (icons.RESOURCES / "icons" / "view-refresh.svg").exists()


def test_the_size_of_the_interface_is_a_factor_and_an_old_text_size_is_converted(tmp_path: Path) -> None:
    assert scale.saved_factor(settings(tmp_path), 10) == 1.0  # nothing saved: the system's size
    assert scale.saved_factor(settings(tmp_path, ui_font_size=15), 10) == 1.5  # a size in points of an older version
    assert scale.saved_factor(settings(tmp_path, ui_font_size=30), 10) == 2.0  # at most Large
    assert scale.saved_factor(settings(tmp_path, interface_scale="1.23"), 10) == 1.25  # on a step of the slider
    assert scale.saved_factor(settings(tmp_path, interface_scale="text"), 10) == 1.0
    assert scale.saved_factor(settings(tmp_path, interface_scale="0.1", ui_font_size=15), 10) == 0.75  # the factor wins; at least Small
    assert scale.factor_at(scale.slider_position(1.35)) == 1.35 and scale.factor_at(0) == 0.75 and scale.factor_at(scale.SLIDER_STEPS) == 2.0


def test_text_icons_and_spacing_follow_the_factor(app: QApplication, tmp_path: Path) -> None:
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QStyle

    original = QFont(app.font())
    appearance._state.system_font = QFont(original)  # noqa: SLF001 - what start() remembers
    system = appearance.system_font_size(app)
    try:
        appearance.apply(app, settings(tmp_path, interface_scale=1.5))  # no size of its own for the other windows yet: they keep the one they shared
        style = app.style()
        assert isinstance(style, scale.ScaledStyle) and app.font().pointSizeF() == system * 1.5 and appearance.main_font(app).pointSizeF() == system * 1.5
        normal = style.baseStyle().pixelMetric(QStyle.PixelMetric.PM_ToolBarIconSize)
        assert style.pixelMetric(QStyle.PixelMetric.PM_ToolBarIconSize) == round(normal * 1.5)
        assert scale.scale_css("padding: 6px 10px; border: 1px solid; font-size: 20px;") == "padding: 9px 15px; border: 1px solid; font-size: 30px;"
        appearance.apply(app, settings(tmp_path, interface_scale=1.5, windows_scale=1.25))  # the other windows have a size of their own
        assert app.font().pointSizeF() == system * 1.25 and appearance.main_font(app).pointSizeF() == system * 1.5 and scale.wpx(40) == 50 and scale.px(40) == 60
        appearance.apply(app, settings(tmp_path, interface_scale=1.5), 0.75, 1.0)  # tried out, not saved
        assert app.font().pointSizeF() == system * 1.0 and appearance.main_font(app).pointSizeF() == system * 0.75 and scale.px(40) == 30
    finally:
        appearance.apply(app, settings(tmp_path, interface_scale=1.0))
        app.setFont(original)  # the other tests start with the size they had


def test_the_sizes_are_together_on_the_appearance_page_and_the_slider_works_at_once(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QLabel

    from chrono_mail.ui.settingsdialog import SettingsDialog

    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    page = dialog.interface_slider.parentWidget()
    assert page is not None
    labels = [label.text() for label in page.findChildren(QLabel)]
    for text in ("Sizes", "Main window", "Message list", "Other windows and dialogs", "Text of messages", "Small", "Large"):
        assert text in labels
    assert "Interface (text and icons)" not in labels
    for moved in (dialog.list_slider, dialog.windows_slider, dialog.reading_slider):
        assert moved.parentWidget() is page
    dialog.interface_scale_tried.connect(window.try_interface_scale)
    try:
        dialog.interface_slider.setValue(scale.slider_position(1.5))
        assert scale.interface() == 1.5 and window.section_bar.buttons["mail"].height() == 60  # 40 pixels at 1.5, at once
        assert dialog.values()["interface_scale"] == 1.5
        dialog.interface_standard.click()
        assert scale.interface() == 1.0 and dialog.values()["interface_scale"] == 1.0
    finally:
        window.try_interface_scale(1.0)


def test_the_page_list_of_settings_is_two_points_larger_and_follows_the_slider(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.settingsdialog import SettingsDialog

    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    system = appearance.system_font_size(app)
    assert dialog.sidebar.font().pointSizeF() == system + 2

    def rows_are_two_points_higher(factor: float) -> None:
        plain = QListWidget()  # the same text without the extra height
        plain.setFont(dialog.sidebar.font())
        plain.addItem(dialog.sidebar.item(0).text())
        assert dialog.sidebar.sizeHintForRow(0) == plain.sizeHintForRow(0) + round(2 * factor * dialog.logicalDpiY() / 72) > plain.sizeHintForRow(0)

    rows_are_two_points_higher(1.0)
    dialog.windows_scale_tried.connect(window.try_windows_scale)
    try:
        dialog.windows_slider.setValue(scale.slider_position(1.5))
        assert dialog.sidebar.font().pointSizeF() == (system + 2) * 1.5
        rows_are_two_points_higher(1.5)
        assert QApplication.font().pointSizeF() == system * 1.5  # the rest of the window: the desktop's size times the factor
    finally:
        window.try_windows_scale(1.0)


def test_cancel_puts_the_size_of_the_interface_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QDialog

    from chrono_mail.ui.settingsdialog import SettingsDialog

    window = world.window
    seen: list[float] = []

    def try_and_cancel(self: SettingsDialog) -> QDialog.DialogCode:
        self.interface_slider.setValue(scale.slider_position(1.75))
        seen.append(scale.interface())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SettingsDialog, "exec", try_and_cancel)
    window.open_settings("Appearance")
    assert seen == [1.75] and scale.interface() == 1.0 and window.section_bar.buttons["mail"].height() == 40


def test_the_keys_change_the_size_a_step_at_a_time_and_a_drag_applies_when_it_ends(world: World, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt as Keys

    from chrono_mail.ui.settingsdialog import SettingsDialog

    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    dialog.interface_scale_tried.connect(window.try_interface_scale)
    slider = dialog.interface_slider
    try:
        for key, expected in ((Keys.Key.Key_Up, 1.05), (Keys.Key.Key_Right, 1.10), (Keys.Key.Key_Down, 1.05), (Keys.Key.Key_Left, 1.00)):
            qtbot.keyClick(slider, key)
            assert scale.interface() == expected  # at once, one step
        slider.setSliderDown(True)  # the handle is held
        slider.setSliderPosition(scale.slider_position(1.5))
        assert scale.interface() == 1.0  # nothing changes while dragging
        slider.setSliderDown(False)  # let go
        assert scale.interface() == 1.5
    finally:
        window.try_interface_scale(1.0)


def test_the_settings_window_grows_with_the_other_windows_slider_within_the_screen(world: World, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt as Keys

    from chrono_mail.ui.settingsdialog import SLIDER_WIDTH, SettingsDialog

    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    dialog.windows_scale_tried.connect(window.try_windows_scale)
    dialog.show()
    qtbot.wait(50)
    width, height = dialog.width(), dialog.height()  # the test screen is 800 pixels: shrink first, then grow back
    try:
        for _ in range(4):  # four steps: 1.00 to 0.80
            qtbot.keyClick(dialog.windows_slider, Keys.Key.Key_Down)
        assert scale.windows() == 0.8 and width * 0.8 - 3 <= dialog.width() < width and height * 0.8 - 3 <= dialog.height() < height  # smaller, never below what the page needs
        assert dialog.windows_slider.minimumWidth() == round(SLIDER_WIDTH * 0.8)
        for _ in range(4):  # and back to 1.00: the size it had
            qtbot.keyClick(dialog.windows_slider, Keys.Key.Key_Up)
        assert width - 3 <= dialog.width() <= max(width, dialog._needed_size().width()) + 3 and height - 3 <= dialog.height() <= max(height, dialog._needed_size().height()) + 3  # noqa: SLF001 - the size it had, or what its page needs
        dialog.windows_slider.setValue(scale.SLIDER_STEPS)  # Large: never beyond the screen
        screen = dialog.screen()
        assert screen is not None and dialog.width() <= screen.availableGeometry().width() and dialog.height() <= screen.availableGeometry().height()
        dialog.reject()  # Cancel: the size it had
        assert width - 3 <= dialog.width() <= max(width, dialog._needed_size().width()) + 3 and height - 3 <= dialog.height() <= max(height, dialog._needed_size().height()) + 3  # noqa: SLF001 - the size it had, or what its page needs
    finally:
        window.try_windows_scale(1.0)


def test_the_size_slider_has_ticks_and_a_taller_one_at_the_standard_size(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.settingsdialog import TICK_LENGTH, SettingsDialog, SizeSlider

    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    dialog.show_page("Appearance")
    dialog.show()
    qtbot.wait(50)
    slider = dialog.interface_slider
    assert isinstance(slider, SizeSlider) and dialog.interface_standard.text() == "Reset"
    places = [slider.tick_x(factor) for factor in (0.75, 1.0, 1.25, 1.5, 1.75, 2.0)]
    gaps = {places[index + 1] - places[index] for index in range(len(places) - 1)}
    assert max(gaps) - min(gaps) <= 1  # every 0.25 the same distance, so one tick left of 1.00 and four right of it
    image = slider.grab().toImage()
    bottom = slider.tick_bottom()  # the ticks stand on the track
    background = image.pixelColor(places[0] + 3, bottom - 1).name()
    high = bottom - TICK_LENGTH - 1  # above the short ticks, within the tall one
    assert image.pixelColor(places[1], high).name() != background and image.pixelColor(places[2], high).name() == background
    assert all(image.pixelColor(x, bottom).name() != background for x in places[2:])  # they reach the track
    assert image.pixelColor(places[2], bottom + 1).name() != background  # and the track starts right below them


def test_at_the_smallest_size_the_slider_row_does_not_overlap(world: World, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt as Keys
    from PySide6.QtWidgets import QLabel

    from chrono_mail.ui.settingsdialog import SettingsDialog

    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    dialog.interface_scale_tried.connect(window.try_interface_scale)
    dialog.show_page("Appearance")
    dialog.show()
    qtbot.wait(50)
    try:
        qtbot.keyClick(dialog.interface_slider, Keys.Key.Key_Home)  # Small
        qtbot.wait(50)
        slider = dialog.interface_slider
        page = slider.parentWidget()
        assert page is not None
        small, large = (next(label for label in page.findChildren(QLabel) if label.text() == text) for text in ("Small", "Large"))
        row = [small.geometry(), slider.geometry(), large.geometry(), dialog.interface_standard.geometry()]
        assert all(row[index].right() < row[index + 1].left() for index in range(len(row) - 1))  # side by side, none over another
    finally:
        window.try_interface_scale(1.0)


def test_an_old_message_size_becomes_a_factor(tmp_path: Path) -> None:
    assert scale.saved_reading(settings(tmp_path)) == 1.0 and scale.saved_reading(settings(tmp_path, reader_zoom=120)) == 1.2
    assert scale.saved_reading(settings(tmp_path, reader_zoom=300)) == 2.0 and scale.saved_reading(settings(tmp_path, reader_zoom=50)) == 0.75  # within Small and Large
    assert scale.saved_reading(settings(tmp_path, reading_scale="1.4", reader_zoom=120)) == 1.4


def test_the_message_slider_works_at_once_for_reading_and_writing_and_cancel_puts_it_back(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import Qt as Keys
    from PySide6.QtWidgets import QDialog

    from chrono_mail.ui.settingsdialog import SettingsDialog

    window = world.window
    window.start()
    wait(qtbot, lambda: window.list_model.rowCount() == 3)
    window.new_message()
    wait(qtbot, lambda: len(window._composers) == 1)  # noqa: SLF001
    composer = window._composers[0]  # noqa: SLF001
    normal = composer.body.font().pointSizeF()
    seen: list[tuple[int, float]] = []

    def try_and_cancel(self: SettingsDialog) -> QDialog.DialogCode:
        for _ in range(2):
            qtbot.keyClick(self.reading_slider, Keys.Key.Key_Up)
        seen.append((window.reader.zoom, composer.body.font().pointSizeF()))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SettingsDialog, "exec", try_and_cancel)
    window.open_settings("Appearance")
    assert seen == [(110, round(normal * 1.1, 2))]  # two steps: 1.10, at once in the reading pane and the open composer
    assert window.reader.zoom == 100 and composer.body.font().pointSizeF() == normal  # Cancel: as they were


def test_a_blank_line_stands_above_the_sizes(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QLabel

    from chrono_mail.ui.settingsdialog import SettingsDialog

    window = world.window
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    dialog.show_page("Appearance")
    dialog.show()
    qtbot.wait(50)
    page = dialog.interface_slider.parentWidget()
    assert page is not None
    labels = page.findChildren(QLabel)
    note = next(label for label in labels if label.text().startswith("Dark needs a style"))
    sizes = next(label for label in labels if label.text() == "Sizes")
    assert sizes.geometry().top() - note.geometry().bottom() >= scale.px(18)


def test_an_old_list_size_becomes_a_factor(tmp_path: Path) -> None:
    assert scale.saved_list(settings(tmp_path), 10) == 1.0 and scale.saved_list(settings(tmp_path, list_font_size=0), 10) == 1.0  # System size
    assert scale.saved_list(settings(tmp_path, list_font_size=12), 10) == 1.2 and scale.saved_list(settings(tmp_path, list_scale="1.6", list_font_size=12), 10) == 1.6


@pytest.mark.parametrize("layout", ["columns", "cards"])
def test_the_list_slider_enlarges_rows_cards_icons_and_text_at_once(world: World, qtbot: QtBot, layout: str) -> None:
    from PySide6.QtCore import Qt as Keys

    from chrono_mail.ui.delegate import AVATAR_SIZE, CARD_HEADER_HEIGHT, COLUMN_ROW_HEIGHTS, ROW_HEIGHT
    from chrono_mail.ui.settingsdialog import SettingsDialog

    window = world.window
    window.start()
    wait(qtbot, lambda: window.list_model.rowCount() == 3)
    window.set_list_layout(layout)
    window.resize(1300, 700)
    window.show()
    qtbot.wait(50)
    font = window.list_view.font().pointSizeF()
    header_height = window.list_view.header().sizeHint().height()
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    dialog.list_scale_tried.connect(window.try_list_scale)
    try:
        for _ in range(10):  # 1.00 to 1.50, at once
            qtbot.keyClick(dialog.list_slider, Keys.Key.Key_Up)
        assert scale.message_list() == 1.5 and window.list_view.font().pointSizeF() == round(font * 1.5, 2)
        assert scale.lpx(AVATAR_SIZE) == 60 and scale.lpx(CARD_HEADER_HEIGHT) == 54
        height = window.list_view.sizeHintForRow(0)
        assert height == (scale.lpx(ROW_HEIGHT) if layout == "cards" else scale.lpx(COLUMN_ROW_HEIGHTS["normal"]))
        if layout == "columns":
            icon_column = window.list_model.columns.index("flag")
            assert window.list_view.columnWidth(icon_column) == scale.lpx(24) and window.list_view.header().sizeHint().height() > header_height
            header = window.list_view.header()
            for factor in (0.75, 1.5):  # the columns fill the list exactly at every size
                window.try_list_scale(factor)
                assert sum(header.sectionSize(index) for index in range(header.count())) == window.list_view.viewport().width()
        dialog.list_slider.setValue(scale.slider_position(1.5))
        dialog.list_reset.click()
        assert scale.message_list() == 1.0 and window.list_view.font().pointSizeF() == font
    finally:
        window.try_list_scale(1.0)


def test_the_interface_slider_leaves_the_list_and_message_sizes_alone(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.settingsdialog import SettingsDialog

    window = world.window
    window.start()
    wait(qtbot, lambda: window.list_model.rowCount() == 3)
    dialog = SettingsDialog(world.backend, window.settings, False, window._shortcut_registry(), window)  # noqa: SLF001
    qtbot.addWidget(dialog)
    for signal, method in ((dialog.interface_scale_tried, window.try_interface_scale), (dialog.list_scale_tried, window.try_list_scale), (dialog.reading_scale_tried, window.try_reading_scale)):
        signal.connect(method)
    try:
        dialog.interface_slider.setValue(scale.SLIDER_STEPS)  # 1. the interface to Large
        dialog.list_slider.setValue(scale.SLIDER_STEPS)  # 2. the message list to Large
        dialog.reading_slider.setValue(scale.SLIDER_STEPS)  # and the text of messages
        list_font = window.list_view.font().pointSizeF()
        dialog.interface_slider.setValue(0)  # 3. the interface to Small
        assert scale.interface() == 0.75 and scale.message_list() == 2.0 and window.list_view.font().pointSizeF() == list_font
        assert window.reader.zoom == 200
    finally:
        window.try_interface_scale(1.0)
        window.try_list_scale(1.0)
        window.try_reading_scale(1.0)



def test_an_older_settings_file_gives_the_other_windows_the_size_of_the_interface(tmp_path: Path) -> None:
    assert scale.saved_windows(settings(tmp_path), 10) == 1.0
    assert scale.saved_windows(settings(tmp_path, interface_scale="1.5"), 10) == 1.5  # they shared the interface size before
    assert scale.saved_windows(settings(tmp_path, interface_scale="1.5", windows_scale="1.25"), 10) == 1.25


def test_the_main_window_and_a_dialog_each_follow_their_own_slider(world: World, qtbot: QtBot, app: QApplication) -> None:
    from PySide6.QtWidgets import QDialog, QStyle

    window = world.window
    system = appearance.system_font_size(app)
    dialog = QDialog(window)
    qtbot.addWidget(dialog)
    try:
        window.try_interface_scale(1.5)
        assert window.font().pointSizeF() == system * 1.5 and window.section_bar.buttons["mail"].height() == 60
        assert dialog.font().pointSizeF() == system * 1.0
        window.try_windows_scale(1.25)
        assert dialog.font().pointSizeF() == system * 1.25 and window.font().pointSizeF() == system * 1.5
        style = app.style()
        assert isinstance(style, scale.ScaledStyle)
        normal = style.baseStyle().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize)
        assert style.pixelMetric(QStyle.PixelMetric.PM_SmallIconSize, None, window.tree) == round(normal * 1.5)
        assert style.pixelMetric(QStyle.PixelMetric.PM_SmallIconSize, None, dialog) == round(normal * 1.25)
    finally:
        window.try_interface_scale(1.0)
        window.try_windows_scale(1.0)


def test_a_menu_follows_the_window_it_opens_from(world: World, qtbot: QtBot, app: QApplication) -> None:
    from PySide6.QtWidgets import QDialog

    window = world.window
    system = appearance.system_font_size(app)
    dialog = QDialog(window)
    qtbot.addWidget(dialog)
    try:
        window.try_interface_scale(1.5)
        window.try_windows_scale(1.25)
        made_before = window.tag_menu  # a menu of the main window made at the start
        context = QMenu(window)  # a right-click menu, made when it is needed
        submenu = context.addMenu("Tag")
        in_dialog = QMenu(dialog)
        for menu in (context, submenu, in_dialog):
            menu.ensurePolished()
        assert made_before.font().pointSizeF() == context.font().pointSizeF() == submenu.font().pointSizeF() == system * 1.5
        assert in_dialog.font().pointSizeF() == system * 1.25
    finally:
        window.try_interface_scale(1.0)
        window.try_windows_scale(1.0)
