import xml.etree.ElementTree as ElementTree

import pytest
from PySide6.QtWidgets import QAbstractButton, QLabel, QSpacerItem, QTabWidget, QWidget
from pytestqt.qtbot import QtBot

from chrono_mail.ui import forms, scale
from chrono_mail.ui.forms import FormError
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.world import World, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_forms.py 4.0.378"

# --- Version history ----------------------------------------------------
# v4.0.378: the texts of the calendar, contacts and tasks windows fit at the smallest and largest size; Tab goes through a contact's entries
#           and addresses, and a colour added, where they are on screen
# v4.0.377: the texts of the mail windows fit at the smallest and largest size; Tab goes through the rule rows where they are on screen
# v4.0.376: the texts of the account, recovery, backup, restore and import windows fit at the smallest and largest size; Edit account shows
#           nothing of a tab the account has not; Tab reaches the found folders before OK; a backup's file name is never markup
# v4.0.375: loading leaves no stand-ins for the layouts' items; a form inside a form keeps its margins and sizes; a window takes its form's
#           title; the texts of the sync conflicts, Operations, rebuild windows and the send notice fit at the smallest and largest size;
#           a very long text never makes a window wider than its screen; a subject and an account name are never markup
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


def test_loading_a_form_leaves_no_stand_ins_for_the_items_of_its_layouts(qtbot: QtBot) -> None:
    """Python's stand-in for a layout's item outlived the window and was later taken for a part made at the same place in memory
    (opening Settings again after it was closed crashed the program, 4.0.374)."""
    import gc

    from PySide6.QtWidgets import QLayout, QLayoutItem

    host = QWidget()
    qtbot.addWidget(host)
    forms.fill(host, "settings_general")
    forms.load("settings_appearance", host)  # spacers of its own, scaled
    gc.collect()
    items = [found for found in gc.get_objects() if isinstance(found, QLayoutItem) and not isinstance(found, QLayout)]
    assert items == []


NESTED_OUTER = """<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>Outer</class>
 <widget class="QWidget" name="Outer">
  <property name="windowTitle"><string>Made in Designer</string></property>
  <layout class="QVBoxLayout" name="layout">
   <item><widget class="QWidget" name="inner"/></item>
  </layout>
 </widget>
</ui>
"""
NESTED_INNER = """<?xml version="1.0" encoding="UTF-8"?>
<ui version="4.0">
 <class>Inner</class>
 <widget class="QWidget" name="Inner">
  <layout class="QVBoxLayout" name="layout">
   <property name="leftMargin"><number>0</number></property>
   <property name="topMargin"><number>0</number></property>
   <property name="rightMargin"><number>0</number></property>
   <property name="bottomMargin"><number>0</number></property>
   <property name="spacing"><number>0</number></property>
   <item>
    <spacer name="blank">
     <property name="orientation"><enum>Qt::Vertical</enum></property>
     <property name="sizeType"><enum>QSizePolicy::Fixed</enum></property>
     <property name="sizeHint" stdset="0"><size><width>0</width><height>18</height></size></property>
    </spacer>
   </item>
   <item>
    <spacer name="none">
     <property name="orientation"><enum>Qt::Vertical</enum></property>
     <property name="sizeType"><enum>QSizePolicy::Fixed</enum></property>
     <property name="sizeHint" stdset="0"><size><width>0</width><height>0</height></size></property>
    </spacer>
   </item>
  </layout>
 </widget>
</ui>
"""


def test_a_form_inside_a_form_keeps_its_own_margins_and_sizes(qtbot: QtBot, tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """The outer form leaves the layouts of a part of the program alone, also one built from a form of its own with the same layout names;
    its spacers follow the size factor once, and a spacer of size 0 takes no room."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    monkeypatch.setattr(forms, "FORMS_DIRECTORY", tmp_path)
    (tmp_path / "outer.ui").write_text(NESTED_OUTER, "utf-8")
    (tmp_path / "inner.ui").write_text(NESTED_INNER, "utf-8")

    def inner(parent: QWidget) -> QWidget:
        made = QWidget(parent)
        forms.fill(made, "inner")
        return made

    scale.set_windows(2.0)
    try:
        host = QWidget()
        qtbot.addWidget(host)
        form = forms.load("outer", host, {"inner": inner})
        part = forms.part(form, QWidget, "inner")
        inside = forms.part(part, QWidget, "Inner")
        layout = inside.layout()
        assert layout is not None and layout.contentsMargins().left() == 0
        assert inside.sizeHint().height() == 36  # 18 at the factor 2, once; the spacer of 0 adds nothing
    finally:
        scale.set_windows(1.0)


def test_a_window_takes_its_title_from_its_form(qtbot: QtBot, tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from PySide6.QtWidgets import QDialog

    assert isinstance(tmp_path, Path)
    monkeypatch.setattr(forms, "FORMS_DIRECTORY", tmp_path)
    (tmp_path / "outer.ui").write_text(NESTED_OUTER.replace('<item><widget class="QWidget" name="inner"/></item>', ""), "utf-8")
    window = QDialog()
    qtbot.addWidget(window)
    forms.fill(window, "outer")
    assert window.windowTitle() == "Made in Designer"


def _texts_fit(window: QWidget, factor: float) -> None:
    for text in [*window.findChildren(QLabel), *window.findChildren(QAbstractButton)]:
        if text.isVisible() and text.text() and not (isinstance(text, QLabel) and text.wordWrap()):
            if text.minimumWidth() == text.maximumWidth():  # a part of a fixed width (a small square button): its text itself fits
                assert text.fontMetrics().horizontalAdvance(text.text()) <= text.width(), f"{window.windowTitle()}: '{text.text()}' is cut off at {factor}"
                continue
            assert text.width() >= text.minimumSizeHint().width(), f"{window.windowTitle()}: '{text.text()}' is cut off at {factor}"


@pytest.mark.parametrize("factor", [0.75, 2.0], ids=["smallest", "largest"])
def test_every_text_of_the_sync_and_operations_windows_fits(world: World, qtbot: QtBot, factor: float) -> None:
    from chrono_mail.engine.commands_conflicts import ConflictField, ConflictSummary
    from chrono_mail.ui.indexrebuilds import RebuildWindow
    from chrono_mail.ui.operations import OperationsWindow
    from chrono_mail.ui.sendnotices import SendNotice
    from chrono_mail.ui.syncconflicts import ConflictDialog, SyncConflictsWindow

    conflict = ConflictSummary(
        "c-1", "acc", "Account", "cal", "Work", "e1", "events", "both_changed", "Changed here and on the server", "Meeting", "The server said no",
        False, True, 1700000000.0, (ConflictField("summary", "Title", "Meeting", "Call"),),
    )
    world.window.try_windows_scale(factor)
    try:
        operations = OperationsWindow(lambda account, direction: f"{account} (POP3)", None, world.window)
        operations.add("anna@example.com", "receive")
        operations.add_error("bob@example.com", "The server could not be reached")
        rebuild = RebuildWindow(world.window)
        rebuild.setModal(False)
        rebuild.show_step(2, 3, "anna@example.com", 50, 100, True)
        windows: list[QWidget] = [
            SyncConflictsWindow(world.backend, world.window.sync_marks, None, world.window), ConflictDialog(conflict, world.window), operations, rebuild,
        ]
        notice = SendNotice("Holiday plans", 7, world.window)
        notice.stop()
        for window in windows:
            qtbot.addWidget(window)
            window.show()
        notice.show()
        notice.adjustSize()
        qtbot.wait(100)
        for window in windows:
            _texts_fit(window, factor)
            if window is operations:
                for tab in (1, 2):
                    operations.tabs.setCurrentIndex(tab)
                    qtbot.wait(50)
                    _texts_fit(window, factor)
        _texts_fit(notice, factor)
        notice.deleteLater()
    finally:
        world.window.try_windows_scale(1.0)


def test_a_very_long_text_never_makes_a_window_wider_than_its_screen(world: World, qtbot: QtBot) -> None:
    from chrono_mail.engine.commands_conflicts import ConflictField, ConflictSummary
    from chrono_mail.ui.syncconflicts import ConflictDialog

    long = "word " * 300
    conflict = ConflictSummary(
        "c-1", "acc", "Account", "cal", "Work", "e1", "events", "both_changed", "Changed here and on the server", "Meeting", "x" * 1500,
        False, True, 1700000000.0, (ConflictField("description", "Description", long, "short"),),
    )
    dialog = ConflictDialog(conflict, world.window)
    qtbot.addWidget(dialog)
    screen = dialog.screen()
    assert screen is not None and dialog.minimumWidth() <= screen.availableGeometry().width()


def test_a_subject_and_an_account_name_are_never_markup(world: World, qtbot: QtBot) -> None:
    from PySide6.QtCore import Qt

    from chrono_mail.ui.indexrebuilds import RebuildWindow
    from chrono_mail.ui.sendnotices import SendNotice

    notice = SendNotice("<b>Holiday</b>", 7, world.window)
    notice.stop()
    rebuild = RebuildWindow(world.window)
    qtbot.addWidget(rebuild)
    rebuild.show_step(1, 1, "<i>anna</i>", 1, 2, True)
    assert notice.label.textFormat() == Qt.TextFormat.PlainText and rebuild.account_label.textFormat() == Qt.TextFormat.PlainText
    notice.deleteLater()


def _account(world: World, kind: str) -> object:
    from dataclasses import replace

    from tests.ui.world import REGISTRY

    listed = REGISTRY.run(world.engine, "account.list", {})
    assert isinstance(listed, tuple)
    return replace(listed[0], kind=kind)


@pytest.mark.parametrize("factor", [0.75, 2.0], ids=["smallest", "largest"])
def test_every_text_of_the_account_and_safety_windows_fits(world: World, qtbot: QtBot, factor: float) -> None:
    from pathlib import Path

    from chrono_mail.engine.commands_accounts import AccountSummary, CertificateResult, KeyStatus
    from chrono_mail.engine.commands_backup import ContentsResult, ContentsRow
    from chrono_mail.engine.commands_dav import FoundFolder, FoundServer
    from chrono_mail.ui.accountdialog import AccountDialog
    from chrono_mail.ui.accountedit import AccountEditDialog
    from chrono_mail.ui.backupdialog import BackupDialog
    from chrono_mail.ui.calendaraccount import CalendarAccountDialog
    from chrono_mail.ui.certificatedialog import CertificateDialog
    from chrono_mail.ui.foundfolders import FoundFoldersDialog
    from chrono_mail.ui.importdialog import ImportDialog
    from chrono_mail.ui.recoverydialog import RecoveryDialog
    from chrono_mail.ui.restoredialog import RestoreDialog
    from chrono_mail.ui.restorekeydialog import RestoreKeyDialog

    parts = ("messages", "rules", "calendars", "contacts", "tasks", "settings")
    contents = ContentsResult("2026-10-20T10:00:00", "4.0.375", parts, (ContentsRow("acc1", "anna@example.com", parts, 12, 3, False),), 12, 3, True, True)
    found = FoundServer("https://dav.example.com/", "", (FoundFolder("a", "Work", "events", True, True, True),), "")
    certificate = CertificateResult("ab" * 32, "mail.example.org", "Home CA", ("mail.example.org",), "2026-01-01", "2026-12-31", True, False, "the issuer is not known")
    world.window.try_windows_scale(factor)
    try:
        windows: list[QWidget] = [
            AccountDialog(world.backend, True, world.window, lambda: None), CalendarAccountDialog(world.backend, [], world.window),
            FoundFoldersDialog(found, world.window), CertificateDialog("mail.example.org", certificate, world.window),
            RecoveryDialog(world.backend, world.window, True), RestoreKeyDialog(world.backend, world.window, KeyStatus("ab" * 16, False, "not there", kept=True)),
            BackupDialog(world.backend, world.window.settings, world.window),
            RestoreDialog(world.backend, world.window.settings, Path("backup.cmz"), None, contents, world.window), ImportDialog(world.backend, lambda: None, world.window),
        ]
        for kind in ("pop3", "imap"):
            summary = _account(world, kind)
            assert isinstance(summary, AccountSummary)
            windows.append(AccountEditDialog(world.backend, summary, world.window))
        for window in windows:
            qtbot.addWidget(window)
            window.show()
            window.resize(window.sizeHint())  # its own size: the test's screen (800 x 800) is smaller than a window at the largest size
            qtbot.wait(50)
            tabs = window.findChild(QTabWidget)
            for page in range(tabs.count() if tabs is not None else 1):
                if tabs is not None:
                    tabs.setCurrentIndex(page)
                    qtbot.wait(20)
                _texts_fit(window, factor)
            window.hide()
    finally:
        world.window.try_windows_scale(1.0)


@pytest.mark.parametrize("kind", ["imap", "local", "dav"])
def test_edit_account_shows_nothing_of_a_tab_the_account_has_not(world: World, qtbot: QtBot, kind: str) -> None:
    """A page the account has not was a child of the window outside its tabs and was drawn over them at the top left."""
    from chrono_mail.engine.commands_accounts import AccountSummary
    from chrono_mail.ui.accountedit import AccountEditDialog

    summary = _account(world, kind)
    assert isinstance(summary, AccountSummary)
    dialog = AccountEditDialog(world.backend, summary, world.window)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(50)
    from PySide6.QtCore import QPoint, QRect

    def place(widget: QWidget) -> QRect:
        return QRect(widget.mapTo(dialog, QPoint(0, 0)), widget.size())

    tabs = place(dialog.tabs)
    loose = [
        widget for widget in dialog.findChildren(QWidget)
        if widget.isVisible() and not dialog.tabs.isAncestorOf(widget) and not widget.isAncestorOf(dialog.tabs) and widget is not dialog.tabs
        and tabs.intersects(place(widget))
    ]
    assert loose == []


def test_tab_reaches_the_found_folders_before_the_buttons(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QCheckBox

    from chrono_mail.engine.commands_dav import FoundFolder, FoundServer
    from chrono_mail.ui.foundfolders import FoundFoldersDialog

    found = FoundServer("https://dav.example.com/", "", (FoundFolder("a", "Work", "events", False, False, True), FoundFolder("b", "Home", "events", False, False, True)), "")
    dialog = FoundFoldersDialog(found, world.window)
    qtbot.addWidget(dialog)
    order = [widget for widget in forms.tab_stops(dialog.folders) if isinstance(widget, QCheckBox)]
    chain: list[QWidget] = []
    current = dialog.nextInFocusChain()
    while current is not None and current is not dialog and len(chain) < 100:
        chain.append(current)
        current = current.nextInFocusChain()
    ok = next(widget for widget in chain if isinstance(widget, QAbstractButton) and widget.text() == "OK")
    assert len(order) == 2 and all(chain.index(tick) < chain.index(ok) for tick in order)


def test_a_backup_s_file_name_is_never_markup(world: World, qtbot: QtBot) -> None:
    from pathlib import Path

    from PySide6.QtCore import Qt

    from chrono_mail.engine.commands_backup import ContentsResult
    from chrono_mail.ui.restoredialog import RestoreDialog

    contents = ContentsResult("2026-10-20T10:00:00", "4.0.375", ("messages",), (), 0, 0, False, False)
    dialog = RestoreDialog(world.backend, world.window.settings, Path("<b>backup.cmz"), None, contents, world.window)
    qtbot.addWidget(dialog)
    names = [label for label in dialog.findChildren(QLabel) if label.text() == "<b>backup.cmz"]
    assert len(names) == 1 and names[0].textFormat() == Qt.TextFormat.PlainText


@pytest.mark.parametrize("factor", [0.75, 2.0], ids=["smallest", "largest"])
def test_every_text_of_the_mail_windows_fits(world: World, qtbot: QtBot, factor: float) -> None:
    from datetime import datetime

    from chrono_mail.engine.commands_export import SourceResult
    from chrono_mail.engine.commands_rules import RuleInfo
    from chrono_mail.ui.advancedsearch import AdvancedSearchDialog
    from chrono_mail.ui.columnsdialog import ColumnsDialog, ListChoice
    from chrono_mail.ui.ruleedit import RuleEditDialog
    from chrono_mail.ui.sourcedialog import SourceDialog
    from chrono_mail.ui.tagsdialog import TagEditor, TagRow
    from chrono_mail.ui.whendialog import WhenDialog

    rule = RuleInfo("r1", "News", True, "any", (), ("from:contains:news", "header.List-Id:contains:club", "attachment:is:yes"), ("move_to:Archive", "stop"))
    world.window.try_windows_scale(factor)
    try:
        windows: list[QWidget] = [
            AdvancedSearchDialog(world.window), ColumnsDialog(ListChoice("columns", ("from", "subject"), "right", "short", "normal"), world.window),
            RuleEditDialog(world.backend, [], ["Inbox"], rule, world.window), TagEditor(TagRow("Work", "#3366cc"), set(), world.window),
            WhenDialog("Schedule", "Send this message at:", "Schedule", world.window, datetime(2026, 10, 26, 14, 30), True),
            SourceDialog("Hello", SourceResult("From: a@example.com\n", 20, True), world.window),
        ]
        for window in windows:
            qtbot.addWidget(window)
            window.show()
            window.resize(window.sizeHint().expandedTo(window.minimumSize()))  # its own size: the test's screen is smaller than a window at the largest size
            qtbot.wait(50)
            _texts_fit(window, factor)
            window.hide()
    finally:
        world.window.try_windows_scale(1.0)


def test_tab_goes_through_the_rule_rows_where_they_are_on_screen(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.ruleedit import RuleEditDialog

    dialog = RuleEditDialog(world.backend, [], ["Inbox"], None, world.window)
    qtbot.addWidget(dialog)
    second = dialog.add_condition("subject:contains:x")
    action = dialog.add_action("stop")
    dialog.move_action(action, -1)  # now the first action
    chain: list[QWidget] = []
    current: QWidget | None = dialog.name
    while current is not None and len(chain) < 200 and current not in chain:
        chain.append(current)
        current = current.nextInFocusChain()
    first = dialog.condition_rows[0]
    order = [dialog.account, first.field, second.field, dialog.add_condition_button, action.kind, dialog.action_rows[1].kind, dialog.add_action_button, dialog.save_button]
    assert [chain.index(widget) for widget in order] == sorted(chain.index(widget) for widget in order)


def _calendar_summary(read_only: bool = False) -> object:
    from chrono_mail.core import OWN_COLOUR_ID
    from chrono_mail.core.pim import PaletteColour
    from chrono_mail.engine.commands_pim import CollectionSummary

    palette = (PaletteColour(OWN_COLOUR_ID, "Work", "#3366cc"), PaletteColour("c1", "Tomato", "#d50000"))
    return CollectionSummary("local", "On this computer", "cal1", "events", "Work", "#3366cc", read_only, 3, palette)


@pytest.mark.parametrize("factor", [0.75, 2.0], ids=["smallest", "largest"])
def test_every_text_of_the_calendar_contacts_and_tasks_windows_fits(world: World, qtbot: QtBot, factor: float) -> None:
    from chrono_mail.engine.commands_pim import CollectionSummary
    from chrono_mail.ui.contacteditor import ContactEditor
    from chrono_mail.ui.eventeditor import EventEditor
    from chrono_mail.ui.newcollection import NewCollectionDialog
    from chrono_mail.ui.paletteeditor import PaletteEditor
    from chrono_mail.ui.taskeditor import TaskEditor

    calendar = _calendar_summary()
    assert isinstance(calendar, CollectionSummary)
    lists = [CollectionSummary("local", "On this computer", "t1", "tasks", "Tasks", "#3366cc", False, 0)]
    books = [CollectionSummary("local", "On this computer", "b1", "contacts", "Contacts", "#3366cc", False, 0)]
    world.window.try_windows_scale(factor)
    try:
        contact = ContactEditor(world.backend, books, [], None, "b1", world.window)
        contact.add_address()
        windows: list[QWidget] = [
            EventEditor(world.backend, [calendar], [], None, "cal1", "", "2026-10-28T10:00", "2026-10-28T11:00", world.window),
            TaskEditor(world.backend, lists, [], None, "t1", world.window), contact, NewCollectionDialog(world.backend, "New calendar", "", world.window),
            PaletteEditor(world.backend, calendar, world.window),
        ]
        for window in windows:
            qtbot.addWidget(window)
            window.show()
            window.resize(window.sizeHint().expandedTo(window.minimumSize()))  # its own size: the test's screen is smaller than a window at the largest size
            qtbot.wait(50)
            _texts_fit(window, factor)
            window.hide()
    finally:
        world.window.try_windows_scale(1.0)


def _chain(start: QWidget) -> list[QWidget]:
    chain: list[QWidget] = []
    current: QWidget | None = start
    while current is not None and len(chain) < 400 and current not in chain:
        chain.append(current)
        current = current.nextInFocusChain()
    return chain


def test_tab_goes_through_the_contact_s_entries_and_addresses_where_they_are_on_screen(world: World, qtbot: QtBot) -> None:
    from chrono_mail.engine.commands_pim import CollectionSummary
    from chrono_mail.ui.contacteditor import ContactEditor

    books = [CollectionSummary("local", "On this computer", "b1", "contacts", "Contacts", "#3366cc", False, 0)]
    editor = ContactEditor(world.backend, books, [], None, "b1", world.window)
    qtbot.addWidget(editor)
    email = editor.emails.rows[0]
    second = editor.emails.add_row()
    address = editor.add_address()
    chain = _chain(editor.title)
    order = [editor.title, email.value, second.value, editor.emails.add_button, editor.phones.rows[0].value, address.street, editor.add_address_button, editor.has_birthday, editor.save_button]
    assert [chain.index(widget) for widget in order] == sorted(chain.index(widget) for widget in order)


def test_a_colour_added_comes_after_the_others_in_the_tab_order(world: World, qtbot: QtBot) -> None:
    from chrono_mail.engine.commands_pim import CollectionSummary
    from chrono_mail.ui.paletteeditor import PaletteEditor

    calendar = _calendar_summary()
    assert isinstance(calendar, CollectionSummary)
    editor = PaletteEditor(world.backend, calendar, world.window)
    qtbot.addWidget(editor)
    editor.add_button.click()
    chain = _chain(editor.rows[0].colour_button)
    order = [editor.rows[0].name, editor.rows[1].name, editor.rows[2].name, editor.add_button, editor.save_button]
    assert [chain.index(widget) for widget in order] == sorted(chain.index(widget) for widget in order)
