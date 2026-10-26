import xml.etree.ElementTree as ElementTree

import pytest
from PySide6.QtWidgets import QAbstractButton, QLabel, QSpacerItem, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui import forms, scale
from chrono_mail.ui.forms import FormError
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_forms.py 3.6.373"

# --- Version history ----------------------------------------------------
# v3.6.373: Initial: the dialogs' forms hold only parts, texts and layout; every form is built; every text of the Settings pages fits at the
#           smallest and the largest size (D-276)

FORM_FILES = sorted(forms.FORMS_DIRECTORY.glob("*.ui"))
LOOK = {"styleSheet", "palette", "font", "autoFillBackground"}  # colours (OR-9) and the rest of the look (OR-40, D-276) belong to the program
FIXED_SIZES = {"minimumSize", "maximumSize", "sizeIncrement", "baseSize", "geometry"}  # a fixed size cuts a text off at another size (OR-40, D-276)
PAGES = (
    "General", "Appearance", "Sidebar", "Message list", "Reading", "Writing", "Accounts", "Rules", "Tags", "Scheduled messages",
    "Operations window", "Security and storage", "Keyboard",
)


def test_the_forms_are_there() -> None:
    assert {path.stem for path in FORM_FILES} >= {"settings", "settings_general", "settings_keyboard"}


@pytest.mark.parametrize("path", FORM_FILES, ids=[path.stem for path in FORM_FILES])
def test_a_form_holds_no_colour_no_look_and_no_fixed_size(path: object) -> None:
    """OR-9: every colour is in core/colours.py; OR-40 with D-276: a form holds the parts, their texts and their layout, nothing else."""
    assert isinstance(path, type(forms.FORMS_DIRECTORY))
    root = ElementTree.parse(path).getroot().find("widget")
    assert root is not None
    for widget in root.iter("widget"):
        for item in widget.findall("property"):
            name = item.get("name")
            assert name not in LOOK, f"{path.name}: {widget.get('name')} sets {name}"
            assert name not in FIXED_SIZES or widget is root, f"{path.name}: {widget.get('name')} sets {name}"


@pytest.mark.parametrize("path", FORM_FILES, ids=[path.stem for path in FORM_FILES])
def test_every_form_is_built(qtbot: QtBot, path: object) -> None:
    assert isinstance(path, type(forms.FORMS_DIRECTORY))
    host = QWidget()
    qtbot.addWidget(host)
    form = forms.load(path.stem, host)
    assert form.parent() is host and form.objectName()


def test_a_missing_part_is_named_with_its_form(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    form = forms.load("settings_general", host)
    with pytest.raises(FormError, match="^The form settings_general.ui has no QLabel named noSuchPart$"):
        forms.part(form, QLabel, "noSuchPart")


def test_a_blank_line_of_a_form_follows_the_size_of_the_windows(qtbot: QtBot) -> None:
    def blank_line() -> int:
        host = QWidget()
        qtbot.addWidget(host)
        form = forms.load("settings_appearance", host)
        layout = form.layout()
        assert layout is not None
        spacers = [item for index in range(layout.count()) if isinstance(item := layout.itemAt(index), QSpacerItem)]
        return spacers[0].sizeHint().height()

    standard = blank_line()
    scale.set_windows(2.0)
    try:
        assert blank_line() == 2 * standard
    finally:
        scale.set_windows(1.0)


@pytest.mark.parametrize("factor", [0.75, 2.0], ids=["smallest", "largest"])
def test_every_text_of_the_settings_pages_fits(world: World, qtbot: QtBot, factor: float) -> None:
    world.window.try_windows_scale(factor)
    try:
        dialog = SettingsDialog(world.backend, world.window.settings, world.window._settings_context(), world.window)
        qtbot.addWidget(dialog)
        dialog.show()
        wait(qtbot, lambda: dialog.accounts.count() == 1)
        for page in PAGES:
            dialog.show_page(page)
            qtbot.wait(200)
            shown = dialog.pages.currentWidget()
            assert shown is not None
            for text in [*shown.findChildren(QLabel), *shown.findChildren(QAbstractButton)]:
                if text.isVisible() and text.text() and not (isinstance(text, QLabel) and text.wordWrap()):
                    assert text.width() >= text.minimumSizeHint().width(), f"{page}: '{text.text()}' is cut off at {factor}"
        dialog.reject()
    finally:
        world.window.try_windows_scale(1.0)


def test_a_part_of_the_program_takes_the_place_of_its_placeholder_in_the_tab_order_and_its_label(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, world.window.settings, world.window._settings_context(), world.window)
    qtbot.addWidget(dialog)

    def stops(page: str) -> list[QWidget]:
        """Where Tab stops on the page, in order (labels and the like are passed over)."""
        dialog.show_page(page)
        shown = dialog.pages.currentWidget()
        assert shown is not None
        return forms.tab_stops(shown)

    general = stops("General")
    assert general.index(dialog.startup_box) < general.index(dialog.check_on_start)  # the chooser first, as on screen
    appearance = stops("Appearance")
    sliders = [dialog.interface_slider, dialog.interface_standard, dialog.list_slider, dialog.list_reset, dialog.windows_slider, dialog.windows_reset]
    assert [appearance.index(widget) for widget in sliders] == sorted(appearance.index(widget) for widget in sliders)  # each slider before its Reset
    stops("Keyboard")
    edits = list(dialog.shortcut_edits.values())
    chain: list[QWidget] = []
    current = dialog.shortcut_table.nextInFocusChain()
    while current is not None and current is not dialog.shortcut_table and len(chain) <= len(dialog.findChildren(QWidget)):  # once round
        chain.append(current)
        current = current.nextInFocusChain()
    assert edits and all(edit in chain for edit in edits) and chain.index(edits[-1]) < chain.index(dialog.reset_keys_button)  # the key fields, then Standard keys
    tags = stops("Tags")
    assert tags.index(dialog.tags_panel.tabs) < tags.index(dialog.tags_panel.list)
    labels = [label for label in dialog.findChildren(QLabel) if label.text() == "Show on startup:"]
    assert len(labels) == 1 and labels[0].buddy() is dialog.startup_box


TRICKY_FORM = """<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>Tricky</class>
 <widget class="QWidget" name="Tricky">
  <layout class="QVBoxLayout" name="outer">
   <item><widget class="QLineEdit" name="first"/></item>
   <item><widget class="QWidget" name="holderOne"/></item>
   <item><widget class="QSpinBox" name="number"/></item>
   <item><widget class="QWidget" name="holderTwo"/></item>
   <item><widget class="QWidget" name="holderGroup"/></item>
   <item><widget class="QLineEdit" name="last"/></item>
  </layout>
 </widget>
</ui>
"""


def test_the_program_s_parts_take_their_placeholder_s_place_whatever_lies_between_them(qtbot: QtBot, tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

    assert isinstance(tmp_path, Path)
    monkeypatch.setattr(forms, "FORMS_DIRECTORY", tmp_path)

    def group(owner: QWidget) -> QWidget:  # a part that takes no focus itself, with two buttons of its own
        made = QWidget(owner)
        row = QHBoxLayout(made)
        row.addWidget(QPushButton("A", made))
        row.addWidget(QPushButton("B", made))
        return made

    for custom in (False, True):  # the form's own order, and an order set in Designer (which lists only parts that take the focus)
        text = TRICKY_FORM.replace("</ui>", "<tabstops><tabstop>last</tabstop><tabstop>number</tabstop><tabstop>first</tabstop></tabstops>\n</ui>") if custom else TRICKY_FORM
        (tmp_path / "tricky.ui").write_text(text, "utf-8")
        host = QWidget()
        qtbot.addWidget(host)
        form = forms.fill(host, "tricky", {"holderOne": QLineEdit, "holderTwo": QLineEdit, "holderGroup": group})
        first, last, number = forms.part(form, QLineEdit, "first"), forms.part(form, QLineEdit, "last"), forms.part(form, QWidget, "number")
        one, two = forms.part(form, QLineEdit, "holderOne"), forms.part(form, QLineEdit, "holderTwo")
        buttons = forms.part(form, QWidget, "holderGroup").findChildren(QPushButton)
        stops = forms.tab_stops(form)
        if custom:
            assert stops.index(last) < stops.index(number) < stops.index(first)
        else:
            assert stops == [first, one, number, two, *buttons, last]
        assert set(stops) == {first, one, number, two, *buttons, last}  # nothing lost, nothing outside the form


def test_a_placeholder_the_form_does_not_have_is_named(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    with pytest.raises(FormError, match="^The form settings_general.ui has no placeholder named noSuchPlace$"):
        forms.load("settings_general", host, {"noSuchPlace": QWidget})
