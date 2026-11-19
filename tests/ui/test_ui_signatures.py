from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

import chrono_mail.engine.commands_signatures as commands
from chrono_mail.core import ConfigurationError
from chrono_mail.ui.composer import ComposeWindow
from chrono_mail.ui.composertexts import SENT_FROM
from chrono_mail.ui.settingsdialog import SettingsDialog
from chrono_mail.ui.signaturesdialog import SignatureEditor, SignaturesPanel
from tests.ui.test_ui_settings import open_settings
from tests.ui.world import Dialogs, World, add_window, answer_open_file, answer_save_file, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_signatures.py 4.6.639"

# --- Version history ----------------------------------------------------
# v4.6.639: the signature is put in with the Signature button's list (pick_signature); Ctrl+1 to Ctrl+7 now add a text at the cursor (D-363)
# v4.5.628: a swap replaces only the signature; a text that cannot be read is left out and named (D-358)
# v4.5.627: the account block as the owner drew it: the fields' heading, the first field level with the account list, the tick under it
# v4.5.626: Initial tests: Settings > Templates and Signatures (the library and the accounts' choices, kept at OK), the window of one
#           text, the New message window's templates, signatures and keys, New's menu (B-24, D-358)


def keep(world: World, *items: tuple[str, str]) -> list[str]:
    """Keep exactly these (name, html) in the library; the answer: their ids."""
    found = world.call("snippet.save-all", ids=tuple("" for _ in items), names=tuple(name for name, _ in items), htmls=tuple(text for _, text in items))
    return [entry.item_id for entry in found]


def add(world: World, name: str, html: str) -> str:
    """One more text at the end of the library, the others kept as they are; its id."""
    current = list(world.call("snippet.all"))
    found = world.call(
        "snippet.save-all", ids=(*(entry.item_id for entry in current), ""), names=(*(entry.name for entry in current), name),
        htmls=(*(entry.html for entry in current), html),
    )
    return str(found[-1].item_id)


def give_signature(world: World, account: str, text: str, below: bool = False) -> str:
    """A new text of the library made from plain lines, as this account's signature for new mail, replies and forwards; its id."""
    item = add(world, f"Signature {len(world.call('snippet.all')) + 1}", "<p>" + "<br>".join(text.split("\n")) + "</p>")
    world.call("snippet.set-account-texts", account=account, signature_new=item, signature_reply=item, signature_forward=item, signature_below=below)
    return item


def kept(world: World) -> list[str]:
    return [entry.name for entry in world.call("snippet.all")]


def shown(panel: SignaturesPanel) -> list[str]:
    lines: list[str] = []
    for row in range(panel.items.topLevelItemCount()):
        item = panel.items.topLevelItem(row)
        if item is not None:
            lines.append(f"{item.text(0)} {item.text(1)}".strip())
    return lines


def open_page(world: World, qtbot: QtBot) -> tuple[SettingsDialog, SignaturesPanel]:
    dialog = open_settings(world, qtbot)
    dialog.show_page("Templates and Signatures")
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001
    return dialog, dialog.signatures_panel


def answer_editor(monkeypatch: pytest.MonkeyPatch, name: str, html: str = "") -> list[tuple[str, str]]:
    """Every window of one text is filled with this name (and text) and saved; the list holds each window's title and the name it opened with."""
    opened: list[tuple[str, str]] = []

    def exec_saved(self: SignatureEditor) -> int:
        opened.append((self.windowTitle(), self.name.text()))
        self.name.setText(name)
        if html:
            self.text.set_html(html)
        self.save_button.click()
        return self.result()

    monkeypatch.setattr(SignatureEditor, "exec", exec_saved)
    return opened


def ok(dialog: SettingsDialog, qtbot: QtBot) -> None:
    dialog.accept()
    wait(qtbot, lambda: dialog.result() == QDialog.DialogCode.Accepted)


def composer(world: World, qtbot: QtBot, draft: object | None = None, template: str = "") -> ComposeWindow:
    """A New message window; a saved draft is taken up again, as the program opens one."""
    folder, message = getattr(draft, "folder", None), getattr(draft, "message_id", None)  # getattr: only a saved draft has them
    resume = (str(folder), str(message)) if folder is not None and message is not None else None
    window = ComposeWindow(world.backend, list(world.call("account.list")), world.account_id, draft, None, resume, template=template)  # type: ignore[arg-type]
    add_window(qtbot, window)
    window.show()
    wait(qtbot, lambda: world.backend._pending == {})  # noqa: SLF001 - the library arrived
    return window


def pick_signature(window: ComposeWindow, place: int) -> None:
    """A text chosen in the Signature button's list: put in as the signature, in place of the one there (D-363)."""
    menu = window.signature_button.menu()
    assert menu is not None
    menu.actions()[place].trigger()


def close(window: ComposeWindow) -> None:
    window._finished = True  # noqa: SLF001
    window.close()


# --- the page -----------------------------------------------------------------------------------------------


def test_the_page_stands_after_writing_with_the_library_above_the_accounts(world: World, qtbot: QtBot) -> None:
    keep(world, *[(f"S{number}", f"<p>{number}</p>") for number in range(1, 9)])
    dialog, panel = open_page(world, qtbot)
    titles = [dialog.sidebar.item(row).text() for row in range(dialog.sidebar.count())]
    assert titles[titles.index("Writing") + 1] == "Templates and Signatures" and dialog.pages.currentWidget() is panel
    assert dialog.sidebar.width() >= dialog.sidebar.sizeHintForColumn(0) + 2 * dialog.sidebar.frameWidth()  # no title cut off
    assert shown(panel) == [*[f"S{number} Ctrl+{number}" for number in range(1, 8)], "S8"]
    dialog.resize(1600, 900)  # a usual screen: room beyond the smallest sizes of both sides
    wait(qtbot, lambda: panel.splitter.width() > 1000)
    assert panel.splitter.count() == 2 and panel.splitter.sizes()[1] > panel.splitter.sizes()[0]  # the preview gets most of the width
    assert panel.accounts.count() == 1 and panel.accounts.currentRow() == 0
    assert panel.preview.toPlainText().strip() == "1"
    assert panel.accounts_splitter.count() == 2  # the account list and its fields can be made wider or narrower
    from PySide6.QtWidgets import QLabel

    heading = panel.findChild(QLabel, "choicesLabel")
    assert heading is not None and heading.text() == "Templates and signatures assigned to the account:"
    top = panel.accounts.mapTo(panel, panel.accounts.rect().topLeft()).y()
    assert abs(panel.boxes[0].mapTo(panel, panel.boxes[0].rect().topLeft()).y() - top) <= 4  # the first field level with the account list
    assert panel.sent_from.mapTo(panel, panel.sent_from.rect().topLeft()).y() > top + panel.accounts.height() - 1  # the tick under the list
    assert panel.sent_from.mapTo(panel, panel.sent_from.rect().topLeft()).x() == panel.accounts.mapTo(panel, panel.accounts.rect().topLeft()).x()
    assert panel.items.mapTo(panel, panel.items.rect().topLeft()).x() == panel.accounts.mapTo(panel, panel.accounts.rect().topLeft()).x()  # lined up on the left
    left, right = panel.splitter.sizes()
    panel.splitter.moveSplitter(left + 100, 1)
    assert panel.splitter.sizes()[0] == left + 100  # the splitter moves: the buttons are no longer in its two sides


def test_a_button_that_cannot_act_is_greyed_and_the_arrows_set_the_keys(world: World, qtbot: QtBot) -> None:
    dialog, panel = open_page(world, qtbot)
    assert panel.new_button.isEnabled() and not any(button.isEnabled() for button in (panel.edit_button, panel.delete_button, panel.save_as_button, panel.up_button, panel.down_button, panel.export_button))
    dialog.reject()
    keep(world, ("A", "<p>a</p>"), ("B", "<p>b</p>"))
    dialog, panel = open_page(world, qtbot)
    assert not panel.up_button.isEnabled() and panel.down_button.isEnabled()
    panel.down_button.click()
    assert shown(panel) == ["B Ctrl+1", "A Ctrl+2"]
    ok(dialog, qtbot)
    assert kept(world) == ["B", "A"]


def test_new_and_edit_are_kept_at_ok_and_cancel_keeps_nothing(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = answer_editor(monkeypatch, "Work", "<p><b>Rob</b> at work</p>")
    dialog, panel = open_page(world, qtbot)
    panel.new_button.click()
    assert opened == [("New template or signature", "")] and shown(panel) == ["Work Ctrl+1"]
    dialog.reject()
    assert kept(world) == []
    dialog, panel = open_page(world, qtbot)
    panel.new_button.click()
    ok(dialog, qtbot)
    assert kept(world) == ["Work"] and "font-weight" in world.call("snippet.all")[0].html
    answer_editor(monkeypatch, "Office")
    dialog, panel = open_page(world, qtbot)
    panel.edit_button.click()
    ok(dialog, qtbot)
    assert kept(world) == ["Office"]


def test_a_new_text_can_be_chosen_for_an_account_before_ok(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    answer_editor(monkeypatch, "Work", "<p>w</p>")
    dialog, panel = open_page(world, qtbot)
    panel.new_button.click()
    box = panel.boxes[3]  # Signature for new mail
    assert [box.itemText(row) for row in range(box.count())] == ["Not selected", "Work"]
    box.setCurrentIndex(1)
    box.activated.emit(1)
    panel.position.setCurrentIndex(1)
    panel.position.activated.emit(1)
    ok(dialog, qtbot)
    chosen = world.call("snippet.account-texts", account=world.account_id)
    assert chosen.signature_new == world.call("snippet.all")[0].item_id and chosen.signature_below and chosen.template_new == ""


def test_delete_asks_first_and_takes_the_text_off_the_accounts(world: World, qtbot: QtBot, dialogs: Dialogs) -> None:
    give_signature(world, world.account_id, "Rob")
    add(world, "Other", "<p>o</p>")
    dialog, panel = open_page(world, qtbot)
    dialogs.answer = QMessageBox.StandardButton.No
    panel.delete_button.click()
    assert len(shown(panel)) == 2
    dialogs.answer = QMessageBox.StandardButton.Yes
    panel.delete_button.click()
    assert dialogs.shown[-1][0:2] == ("question", "Delete") and shown(panel) == ["Other Ctrl+1"] and panel.boxes[3].currentText() == "Not selected"
    ok(dialog, qtbot)
    assert kept(world) == ["Other"] and world.call("snippet.account-texts", account=world.account_id).signature_new == ""


def test_open_save_as_export_and_import_go_through_files(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    keep(world, ("Work", "<p>Work text</p>"))
    source = tmp_path / "Office.html"
    source.write_text("<p>From the office</p>", "utf-8")
    answer_open_file(monkeypatch, source)
    opened = answer_editor(monkeypatch, "Office")
    dialog, panel = open_page(world, qtbot)
    panel.open_button.click()
    wait(qtbot, lambda: len(shown(panel)) == 2)
    assert opened == [("Open a template or signature", "Office")] and "From the office" in panel.preview.toPlainText()
    page = tmp_path / "Office copy"
    answer_save_file(monkeypatch, page)
    panel.save_as_button.click()
    wait(qtbot, lambda: page.with_suffix(".html").exists())
    everything = tmp_path / "all"
    answer_save_file(monkeypatch, everything)
    panel.export_button.click()
    wait(qtbot, lambda: everything.with_suffix(".json").exists())
    answer_open_file(monkeypatch, everything.with_suffix(".json"))
    panel.import_button.click()
    wait(qtbot, lambda: len(shown(panel)) == 4)
    ok(dialog, qtbot)
    assert kept(world) == ["Work", "Office", "Work (2)", "Office (2)"]


def test_a_refusal_at_ok_keeps_settings_open_on_the_page_and_says_why(world: World, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    answer_editor(monkeypatch, "Work")
    dialog, panel = open_page(world, qtbot)
    panel.new_button.click()
    dialog.show_page("General")

    def full(directory: Path, items: object) -> None:
        raise ConfigurationError("The templates and signatures could not be saved: No space left on device")

    monkeypatch.setattr(commands, "save_items", full)
    dialog.accept()
    wait(qtbot, lambda: panel.status.isVisibleTo(panel))
    assert "No space left on device" in panel.status.text() and dialog.isVisible() and dialog.pages.currentWidget() is panel
    assert kept(world) == []


def test_a_name_is_needed_and_one_used_is_said(world: World, qtbot: QtBot) -> None:
    editor = SignatureEditor(world.window.settings, None, lambda name: name.casefold() == "work")
    qtbot.addWidget(editor)
    editor.show()
    assert not editor.save_button.isEnabled()
    editor.name.setText("WORK")
    editor.save_button.click()
    assert "called WORK already exists" in editor.status.text() and editor.isVisible() and editor.result_text is None


def test_the_start_moves_an_old_signature_into_the_library_as_the_account_s(world: World, qtbot: QtBot) -> None:
    from dataclasses import replace

    world.engine.change_config(lambda config: config.with_account(replace(config.find(world.account_id), signature="Rob\nChrono Mail")))
    world.window.settings.setValue("reply_quote", "below")
    world.window._take_old_signatures()  # noqa: SLF001 - what the start does once the accounts are read
    wait(qtbot, lambda: kept(world) != [])
    entry = world.call("snippet.all")[0]
    chosen = world.call("snippet.account-texts", account=world.account_id)
    assert entry.html == "<p>Rob<br>Chrono Mail</p>" and (chosen.signature_new, chosen.signature_reply, chosen.signature_below) == (entry.item_id, entry.item_id, True)
    assert world.engine.config().find(world.account_id).signature == ""


# --- the New message window ---------------------------------------------------------------------------------


def test_a_new_message_gets_the_account_s_template_and_signature_unchanged(world: World, qtbot: QtBot) -> None:
    signature = give_signature(world, world.account_id, "Rob\nChrono Mail")
    template = add(world, "Offer", "<p>Dear customer,</p>")
    world.call("snippet.set-account-texts", account=world.account_id, template_new=template, signature_new=signature)
    window = composer(world, qtbot)
    text = window.body.toPlainText()
    assert text.startswith("Dear customer,") and text.rstrip().endswith("-- \nRob\nChrono Mail") and not window.is_modified()
    close(window)


def test_the_signature_button_puts_in_one_signature_the_same_does_nothing_another_replaces(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards</p>"), ("Short", "<p>Cheers</p>"))
    window = composer(world, qtbot)
    pick_signature(window, 0)
    pick_signature(window, 0)
    assert window.body.toPlainText().count("-- \n") == 1 and "Kind regards" in window.body.toPlainText()
    pick_signature(window, 1)
    text = window.body.toPlainText()
    assert "Cheers" in text and "Kind regards" not in text and text.count("-- \n") == 1
    assert [action.text().split("\t")[0] for action in window.signature_button.menu().actions()] == ["Formal", "Short"]  # type: ignore[union-attr]
    close(window)


def test_in_rich_text_the_signature_button_replaces_the_signature_and_keeps_the_formatting(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p><b>Kind regards</b></p>"), ("Short", "<p>Cheers</p>"))
    window = composer(world, qtbot)
    window.rich_action.trigger()
    pick_signature(window, 0)
    assert "Kind regards" in window.rich.toPlainText() and window.rich.has_formatting()
    pick_signature(window, 1)
    assert "Cheers" in window.rich.toPlainText() and "Kind regards" not in window.rich.toPlainText()
    close(window)


def test_from_swaps_a_default_signature_but_keeps_one_put_in_by_the_signature_button(world: World, qtbot: QtBot) -> None:
    from tests.test_engine_commands import add_account

    second = add_account(world.engine, world.pop, world.smtp, email="second@example.com")
    give_signature(world, world.account_id, "First signature")
    give_signature(world, second, "Second signature")
    window = composer(world, qtbot)
    assert "First signature" in window.body.toPlainText()
    window.from_box.setCurrentIndex(window.from_box.findData(second))
    text = window.body.toPlainText()
    assert "Second signature" in text and "First signature" not in text
    world.call("snippet.set-account-texts", account=second)  # the second account now has none
    window._accounts = list(world.call("account.list"))  # noqa: SLF001
    window.from_box.setCurrentIndex(window.from_box.findData(world.account_id))
    window.from_box.setCurrentIndex(window.from_box.findData(second))
    assert "-- \n" not in window.body.toPlainText()
    pick_signature(window, 0)
    window.from_box.setCurrentIndex(window.from_box.findData(world.account_id))
    assert window.body.toPlainText().count("-- \n") == 1
    close(window)


def test_new_s_menu_starts_a_message_from_a_text(world: World, qtbot: QtBot) -> None:
    keep(world, ("Offer", "<p>Dear customer,</p>"))
    world.window.composing.load_library()
    wait(qtbot, lambda: len(world.window.composing.library) == 1)
    world.window.composing.template_menu.aboutToShow.emit()
    (action,) = world.window.composing.template_menu.actions()
    assert action.text() == "Offer"
    action.trigger()
    wait(qtbot, lambda: len(world.window.composing.composers) == 1)
    window = world.window.composing.composers[0]
    wait(qtbot, lambda: window.body.toPlainText().startswith("Dear customer,") or window.rich.toPlainText().startswith("Dear customer,"))
    close(window)


def test_sent_from_chrono_mail_ends_every_message_sent_when_ticked(world: World, qtbot: QtBot) -> None:
    from chrono_mail.ui.composer import ComposeOptions

    window = ComposeWindow(world.backend, list(world.call("account.list")), world.account_id, options=ComposeOptions(sent_from_line=True))  # type: ignore[arg-type]
    add_window(qtbot, window)
    window.show()
    window.to.setText("you@example.com")
    window.subject.setText("Hello")
    window.body.setPlainText("Hi there")
    window.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    assert SENT_FROM in world.smtp.received[0].data.decode("utf-8", "replace")


def test_a_signature_the_person_changed_is_still_replaced_not_doubled(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards<br>Phone 1</p>"), ("Short", "<p>Cheers</p>"))
    window = composer(world, qtbot)
    pick_signature(window, 0)
    window.body.setPlainText(window.body.toPlainText().replace("Phone 1", "Phone 2"))  # the person fixes a number
    pick_signature(window, 1)
    text = window.body.toPlainText()
    assert text.count("-- \n") == 1 and "Cheers" in text and "Phone" not in text
    close(window)


def test_a_draft_taken_up_again_keeps_one_signature(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards</p>"), ("Short", "<p>Cheers</p>"))
    saved = world.call("draft.save", account=world.account_id, subject="x", body="mine\n\n-- \nOld signature\n")
    opened = world.call("draft.open", account=world.account_id, message=saved.message_id)
    window = composer(world, qtbot, opened)
    pick_signature(window, 1)
    text = window.body.toPlainText()
    assert text.count("-- \n") == 1 and "Cheers" in text and "Old signature" not in text and text.startswith("mine")
    close(window)


def test_a_rich_draft_taken_up_again_keeps_one_signature(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards</p>"), ("Short", "<p>Cheers</p>"))
    window = composer(world, qtbot)
    window.rich_action.trigger()
    pick_signature(window, 0)
    saved = world.call("draft.save", account=world.account_id, subject="x", body=window.rich.toPlainText(), html=window.rich.outgoing_html())
    close(window)
    opened = world.call("draft.open", account=world.account_id, message=saved.message_id)
    again = composer(world, qtbot, opened)
    assert again.is_rich()
    pick_signature(again, 1)
    assert "Cheers" in again.rich.toPlainText() and "Kind regards" not in again.rich.toPlainText()
    close(again)


def test_a_switch_between_rich_and_plain_text_takes_the_signature_along(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards</p>"), ("Short", "<p>Cheers</p>"))
    window = composer(world, qtbot)
    window.rich_action.trigger()
    pick_signature(window, 0)
    window.plain_action.trigger()
    assert window.body.toPlainText().count("-- \nKind regards") == 1
    pick_signature(window, 1)
    assert window.body.toPlainText().count("-- \n") == 1 and "Cheers" in window.body.toPlainText()
    window.rich_action.trigger()
    pick_signature(window, 0)
    text = window.rich.toPlainText()
    assert "Kind regards" in text and "Cheers" not in text and "-- " not in text
    close(window)


def test_the_signature_button_in_a_new_message_puts_the_signature_after_what_is_written(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards</p>"))
    window = composer(world, qtbot)
    window.body.setPlainText("Hello Ann,\n\nSee you tomorrow.")
    pick_signature(window, 0)
    text = window.body.toPlainText()
    assert text.startswith("Hello Ann,") and text.index("See you tomorrow.") < text.index("-- \nKind regards")
    close(window)


def test_a_signature_with_an_empty_line_is_replaced_whole(world: World, qtbot: QtBot) -> None:
    keep(world, ("Long", "<p>Rob</p><p><br></p><p>Example company</p>"), ("Short", "<p>Cheers</p>"))
    drafted = world.call("message.compose", account=world.account_id, folder="Inbox", message=world.call("message.list", account=world.account_id, folder="Inbox").messages[0].message_id, kind="reply")
    window = composer(world, qtbot, drafted)
    pick_signature(window, 0)
    assert "Example company" in window.body.toPlainText()
    pick_signature(window, 1)
    text = window.body.toPlainText()
    assert "Cheers" in text and "Example company" not in text and "Rob\n" not in text.split("wrote:")[0] and text.count("-- \n") == 1
    close(window)


def test_a_forwarded_mail_s_own_signature_is_never_taken_for_yours(world: World, qtbot: QtBot) -> None:
    keep(world, ("Formal", "<p>Kind regards</p>"), ("Short", "<p>Cheers</p>"))
    drafted = world.call("message.compose", account=world.account_id, folder="Inbox", message=world.call("message.list", account=world.account_id, folder="Inbox").messages[0].message_id, kind="forward")
    window = composer(world, qtbot, drafted)
    window.body.setPlainText("\n\n---------- Forwarded message ----------\nFrom: Ann\n\nSee below.\n-- \nAnn's signature\n")
    pick_signature(window, 0)
    pick_signature(window, 1)
    text = window.body.toPlainText()
    assert "Ann's signature" in text and "Cheers" in text and "Kind regards" not in text
    assert text.index("-- \nCheers") < text.index("Forwarded message")  # yours before the forwarded mail
    window.rich_action.trigger()
    shown = window.rich.toPlainText()
    assert "---------- Forwarded message ----------" in shown and "Ann's signature" in shown and "Cheers" in shown
    close(window)


def test_file_new_stays_one_click_and_the_toolbar_button_has_the_list(world: World, qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QToolButton

    assert world.window.act_new.menu() is None
    buttons = [button for button in world.window.findChildren(QToolButton) if button.defaultAction() is world.window.act_new]
    assert buttons and buttons[0].menu() is world.window.composing.template_menu


def test_a_library_that_cannot_be_read_is_said_in_the_window(world: World, qtbot: QtBot) -> None:
    folder = world.engine.directories.signatures_directory
    folder.mkdir(parents=True)
    (folder / "items.json").write_text("{ damaged", "utf-8")
    window = composer(world, qtbot)
    assert "could not be read" in window.status.text()
    close(window)


def test_a_choice_made_in_settings_is_used_at_once_after_ok(world: World, qtbot: QtBot) -> None:
    item = add(world, "Work", "<p>Kind regards</p>")
    dialog, panel = open_page(world, qtbot)
    box = panel.boxes[3]
    box.setCurrentIndex(box.findData(item))
    box.activated.emit(box.currentIndex())
    ok(dialog, qtbot)
    world.window._settings_closed(dialog, True)  # noqa: SLF001 - what the main window does after Settings OK
    wait(qtbot, lambda: any(account.texts.signature_new == item for account in world.window.mail_state.mail_accounts()))


def test_a_swap_replaces_only_the_signature_never_text_typed_around_it(world: World, qtbot: QtBot) -> None:
    from PySide6.QtGui import QTextCursor

    keep(world, ("Formal", "<p>Kind regards<br>Rob</p>"), ("Short", "<p>Cheers</p>"))
    window = composer(world, qtbot)
    window.body.setPlainText("Hello Ann,")
    pick_signature(window, 0)
    cursor = window.body.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText("\n\nP.S. see you Monday")  # typed below the signature
    text = window.body.toPlainText()
    cursor.setPosition(text.index("Kind") + 4)
    cursor.insertText(" and warm")  # a change inside the signature
    pick_signature(window, 1)
    text = window.body.toPlainText()
    assert text.startswith("Hello Ann,") and "P.S. see you Monday" in text and "Cheers" in text
    assert "Kind" not in text and "warm" not in text and text.count("-- \n") == 1, text
    close(window)


def test_a_text_that_cannot_be_read_is_left_out_and_named(world: World, qtbot: QtBot) -> None:
    from chrono_mail.config.signatures import MAXIMUM_HTML_BYTES

    big, _ = keep(world, ("Huge", "<p>h</p>"), ("Work", "<p>Kind regards</p>"))
    (world.engine.directories.signatures_directory / f"{big}.html").write_bytes(b"x" * (MAXIMUM_HTML_BYTES + 1))
    dialog, panel = open_page(world, qtbot)
    assert panel.status.isVisibleTo(panel) and "Huge" in panel.status.text() and panel.new_button.isEnabled()
    ok(dialog, qtbot)
    assert (world.engine.directories.signatures_directory / f"{big}.html").stat().st_size == MAXIMUM_HTML_BYTES + 1  # its file left alone
    window = composer(world, qtbot)
    assert [action.text().split("\t")[0] for action in window.signature_button.menu().actions()] == ["Work"]  # type: ignore[union-attr]
    close(window)


def test_emoji_above_the_signature_never_shift_what_a_swap_or_a_removal_cuts(world: World, qtbot: QtBot) -> None:
    from tests.test_engine_commands import add_account

    second = add_account(world.engine, world.pop, world.smtp, email="second@example.com")
    give_signature(world, world.account_id, "Kind regards")
    window = composer(world, qtbot)
    window.body.setPlainText("Hi 😀😀 Ann" + window.body.toPlainText())
    window.texts.marks = None  # found again from its "-- " line, as after a switch
    window.texts.plain_signature = "-- \nKind regards"
    window.from_box.setCurrentIndex(window.from_box.findData(second))  # the second account has none: the default goes
    assert window.body.toPlainText() == "Hi 😀😀 Ann\n"
    close(window)
