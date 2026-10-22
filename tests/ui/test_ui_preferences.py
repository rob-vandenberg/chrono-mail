import dataclasses
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from pytestqt.qtbot import QtBot

from chrono_mail.engine.commands_sidebar import LocalFolderParameters
from chrono_mail.ui import appearance, prefs, scale
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.engine.mailbox import open_mailbox
from chrono_mail.ui.statusreport import MISSING_WARNING
from chrono_mail.ui.settingsdialog import SettingsContext, SettingsDialog
from tests.ui.test_ui_composer import fill, new_composer
from tests.ui.test_ui_reader import Probe, load, probe, reader, shown  # noqa: F401 - fixtures
from tests.ui.world import World, row_of, settle, wait

__all__ = ["probe", "reader"]

# --- Version ------------------------------------------------------------
__version__ = "test_ui_preferences.py 2.0.298"

# --- Version history ----------------------------------------------------
# v2.0.298: Settings > Sidebar's Favorites ticks in the owner's new order (D-061)
# v2.0.290: Settings > Sidebar's Favorites ticks in the owner's order (D-061)
# v2.0.281: Rebuild the search index is done by the main window (rebuild_requested, D-229)
# v2.0.280: the Message list page shows and returns the columns of the open folder (B-16)
# v1.9.270: The Sidebar page's changes are typed steps (R-19)
# v1.9.265: the error line of an action on several messages is waited for (it comes once every answer is in, R-08)
# v1.9.264: The window's parts (R-17): what the tests reach is asked of the part that holds it
# v1.3.255: Settings OK takes into use only what changed: nothing at all without a change, a dragged reading pane width stays (R-13)
# v1.2.253: the Settings window hands back the typed settings (chosen(), R-12); every setting round trips
# v1.1.229: Sync conflicts left Settings for a window of its own
# v1.1.226: the Sync conflicts page after Scheduled messages
# v1.1.208: A closed message window leaves the list of open windows
# v1.1.204: A tick for each standard folder of Local Folders (greyed with Local Folders off, kept by Show on startup); OK hides it
# v1.1.194: The Sidebar page sits between Appearance and Message list
# v1.1.191: The Sidebar page (Favorites entries, pinned folders, Local Folders, accounts); what Show on startup chose is ticked and greyed; the last thing shown stays
# v1.1.182: Show on startup is a folded tree (StartupChooser); the Favorites page switches entries on and off after OK
# v0.5.164: rows of the message list are asked for with their column (the list has no index method of its own any more)
# v0.5.124: Show on startup: its list and the value saved (D-121)
# v0.4.119: A folder held back by a safety rule is a question; the answer is sent with mail.settle
# v0.3.116: The list is shown again after an action in which a message failed; the error stays
# v0.3.112: Repair the search index; one warning for messages whose file is gone
# v0.3.111: Maintenance once per place where mail is kept; its report can be selected
# v0.3.108: The size of messages, read and written, is set with its slider
# v0.2.100: Snoozed until keeps its place when the columns are chosen in Settings
# v0.2.98: Roomy is checked by the row height (36 px), not by the old row padding
# v0.2.96: The columns are chosen right on the Message list page
# v0.0.76: Exit has no keyboard shortcut
# v0.0.74: A message window uses and saves the allowed senders
# v0.0.52: Ctrl+Q marks as read, Quit is Ctrl+Shift+Q
# v0.0.51: Colour scheme and style settings, dark mode, icons that suit both schemes
# v0.0.50: Rules and scheduled messages are pages of the Settings window
# v0.0.44: links from the settings
# v0.0.42: Initial tests of the preferences and their effects


def settings_of(world: World) -> QSettings:
    return world.window.settings


def edit_settings(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, change: object) -> None:
    """Open the settings window, run `change` on the dialog, and press OK."""

    def fake_exec(self: SettingsDialog) -> QDialog.DialogCode:
        assert callable(change)
        change(self)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    world.window.act_settings.trigger()


def test_the_preference_helpers_cope_with_bad_values(tmp_path: Path) -> None:
    store = QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat)
    store.setValue("autosave_seconds", "many")
    store.setValue("mark_read", "sometimes")
    store.setValue("tray", "false")
    store.setValue("shortcuts", "{bad")
    assert prefs.get_int(store, "autosave_seconds", 0, 600) == 30 and prefs.get_text(store, "mark_read", prefs.MARK_READ) == "delay" and prefs.get_bool(store, "tray") is False
    assert prefs.shortcuts(store) == {}
    store.setValue("autosave_seconds", "9999")
    assert prefs.get_int(store, "autosave_seconds", 0, 600) == 600
    store.setValue("shortcuts", json.dumps({"a": "Ctrl+A", "b": 5, "c": "x" * 50}))
    assert prefs.shortcuts(store) == {"a": "Ctrl+A"}
    store.setValue("remote_senders", "A@Example.org\nnot an address\n b@x.org ")
    assert prefs.allowed_senders(store) == {"a@example.org", "b@x.org"}


def test_every_page_exists_and_the_values_round_trip(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, settings_of(world), SettingsContext(tray_available=True, shortcut_actions=world.window._shortcut_registry()))  # noqa: SLF001
    qtbot.addWidget(dialog)
    assert [dialog.sidebar.item(row).text() for row in range(dialog.sidebar.count())] == [
        "General", "Appearance", "Sidebar", "Message list", "Reading", "Writing", "Accounts", "Rules", "Tags", "Scheduled messages", "Operations window",
        "Security and storage", "Keyboard",
    ]
    chosen = dialog.chosen()
    dialog.save()
    assert prefs.read_preferences(settings_of(world)) == chosen  # every setting round trips through the file (R-12)
    for key, value in prefs.DEFAULTS.items():
        if isinstance(value, bool):
            assert prefs.get_bool(settings_of(world), key) is getattr(chosen, key), key


def test_marking_as_read_follows_the_setting(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)

    def never(dialog: SettingsDialog) -> None:
        dialog.mark_read_box.setCurrentIndex(dialog.mark_read_box.findData("never"))

    edit_settings(world, qtbot, monkeypatch, never)
    unread = next(position for position in range(3) if "seen" not in row_of(world.window, position).flags)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(unread, 0))
    wait(qtbot, lambda: world.window.reading.shown is not None)  # noqa: SLF001
    qtbot.wait(1200)
    assert "seen" not in row_of(world.window, unread).flags


def test_a_long_or_short_delay_for_marking_read(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)

    def quick(dialog: SettingsDialog) -> None:
        dialog.mark_read_delay.setValue(0)

    edit_settings(world, qtbot, monkeypatch, quick)
    assert prefs.get_int(settings_of(world), "mark_read_delay_ms", 0, 10_000) == 0
    unread = next(position for position in range(3) if "seen" not in row_of(world.window, position).flags)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(unread, 0))
    wait(qtbot, lambda: "seen" in row_of(world.window, unread).flags, timeout=3000)


def test_the_look_of_the_list_follows_the_settings(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def change(dialog: SettingsDialog) -> None:
        dialog.density_box.setCurrentIndex(dialog.density_box.findData("roomy"))
        dialog.list_slider.setValue(scale.slider_position(1.5))
        dialog.layout_box.setCurrentIndex(dialog.layout_box.findData("cards"))
        dialog.pane_box.setCurrentIndex(dialog.pane_box.findData("bottom"))
        dialog.date_box.setCurrentIndex(dialog.date_box.findData("iso"))

    edit_settings(world, qtbot, monkeypatch, change)
    window = world.window
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    assert window.column_delegate.row_height == 36 and scale.message_list() == 1.5  # Roomy, drawn at 1.5 (scale.lpx)
    assert window.list_view.font().pointSizeF() == round(appearance.system_font_size(app) * 1.5, 2)
    assert window.list_model.columns == ("card",) and window.content_splitter.orientation().name == "Vertical" and window.list_model.date_style == "iso"


def test_the_columns_are_chosen_on_the_message_list_page(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, settings_of(world), SettingsContext(tray_available=True, columns=("subject", "snoozed")))  # the open folder's
    qtbot.addWidget(dialog)
    picker = dialog.columns_picker
    assert not hasattr(dialog, "columns_button") and picker.list.count() > 5
    for row in range(picker.list.count()):
        picker.list.item(row).setCheckState(Qt.CheckState.Unchecked)
    picker.list.item(3).setCheckState(Qt.CheckState.Checked)
    chosen = str(picker.list.item(3).data(Qt.ItemDataRole.UserRole))
    assert dialog.chosen_columns() == ("snoozed", chosen)  # Snoozed until is not offered here and keeps its place (Subject before it went)
    dialog.layout_box.setCurrentIndex(dialog.layout_box.findData("cards"))
    assert not picker.list.isEnabled() and not picker.reset_button.isEnabled()
    dialog.layout_box.setCurrentIndex(dialog.layout_box.findData("columns"))
    assert picker.list.isEnabled()


def test_plain_text_first_and_remote_content_settings(reader: ReaderWidget, qtbot: QtBot, probe: Probe) -> None:
    both = shown(html=f"<p>HTML version <img src='{probe.url()}'></p>", text="Plain version")
    reader.prefer_plain = True
    reader.show_message(both)
    assert not reader.showing_html() and "Plain version" in reader.text_view.toPlainText()
    reader.prefer_plain = False
    reader.always_remote = True
    load(qtbot, reader, both)
    qtbot.waitUntil(lambda: len(probe.requests) >= 1, timeout=8000)  # the person said: always show
    assert not reader.banner_visible()
    reader.always_remote = False
    probe.requests.clear()
    reader.allowed_senders = {"alice@example.com"}
    load(qtbot, reader, both)
    qtbot.waitUntil(lambda: len(probe.requests) >= 1, timeout=8000)  # this sender is on the list
    reader.allowed_senders = set()
    probe.requests.clear()
    load(qtbot, reader, both)
    qtbot.wait(300)
    assert probe.requests == [] and reader.banner_visible()


def test_always_for_this_sender_remembers_the_address(reader: ReaderWidget, qtbot: QtBot, probe: Probe) -> None:
    both = shown(html=f"<p><img src='{probe.url()}'></p>", text="")
    got: list[str] = []
    reader.sender_allowed.connect(got.append)
    load(qtbot, reader, both)
    qtbot.waitUntil(reader.banner_visible, timeout=8000)
    reader._always_remote.click()  # noqa: SLF001
    assert got == ["alice@example.com"] and "alice@example.com" in reader.allowed_senders
    qtbot.waitUntil(lambda: len(probe.requests) >= 1, timeout=8000)


def test_the_window_stores_an_allowed_sender_and_the_zoom_applies(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.reading.sender_allowed("New@Example.org")  # noqa: SLF001
    assert prefs.allowed_senders(settings_of(world)) == {"new@example.org"}

    def change(dialog: SettingsDialog) -> None:
        dialog.reading_slider.setValue(scale.slider_position(1.5))
        dialog.prefer_plain.setChecked(True)
        dialog.remote_always.setChecked(True)

    edit_settings(world, qtbot, monkeypatch, change)
    reader = world.window.reader
    assert reader.zoom == 150 and reader.prefer_plain and reader.always_remote and "new@example.org" in reader.allowed_senders

    def forget(dialog: SettingsDialog) -> None:
        dialog.forget_senders_button.click()

    edit_settings(world, qtbot, monkeypatch, forget)
    assert prefs.allowed_senders(settings_of(world)) == set() and not world.window.reader.allowed_senders


def test_links_open_without_asking_when_the_setting_says_so(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    def record(url: QUrl) -> bool:
        opened.append(url.toString())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(record))
    world.window.reading.open_link(QUrl("https://example.org/a"))  # noqa: SLF001
    assert opened == ["https://example.org/a"] and any("Open link" == title for _, title, _ in world.dialogs.shown)  # asked first
    opened.clear()
    world.dialogs.shown.clear()

    def change(dialog: SettingsDialog) -> None:
        dialog.links_ask.setChecked(False)

    edit_settings(world, qtbot, monkeypatch, change)
    world.window.reading.open_link(QUrl("https://example.org/b"))  # noqa: SLF001
    assert opened == ["https://example.org/b"] and world.dialogs.shown == []
    world.window.reading.open_link(QUrl("javascript:alert(1)"))  # noqa: SLF001
    assert opened == ["https://example.org/b"]


def test_writing_settings_reach_the_composer(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def change(dialog: SettingsDialog) -> None:
        dialog.autosave.setValue(0)
        dialog.warn_no_subject.setChecked(False)
        dialog.reading_slider.setValue(scale.slider_position(1.6))  # one size for reading and writing
        dialog.reply_quote_box.setCurrentIndex(dialog.reply_quote_box.findData("below"))

    edit_settings(world, qtbot, monkeypatch, change)
    options = world.window.composing.compose_options()  # noqa: SLF001
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    points = round(appearance.system_font_size(app) * 1.6, 2)
    assert (options.autosave_seconds, options.warn_no_subject, options.font_size, options.reply_below) == (0, False, points, True)
    world.window.start()
    settle(qtbot, world, 3)
    world.window.composing.new_message()
    wait(qtbot, lambda: len(world.window.composing.composers) == 1)  # noqa: SLF001
    composer = world.window.composing.composers[0]  # noqa: SLF001
    assert not composer.autosave_timer.isActive() and composer.body.font().pointSizeF() == points
    fill(composer, subject="")
    composer.send_button.click()  # no question about the missing subject
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    assert not any(title == "Send" for _, title, _ in world.dialogs.shown)


def test_replies_follow_the_quote_setting_and_the_signature_goes_to_the_end(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("account.edit", account=world.account_id, signature="Rob")

    def change(dialog: SettingsDialog) -> None:
        dialog.reply_quote_box.setCurrentIndex(dialog.reply_quote_box.findData("below"))

    edit_settings(world, qtbot, monkeypatch, change)
    world.window.start()
    settle(qtbot, world, 3)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0, 0))
    world.window.act_reply.trigger()
    wait(qtbot, lambda: len(world.window.composing.composers) == 1)  # noqa: SLF001
    text = world.window.composing.composers[0].body.toPlainText()  # noqa: SLF001
    assert text.index("wrote:") < text.index("-- \nRob") and text.rstrip().endswith("Rob")
    first = world.window.composing.composers[0]  # noqa: SLF001
    first._finished = True  # noqa: SLF001
    first.close()

    def none(dialog: SettingsDialog) -> None:
        dialog.reply_quote_box.setCurrentIndex(dialog.reply_quote_box.findData("none"))

    edit_settings(world, qtbot, monkeypatch, none)
    world.window.act_reply.trigger()
    wait(qtbot, lambda: len(world.window.composing.composers) == 1 and world.window.composing.composers[0] is not first)  # noqa: SLF001 - the closed one is gone
    plain = world.window.composing.composers[0].body.toPlainText()  # noqa: SLF001
    assert "wrote:" not in plain and "> " not in plain
    for composer in world.window.composing.composers:  # noqa: SLF001
        composer._finished = True  # noqa: SLF001
        composer.close()


def test_a_default_account_is_used_for_new_messages(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_engine_commands import add_account

    second = add_account(world.engine, world.pop, world.smtp, email="second@example.com")
    world.window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: len(world.window.mail_state.accounts) == 2)  # noqa: SLF001
    world.window.start()
    settle(qtbot, world, 3)

    def change(dialog: SettingsDialog) -> None:
        wait(qtbot, lambda: dialog.default_account.count() == 3)
        dialog.default_account.setCurrentIndex(dialog.default_account.findData(second))

    edit_settings(world, qtbot, monkeypatch, change)
    world.window.composing.new_message()
    wait(qtbot, lambda: len(world.window.composing.composers) == 1)  # noqa: SLF001
    assert world.window.composing.composers[0].from_box.currentData() == second  # noqa: SLF001
    world.window.composing.composers[0]._finished = True  # noqa: SLF001
    world.window.composing.composers[0].close()  # noqa: SLF001


def test_the_security_page_shows_the_key_and_can_check_and_rebuild(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, settings_of(world), SettingsContext(tray_available=True))
    dialog.rebuild_requested.connect(world.window.rebuilds.rebuild_all)  # as the main window does
    qtbot.addWidget(dialog)
    dialog.show()
    wait(qtbot, lambda: "encryption key" in dialog.key_label.text())
    dialog.verify_button.click()
    wait(qtbot, lambda: "all can be read" in dialog.maintenance_label.text() and dialog.verify_button.isEnabled())
    assert "3 messages checked" in dialog.maintenance_label.text()
    dialog.rebuild_button.click()
    wait(qtbot, lambda: "messages indexed" in dialog.maintenance_label.text() and dialog.rebuild_button.isEnabled())
    assert "3 messages indexed" in dialog.maintenance_label.text()
    assert any(str(world.engine.directories.data_directory) in label.text() for label in dialog.findChildren(QLabel))


def test_maintenance_works_once_on_local_folders_shared_by_accounts(world: World, qtbot: QtBot) -> None:
    world.call("account.set-storage", account=world.account_id, storage="local")  # the account's mail moves into Local Folders
    dialog = SettingsDialog(world.backend, settings_of(world), SettingsContext(tray_available=True))
    dialog.rebuild_requested.connect(world.window.rebuilds.rebuild_all)  # as the main window does
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.maintenance_label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    for button, done in ((dialog.rebuild_button, "messages indexed"), (dialog.verify_button, "messages checked")):
        button.click()
        wait(qtbot, lambda: done in dialog.maintenance_label.text() and button.isEnabled())
        lines = dialog.maintenance_label.text().split("<br>")
        assert len(lines) == 1 and lines[0].startswith("Local Folders: 3 "), lines  # one line, no error from a second run on the same index


def test_keys_can_be_changed_checked_for_clashes_and_reset(world: World, qtbot: QtBot) -> None:
    window = world.window
    registry = window._shortcut_registry()  # noqa: SLF001
    assert "reply" in registry and registry["reply"][1] == "Ctrl+R" and "rules" in registry
    dialog = SettingsDialog(world.backend, settings_of(world), SettingsContext(tray_available=True, shortcut_actions=registry))
    qtbot.addWidget(dialog)
    dialog.shortcut_edits["reply"].setKeySequence(QKeySequence("Ctrl+Shift+Y"))
    dialog.shortcut_edits["reply_all"].setKeySequence(QKeySequence("Ctrl+Shift+Y"))
    assert dialog.shortcut_conflicts() and "Ctrl+Shift+Y" in dialog.shortcut_conflicts()[0]
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted and "twice" in dialog.shortcut_note.text()
    dialog.shortcut_edits["reply_all"].setKeySequence(QKeySequence("Ctrl+Shift+A"))
    dialog.save()
    window.apply_preferences()
    assert window.act_reply.shortcut().toString() == "Ctrl+Shift+Y" and window.act_reply_all.shortcut().toString() == "Ctrl+Shift+A"
    assert json.loads(str(settings_of(world).value("shortcuts"))) == {"reply": "Ctrl+Shift+Y", "reply_all": "Ctrl+Shift+A"}  # only what differs
    dialog.reset_keys_button.click()
    dialog.save()
    window.apply_preferences()
    assert window.act_reply.shortcut().toString() == "Ctrl+R" and json.loads(str(settings_of(world).value("shortcuts"))) == {}
    dialog.shortcut_edits["rules"].setKeySequence(QKeySequence("Ctrl+Alt+R"))  # an action that had no key gets one
    dialog.save()
    window.apply_preferences()
    assert window.act_rules.shortcut().toString() == "Ctrl+Alt+R"


def test_rules_and_scheduled_messages_are_pages_of_the_settings(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, settings_of(world), SettingsContext(tray_available=True))
    qtbot.addWidget(dialog)
    titles = [dialog.sidebar.item(row).text() for row in range(dialog.sidebar.count())]
    assert "Rules" in titles and "Scheduled messages" in titles
    dialog.show_page("Scheduled messages")
    assert dialog.pages.currentWidget() is dialog.scheduled_panel
    assert not hasattr(dialog, "rules_button")


def test_ctrl_q_marks_as_read_and_quit_has_another_key(world: World) -> None:
    window = world.window
    assert window.act_read.shortcut().toString() == "Ctrl+Q" and window.act_quit.text() == "Exit" and window.act_quit.shortcut().toString() == ""  # Exit has no key
    keys = [action.shortcut().toString() for name, action in vars(window).items() if name.startswith("act_") and hasattr(action, "shortcut") and action.shortcut().toString()]
    assert len(keys) == len(set(keys))  # no key is given twice


def message_window(world: World, qtbot: QtBot, html: str) -> MessageWindow:
    window = MessageWindow(world.window.backend, "a", "INBOX", "m", shown(html=html, text=""), lambda *args: None, lambda url: None, world.window.reading.prepare_message_reader)  # noqa: SLF001
    qtbot.addWidget(window)
    window.show()
    return window


def test_a_message_window_saves_always_for_this_sender(world: World, qtbot: QtBot, probe: Probe) -> None:
    window = message_window(world, qtbot, f"<p><img src='{probe.url()}'></p>")
    qtbot.waitUntil(window.reader.banner_visible, timeout=8000)
    window.reader._always_remote.click()  # noqa: SLF001
    assert prefs.allowed_senders(settings_of(world)) == {"alice@example.com"}


def test_a_message_window_knows_the_allowed_senders_before_it_shows_the_message(world: World, qtbot: QtBot, probe: Probe) -> None:
    settings_of(world).setValue("remote_senders", "alice@example.com")
    window = message_window(world, qtbot, f"<p><img src='{probe.url()}'></p>")
    qtbot.waitUntil(lambda: len(probe.requests) >= 1, timeout=8000)  # shown at once: no banner, no second load
    assert not window.reader.banner_visible()


@pytest.mark.parametrize("rich", [True, False])
def test_new_messages_start_in_the_way_of_writing_chosen(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, rich: bool) -> None:
    def change(dialog: SettingsDialog) -> None:
        assert [dialog.compose_html.itemText(index).split(" (")[0] for index in range(dialog.compose_html.count())] == ["Rich text", "Plain text"]
        dialog.compose_html.setCurrentIndex(dialog.compose_html.findData(rich))

    edit_settings(world, qtbot, monkeypatch, change)
    assert prefs.get_bool(settings_of(world), "compose_html") is rich
    world.window.start()
    settle(qtbot, world, 3)
    world.window.composing.new_message()
    wait(qtbot, lambda: len(world.window.composing.composers) == 1)  # noqa: SLF001
    composer = world.window.composing.composers[0]  # noqa: SLF001
    assert composer.is_rich() is rich and composer.rich_button.text() == ("Rich text" if rich else "Plain text")



def lose_files(world: World, message_ids: list[str]) -> None:
    """The files of these messages disappear; the index still lists them."""
    with open_mailbox(world.engine, world.account_id) as mailbox:
        for message_id in message_ids:
            for path in mailbox.store.root.rglob(f"{message_id}*"):
                path.unlink()


def test_repair_removes_messages_whose_file_is_gone(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    lose_files(world, [row_of(world.window, 0).message_id])
    dialog = SettingsDialog(world.backend, settings_of(world), SettingsContext(tray_available=True))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.repair_button.click()
    wait(qtbot, lambda: "removed" in dialog.maintenance_label.text() and dialog.repair_button.isEnabled())
    assert f"{world.account_id}" not in dialog.maintenance_label.text() and ": 1 removed (no file), 0 added, 0 corrected" in dialog.maintenance_label.text()


def test_messages_whose_file_is_gone_give_one_warning_per_action(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    rows = [row_of(window, position) for position in range(3)]
    lose_files(world, [row.message_id for row in rows])
    window.mail_actions.delete_rows(rows[:2])  # noqa: SLF001 - two messages to Trash: they are gone, removed from the list
    wait(qtbot, lambda: window.list_model.message_count() == 1 and window.status_label.text() == MISSING_WARNING)
    assert [shown for shown in world.dialogs.shown if shown[1] == "Messages missing"] == [("warning", "Messages missing", MISSING_WARNING)]
    window.list_view.setCurrentIndex(window.list_model.index(0, 0))
    window.mail_actions.mark_selected(flagged=True)  # flagging only warns: the message stays in the list
    wait(qtbot, lambda: len([shown for shown in world.dialogs.shown if shown[1] == "Messages missing"]) == 2)
    assert window.list_model.message_count() == 1 and window.status_label.text() == MISSING_WARNING


def test_the_list_is_refreshed_when_a_message_of_an_action_fails_and_the_error_stays(world: World, qtbot: QtBot) -> None:
    window = world.window
    window.start()
    settle(qtbot, world, 3)
    rows = [row_of(window, position) for position in range(3)]
    broken = dataclasses.replace(rows[1], folder="No such folder")  # its delete fails
    window.mail_actions.delete_rows([rows[0], broken])  # noqa: SLF001
    wait(qtbot, lambda: window.list_model.message_count() == 2)  # the deleted one is gone, the failed one stays
    wait(qtbot, lambda: window.status_label.text().startswith("Could not delete"))  # one line once every answer is in (R-08)
    lose_files(world, [rows[2].message_id])  # its move fails: the file is gone
    window.list_view.selectionModel().clearSelection()
    for row in (rows[1], rows[2]):
        position = next(index for index in range(window.list_model.rowCount()) if row_of(window, index) is not None and row_of(window, index).message_id == row.message_id)
        window.list_view.selectionModel().select(window.list_model.index(position, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    window.mail_actions.move_selected("Trash")
    wait(qtbot, lambda: window.list_model.message_count() == 1 and window.status_label.text() == MISSING_WARNING)


def test_a_folder_held_back_is_a_question_and_the_answer_is_sent(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from chrono_mail.ui import mailchecking

    window = world.window
    window.reload_accounts(select_saved=False)
    asked: list[tuple[str, list[str]]] = []
    answers: list[int | None] = [None, 1]

    def ask(parent: object, title: str, text: str, buttons: list[str]) -> int | None:
        asked.append((text, buttons))
        return answers.pop(0)

    calls: list[tuple[str, dict[str, object]]] = []

    def record(command: str, values: dict[str, object], *rest: object, **named: object) -> None:
        calls.append((command, values))

    monkeypatch.setattr(mailchecking, "ask_choice", ask)
    monkeypatch.setattr(window.backend, "call", record)
    held = SimpleNamespace(kind="missing_here", folder="Trash", count=58)
    window.checking.ask_about_blocked(world.account_id, held)  # noqa: SLF001 - the window is closed: nothing happens, asked again later
    assert calls == [] and "58 messages that you deleted in Chrono Mail are still in Trash on the server of" in asked[0][0]
    assert asked[0][1] == ["Delete them on the server too", "Bring them back here"]
    window.checking.ask_about_blocked(world.account_id, held)  # noqa: SLF001
    assert calls == [("mail.settle", {"account": world.account_id, "folder": "Trash", "choice": "restore_here"})]


def test_show_on_startup_offers_the_choices_and_saves_the_one_picked(world: World, qtbot: QtBot) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.tree_model.rowCount() > 0 and world.window.list_model.rowCount() == 3)
    choices = world.window.folders.startup_choices()  # noqa: SLF001
    dialog = SettingsDialog(world.backend, settings_of(world), SettingsContext(tray_available=True, startup_choices=tuple(choices)))
    qtbot.addWidget(dialog)
    box = dialog.startup_box
    assert box.currentIndex() == 0 and box.currentText() == "Last selected folder"
    headings = [box.tree_model.item(row) for row in range(box.tree_model.rowCount()) if box.tree_model.item(row).hasChildren()]
    assert headings and not any(item.isSelectable() for item in headings)  # Favorites and the account cannot be picked
    inbox = f"mail\t{world.account_id}\tfolder\tInbox"
    assert box.choose_value(inbox) and not box.choose_value("-")
    assert box.currentText() == "Inbox" and dialog.chosen().startup.text == inbox
    dialog.save()
    again = SettingsDialog(world.backend, settings_of(world), SettingsContext(tray_available=True, startup_choices=tuple(choices)))
    qtbot.addWidget(again)
    assert again.startup_box.currentData() == inbox


def test_the_sidebar_page_switches_entries_on_and_off_after_ok(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.call("favorite.pin", account=world.account_id, folder="Sent")
    world.window.start()
    wait(qtbot, lambda: world.window.tree_model.rowCount() > 0 and len(world.window.tree_model.pins) == 1)

    def change(dialog: SettingsDialog) -> None:
        page = dialog.sidebar_page
        assert list(page.standard_boxes) == ["inboxes", "unread", "outbox", "sent", "drafts", "flagged", "unreplied", "junk", "trash"]  # Snoozed and Archive: not used
        assert all(box.isChecked() for box in page.standard_boxes.values()) and len(page.pin_boxes) == 1
        assert list(page.account_boxes) == [world.account_id]  # an account with its own folders
        assert page.commands() == []  # nothing changed: nothing is sent
        page.standard_boxes["unread"].setChecked(False)
        next(iter(page.pin_boxes.values())).setChecked(False)
        page.local_box.setChecked(False)
        page.account_boxes[world.account_id].setChecked(False)  # the last thing still shown besides Favorites

    edit_settings(world, qtbot, monkeypatch, change)
    wait(qtbot, lambda: world.window.tree_model.hidden == {"unread"} and world.window.tree_model.pins == [] and world.window.tree_model.hidden_trees == {world.account_id})
    sidebar = world.call("sidebar.show")
    assert tuple(sidebar.hidden) == ("unread",) and tuple(sidebar.pins) == () and tuple(sidebar.hidden_trees) == (world.account_id,)


def test_what_show_on_startup_chose_is_ticked_and_greyed(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.sidebarpage import SidebarPage

    standard = (("inboxes", "All Inboxes"), ("unread", "Unread"))
    page = SidebarPage(standard, {"unread"}, [], True, True, True, True, "1" * 32, [("a" * 32, "one")], set(), "mail\t\tfavorite\tunread")
    qtbot.addWidget(page)
    unread = page.standard_boxes["unread"]
    assert unread.isChecked() and not unread.isEnabled() and not page.favorites_box.isEnabled()  # chosen at startup: always shown
    page.set_startup("mail\t" + "a" * 32 + "\tfolder\tInbox")  # another startup choice: Unread is unticked again, as the person left it
    assert not unread.isChecked() and unread.isEnabled() and page.account_boxes["a" * 32].isChecked() and not page.account_boxes["a" * 32].isEnabled()
    page.set_startup("")
    for box in (page.favorites_box, page.tags_box, page.local_box):
        assert box is not None
        box.setChecked(False)
    last = page.account_boxes["a" * 32]
    assert last.isChecked() and not last.isEnabled()  # the last thing still shown cannot be switched off
    assert page.values() == {"show_favorites": False, "show_tags": False, "show_local_folders": False}



def test_the_standard_folders_of_local_folders_have_a_tick(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.sidebarpage import SidebarPage

    local = "1" * 32
    folders = [("inbox", "Inbox"), ("trash", "Trash"), ("snoozed", "Snoozed")]
    page = SidebarPage((), set(), [], True, True, True, False, local, [], set(), "", local_folders=folders, hidden_local={"trash"})
    qtbot.addWidget(page)
    boxes = page.local_folder_boxes
    assert list(boxes) == ["inbox", "trash", "snoozed"] and [box.text() for box in boxes.values()] == ["Inbox", "Trash", "Snoozed"]
    assert boxes["inbox"].isChecked() and not boxes["trash"].isChecked() and page.commands() == []
    page.local_box.setChecked(False)
    assert not any(box.isEnabled() for box in boxes.values())  # greyed while Local Folders is off
    page.local_box.setChecked(True)
    boxes["inbox"].setChecked(False)
    page.set_startup(f"mail\t{local}\tfolder\tInbox/Sub")  # a folder inside Inbox chosen at startup: Inbox stays shown
    assert boxes["inbox"].isChecked() and not boxes["inbox"].isEnabled() and not page.local_box.isEnabled()
    page.set_startup("")
    assert not boxes["inbox"].isChecked() and boxes["inbox"].isEnabled()  # the person's own tick again
    boxes["trash"].setChecked(True)
    assert [(step.command.id, step.parameters) for step in page.commands()] == [("sidebar.show-local-folder", LocalFolderParameters("inbox", False)), ("sidebar.show-local-folder", LocalFolderParameters("trash", True))]  # type: ignore[attr-defined]


def test_the_sidebar_page_follows_show_on_startup_and_cancel_changes_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    wait(qtbot, lambda: world.window.tree_model.rowCount() > 0)
    before = world.call("sidebar.show")

    def cancelled(dialog: SettingsDialog) -> QDialog.DialogCode:
        page = dialog.sidebar_page
        unread = page.standard_boxes["unread"]
        assert unread.isEnabled()
        assert dialog.startup_box.choose_value("mail\t\tfavorite\tunread")  # chosen on General while Settings is open
        assert unread.isChecked() and not unread.isEnabled() and not page.favorites_box.isEnabled()
        page.local_box.setChecked(False)
        page.standard_boxes["inboxes"].setChecked(False)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SettingsDialog, "exec", cancelled)
    world.window.act_settings.trigger()
    assert world.call("sidebar.show") == before and prefs.get_bool(settings_of(world), "show_local_folders")  # Cancel: nothing changed


def test_settings_ok_takes_into_use_only_what_changed(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """R-13: OK set every colour, size, list look and key again, also when nothing changed (a flash, a moment of work)."""
    window = world.window
    window.start()
    calls: list[str] = []
    real_apply = appearance.apply
    monkeypatch.setattr(appearance, "apply", lambda *args, **kwargs: (calls.append("colours"), real_apply(*args, **kwargs))[1])
    for owner, method, name in (
        (window, "_apply_shortcuts", "_apply_shortcuts"), (window.columns, "apply_list_look", "_apply_list_look"),
        (window.columns, "apply_list_layout", "_apply_list_layout"), (window.folders, "lay_out", "_lay_out_tree"),
    ):
        real = getattr(owner, method)
        monkeypatch.setattr(owner, method, lambda *args, real=real, name=name, **kwargs: (calls.append(name), real(*args, **kwargs))[1])
    window.apply_preferences()  # OK without a change
    assert calls == []
    window.settings.setValue("color_scheme", "dark")
    window.apply_preferences()
    assert "colours" in calls and "_apply_shortcuts" not in calls and "_apply_list_layout" not in calls
    calls.clear()
    window.settings.setValue("shortcuts", '{"refresh": "F9"}')
    window.apply_preferences()
    assert calls == ["_apply_shortcuts"]


def test_a_dragged_reading_pane_width_stays_after_settings_ok(world: World, qtbot: QtBot) -> None:
    """R-13: OK put the reading pane back at its standard width, also when only another setting changed."""
    window = world.window
    window.start()
    window.show()
    qtbot.waitExposed(window)
    window.content_splitter.setSizes([300, 700])
    dragged = window.content_splitter.sizes()
    window.settings.setValue("mark_read", "never")
    window.apply_preferences()
    assert window.content_splitter.sizes() == dragged
