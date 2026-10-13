import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import QDialog, QLabel
from pytestqt.qtbot import QtBot

from chrono_mail.ui import prefs
from chrono_mail.ui.messagewindow import MessageWindow
from chrono_mail.ui.reader import ReaderWidget
from chrono_mail.ui.settingsdialog import SettingsDialog
from tests.ui.test_ui_composer import fill, new_composer
from tests.ui.test_ui_reader import Probe, load, probe, reader, shown  # noqa: F401 - fixtures
from tests.ui.world import World, row_of, settle, wait

__all__ = ["probe", "reader"]

# --- Version ------------------------------------------------------------
__version__ = "test_ui_preferences.py 0.2.98"

# --- Version history ----------------------------------------------------
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
    dialog = SettingsDialog(world.backend, settings_of(world), True, world.window._shortcut_registry())  # noqa: SLF001
    qtbot.addWidget(dialog)
    assert [dialog.sidebar.item(row).text() for row in range(dialog.sidebar.count())] == ["General", "Appearance", "Message list", "Reading", "Writing", "Accounts", "Rules", "Scheduled messages", "Security and storage", "Keyboard"]
    values = dialog.values()
    assert set(values) >= set(prefs.DEFAULTS)
    dialog.save()
    for key, value in values.items():
        if isinstance(value, bool):
            assert prefs.get_bool(settings_of(world), key) is value, key


def test_marking_as_read_follows_the_setting(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    world.window.start()
    settle(qtbot, world, 3)

    def never(dialog: SettingsDialog) -> None:
        dialog.mark_read_box.setCurrentIndex(dialog.mark_read_box.findData("never"))

    edit_settings(world, qtbot, monkeypatch, never)
    unread = next(position for position in range(3) if "seen" not in row_of(world.window, position).flags)
    world.window.list_view.setCurrentIndex(world.window.list_model.index(unread))
    wait(qtbot, lambda: world.window._shown is not None)  # noqa: SLF001
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
    world.window.list_view.setCurrentIndex(world.window.list_model.index(unread))
    wait(qtbot, lambda: "seen" in row_of(world.window, unread).flags, timeout=3000)


def test_the_look_of_the_list_follows_the_settings(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def change(dialog: SettingsDialog) -> None:
        dialog.density_box.setCurrentIndex(dialog.density_box.findData("roomy"))
        dialog.list_font.setValue(14)
        dialog.layout_box.setCurrentIndex(dialog.layout_box.findData("cards"))
        dialog.pane_box.setCurrentIndex(dialog.pane_box.findData("bottom"))
        dialog.date_box.setCurrentIndex(dialog.date_box.findData("iso"))

    edit_settings(world, qtbot, monkeypatch, change)
    window = world.window
    assert window.column_delegate.row_height == 36 and window.list_view.font().pointSize() == 14
    assert window.list_model.columns == ("card",) and window.content_splitter.orientation().name == "Vertical" and window.list_model.date_style == "iso"


def test_the_columns_are_chosen_on_the_message_list_page(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, settings_of(world), True, None)
    qtbot.addWidget(dialog)
    picker = dialog.columns_picker
    assert not hasattr(dialog, "columns_button") and picker.list.count() > 5
    for row in range(picker.list.count()):
        picker.list.item(row).setCheckState(Qt.CheckState.Unchecked)
    picker.list.item(3).setCheckState(Qt.CheckState.Checked)
    chosen = str(picker.list.item(3).data(Qt.ItemDataRole.UserRole))
    assert dialog.values()["columns"] == chosen
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
    world.window._sender_allowed("New@Example.org")  # noqa: SLF001
    assert prefs.allowed_senders(settings_of(world)) == {"new@example.org"}

    def change(dialog: SettingsDialog) -> None:
        dialog.zoom.setValue(150)
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
    world.window._open_link(QUrl("https://example.org/a"))  # noqa: SLF001
    assert opened == ["https://example.org/a"] and any("Open link" == title for _, title, _ in world.dialogs.shown)  # asked first
    opened.clear()
    world.dialogs.shown.clear()

    def change(dialog: SettingsDialog) -> None:
        dialog.links_ask.setChecked(False)

    edit_settings(world, qtbot, monkeypatch, change)
    world.window._open_link(QUrl("https://example.org/b"))  # noqa: SLF001
    assert opened == ["https://example.org/b"] and world.dialogs.shown == []
    world.window._open_link(QUrl("javascript:alert(1)"))  # noqa: SLF001
    assert opened == ["https://example.org/b"]


def test_writing_settings_reach_the_composer(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def change(dialog: SettingsDialog) -> None:
        dialog.autosave.setValue(0)
        dialog.warn_no_subject.setChecked(False)
        dialog.compose_font.setValue(16)
        dialog.reply_quote_box.setCurrentIndex(dialog.reply_quote_box.findData("below"))

    edit_settings(world, qtbot, monkeypatch, change)
    options = world.window._compose_options()  # noqa: SLF001
    assert (options.autosave_seconds, options.warn_no_subject, options.font_size, options.reply_below) == (0, False, 16, True)
    world.window.start()
    settle(qtbot, world, 3)
    world.window.new_message()
    wait(qtbot, lambda: len(world.window._composers) == 1)  # noqa: SLF001
    composer = world.window._composers[0]  # noqa: SLF001
    assert not composer.autosave_timer.isActive() and composer.body.font().pointSize() == 16
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
    world.window.list_view.setCurrentIndex(world.window.list_model.index(0))
    world.window.act_reply.trigger()
    wait(qtbot, lambda: len(world.window._composers) == 1)  # noqa: SLF001
    text = world.window._composers[0].body.toPlainText()  # noqa: SLF001
    assert text.index("wrote:") < text.index("-- \nRob") and text.rstrip().endswith("Rob")
    composer = world.window._composers[0]  # noqa: SLF001
    composer._finished = True  # noqa: SLF001
    composer.close()

    def none(dialog: SettingsDialog) -> None:
        dialog.reply_quote_box.setCurrentIndex(dialog.reply_quote_box.findData("none"))

    edit_settings(world, qtbot, monkeypatch, none)
    world.window.act_reply.trigger()
    wait(qtbot, lambda: len(world.window._composers) == 2)  # noqa: SLF001
    plain = world.window._composers[1].body.toPlainText()  # noqa: SLF001
    assert "wrote:" not in plain and "> " not in plain
    for composer in world.window._composers:  # noqa: SLF001
        composer._finished = True  # noqa: SLF001
        composer.close()


def test_a_default_account_is_used_for_new_messages(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_engine_commands import add_account

    second = add_account(world.engine, world.pop, world.smtp, email="second@example.com")
    world.window.reload_accounts(select_saved=False)
    wait(qtbot, lambda: len(world.window._accounts) == 2)  # noqa: SLF001
    world.window.start()
    settle(qtbot, world, 3)

    def change(dialog: SettingsDialog) -> None:
        wait(qtbot, lambda: dialog.default_account.count() == 3)
        dialog.default_account.setCurrentIndex(dialog.default_account.findData(second))

    edit_settings(world, qtbot, monkeypatch, change)
    world.window.new_message()
    wait(qtbot, lambda: len(world.window._composers) == 1)  # noqa: SLF001
    assert world.window._composers[0].from_box.currentData() == second  # noqa: SLF001
    world.window._composers[0]._finished = True  # noqa: SLF001
    world.window._composers[0].close()  # noqa: SLF001


def test_the_security_page_shows_the_key_and_can_check_and_rebuild(world: World, qtbot: QtBot) -> None:
    dialog = SettingsDialog(world.backend, settings_of(world), True, None)
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


def test_keys_can_be_changed_checked_for_clashes_and_reset(world: World, qtbot: QtBot) -> None:
    window = world.window
    registry = window._shortcut_registry()  # noqa: SLF001
    assert "reply" in registry and registry["reply"][1] == "Ctrl+R" and "rules" in registry
    dialog = SettingsDialog(world.backend, settings_of(world), True, registry)
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
    dialog = SettingsDialog(world.backend, settings_of(world), True, None, None, [], lambda: [], lambda: None)
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
    window = MessageWindow(world.window.backend, "a", "INBOX", "m", shown(html=html, text=""), lambda *args: None, lambda url: None, world.window._prepare_message_reader)  # noqa: SLF001
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
