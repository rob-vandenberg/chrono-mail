from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFormLayout, QLabel, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.ui.colours import red
from chrono_mail.ui.composer import ComposeWindow, parse_recipients, signature_block, with_signature
from tests.ui.world import World, add_window, gone, row_of, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_composer.py 2.2.314"

# --- Version history ----------------------------------------------------
# v2.2.314: a failed send says it is sent again with the next Send and Receive (D-028)
# v1.9.267: a failed automatic save in red on the bottom line without a box; a failed Save with the box
# v1.1.208: A message window deletes itself when it closes: gone and add_window
# v1.1.180: the account to send from is the From row, above To
# v0.0.86: Forward as attachment
# v0.0.69: Accounts are told apart by user name, incoming server and protocol
# v0.0.20: Settings window, account editing, signatures
# v0.0.16: Drafts
# v0.0.13: Initial composer tests


def accounts(world: World) -> list[object]:
    return list(world.call("account.list"))


def new_composer(world: World, qtbot: QtBot, draft: object | None = None, source: tuple[str, str] | None = None) -> ComposeWindow:
    window = ComposeWindow(world.backend, accounts(world), world.account_id, draft, source)
    add_window(qtbot, window)  # it deletes itself when it closes
    window.show()
    return window


def fill(window: ComposeWindow, to: str = "you@example.com", subject: str = "Hello", body: str = "Hi there") -> None:
    window.to.setText(to)
    window.subject.setText(subject)
    window.body.setPlainText(body)


def sent_signals(window: ComposeWindow) -> list[str]:
    accounts_seen: list[str] = []

    def record(account: str) -> None:
        accounts_seen.append(account)

    window.sent.connect(record)
    return accounts_seen


def box_titles(world: World, kind: str) -> list[str]:
    return [title for shown_kind, title, _ in world.dialogs.shown if shown_kind == kind]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", []), ("a@x.org", ["a@x.org"]), ("a@x.org, b@y.org", ["a@x.org", "b@y.org"]), ("a@x.org; b@y.org", ["a@x.org", "b@y.org"]), ("a@x.org\nb@y.org", ["a@x.org", "b@y.org"]),
     ("Alice <a@x.org>, Bob <b@y.org>", ["Alice <a@x.org>", "Bob <b@y.org>"]), ('"Smith, Bob" <b@y.org>', ['"Smith, Bob" <b@y.org>']), ("  a@x.org  ,, ", ["a@x.org"]),
     ("nobody", None), ("a@b@c", None), ("a@", None), ("@x.org", None), ("a b@x.org", None), ("a@x.org, junk", None)],
)
def test_parse_recipients(text: str, expected: list[str] | None) -> None:
    assert parse_recipients(text) == expected


def test_a_new_message_is_empty_and_the_sender_can_be_chosen(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    assert window.to.text() == "" and window.subject.text() == "" and window.body.toPlainText() == ""
    assert window.from_box.count() == 1 and window.from_box.currentData() == world.account_id and not window.is_modified()
    assert window.include_original.isHidden()
    rows = [(window.fields.itemAt(row, QFormLayout.ItemRole.LabelRole), window.fields.itemAt(row, QFormLayout.ItemRole.FieldRole)) for row in range(2)]
    labels = [label.widget() for label, _ in rows]
    assert all(isinstance(label, QLabel) for label in labels)
    assert [label.text() for label in labels if isinstance(label, QLabel)] == ["From", "To"]
    assert [field.widget() for _, field in rows] == [window.from_box, window.to]  # From is a row of its own, above To


def test_sending_a_message(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    signals = sent_signals(window)
    fill(window, to="You <you@example.com>, other@example.org")
    window.cc.setText("copy@example.com")
    window.bcc.setText("secret@example.com")
    window.send_button.click()
    wait(qtbot, lambda: bool(signals))
    (received,) = world.smtp.received
    assert received.recipients == ("you@example.com", "other@example.org", "copy@example.com", "secret@example.com")
    assert b"secret@example.com" not in received.data and b"Subject: Hello" in received.data
    assert signals == [world.account_id]
    wait(qtbot, lambda: gone(window))
    assert world.call("message.list", account=world.account_id, folder="Sent").total == 1


def test_a_message_with_only_a_blind_copy(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    fill(window, to="")
    window.bcc.setText("hidden@example.com")
    window.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    assert world.smtp.received[0].recipients == ("hidden@example.com",) and b"hidden@example.com" not in world.smtp.received[0].data


def test_nothing_is_sent_without_a_valid_recipient(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    fill(window, to="")
    window.send_button.click()
    assert "Add at least one recipient." in [text for _, _, text in world.dialogs.shown]
    for field_name, text in (("to", "not an address"), ("cc", "a@b@c"), ("bcc", "x@")):
        fill(window, to="ok@example.com")
        getattr(window, field_name).setText(text)
        window.send_button.click()
    assert sum("not an e-mail address" in text for _, _, text in world.dialogs.shown) == 3
    qtbot.wait(200)
    assert world.smtp.received == [] and window.isVisible()


def test_an_empty_subject_asks_first(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    fill(window, subject="")
    world.dialogs.answer = QMessageBox.StandardButton.No
    window.send_button.click()
    qtbot.wait(200)
    assert world.smtp.received == [] and "Send" in box_titles(world, "question")
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    window.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)


def test_attachments_are_listed_removed_and_sent(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    keep, drop = tmp_path / "keep.txt", tmp_path / "drop.txt"
    keep.write_text("keep this")
    drop.write_text("drop this")
    window = new_composer(world, qtbot)
    fill(window)
    window.add_attachment(str(keep))
    window.add_attachment(str(drop))
    assert window.attached_paths() == [str(keep), str(drop)] and "keep.txt" in window.attachments.item(0).text()
    window.attachments.setCurrentRow(1)
    window.attachments.item(1).setSelected(True)
    window.remove_button.click()
    assert window.attached_paths() == [str(keep)]
    window.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    assert b"keep.txt" in world.smtp.received[0].data and b"drop.txt" not in world.smtp.received[0].data


def test_a_failed_send_keeps_the_message_in_the_outbox_and_the_window_open(world: World, qtbot: QtBot) -> None:
    world.engine.passwords.set(world.account_id, ServerRole.OUTGOING, "wrong")
    window = new_composer(world, qtbot)
    signals = sent_signals(window)
    fill(window)
    window.send_button.click()
    wait(qtbot, lambda: "The message was not sent" in box_titles(world, "warning"))
    text = next(text for _, title, text in world.dialogs.shown if title == "The message was not sent")
    assert "Outbox" in text and "sent again with the next Send and Receive" in text  # D-028: tried again at every refresh (2.2.314)
    assert window.isVisible() and window.send_button.isEnabled() and window.to.isEnabled()  # the person can correct and retry
    assert world.call("message.list", account=world.account_id, folder="Outbox").total == 1 and world.smtp.received == []
    assert signals == [world.account_id]  # the Outbox changed
    world.engine.passwords.set(world.account_id, ServerRole.OUTGOING, "secret")
    window.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)


def test_an_engine_error_before_anything_is_stored_says_so(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    fill(window, subject="x\r\nBcc: evil@example.com")
    window.send_button.click()
    wait(qtbot, lambda: "The message was not sent" in box_titles(world, "warning"))
    assert "Nothing was stored or sent" in next(text for _, title, text in world.dialogs.shown if title == "The message was not sent")
    assert world.call("message.list", account=world.account_id, folder="Outbox").total == 0 and window.send_button.isEnabled()


def test_closing_a_changed_message_asks_and_an_untouched_one_does_not(world: World, qtbot: QtBot) -> None:
    untouched = new_composer(world, qtbot)
    assert untouched.close() and not world.dialogs.shown
    window = new_composer(world, qtbot)
    fill(window)
    world.dialogs.answer = QMessageBox.StandardButton.Cancel
    assert not window.close() and window.isVisible()
    world.dialogs.answer = QMessageBox.StandardButton.Discard
    assert window.close() and gone(window)
    assert "Save this message?" in box_titles(world, "question")
    assert world.call("message.list", account=world.account_id, folder="Drafts").total == 0


def test_saving_on_close_keeps_the_message_as_a_draft(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    fill(window, subject="Unfinished", body="to be continued")
    world.dialogs.answer = QMessageBox.StandardButton.Save
    window.close()
    wait(qtbot, lambda: gone(window))
    listing = world.call("message.list", account=world.account_id, folder="Drafts")
    assert listing.total == 1 and listing.messages[0].subject == "Unfinished"


def test_saving_twice_replaces_the_draft_and_discard_removes_an_autosaved_one(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    fill(window, subject="One")
    window.save_button.click()
    wait(qtbot, lambda: window.stored_as() is not None and not window.is_modified())
    window.subject.setText("Two")
    window.save_button.click()
    wait(qtbot, lambda: not window.is_modified() and window.status.text().startswith("Draft saved"))
    listing = world.call("message.list", account=world.account_id, folder="Drafts")
    assert listing.total == 1 and listing.messages[0].subject == "Two"
    window.subject.setText("Three")
    world.dialogs.answer = QMessageBox.StandardButton.Discard
    window.close()
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Drafts").total == 0)


def test_autosave_stores_a_changed_message_and_skips_an_empty_one(world: World, qtbot: QtBot) -> None:
    window = new_composer(world, qtbot)
    window._autosave()  # noqa: SLF001
    qtbot.wait(100)
    assert world.call("message.list", account=world.account_id, folder="Drafts").total == 0
    fill(window, subject="Auto")
    window._autosave()  # noqa: SLF001
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Drafts").total == 1)
    window._finished = True  # noqa: SLF001
    window.close()


def test_a_draft_with_an_attachment_is_resumed_and_sent_with_it(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    attachment = tmp_path / "plan.txt"
    attachment.write_text("the plan")
    window = new_composer(world, qtbot)
    fill(window, to="you@example.com", subject="With file")
    window.add_attachment(str(attachment))
    window.save_button.click()
    wait(qtbot, lambda: not window.is_modified() and window.stored_as() is not None)
    assert window.attached_paths() == [] and window.kept_positions() == ["0"]
    window._finished = True  # noqa: SLF001
    window.close()
    drafts = world.call("message.list", account=world.account_id, folder="Drafts")
    opened = world.call("draft.open", account=world.account_id, message=drafts.messages[0].message_id)
    resumed = new_composer(world, qtbot, opened)
    resumed._stored = (opened.folder, opened.message_id)  # noqa: SLF001
    assert resumed.kept_positions() == ["0"] and resumed.subject.text() == "With file" and not resumed.is_modified()
    resumed.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    assert b"plan.txt" in world.smtp.received[0].data
    wait(qtbot, lambda: world.call("message.list", account=world.account_id, folder="Drafts").total == 0)


# --- replying and forwarding ----------------------------------------------------------------------------------------------------------


def test_reply_marks_the_original_as_replied_and_threads_the_message(world: World, qtbot: QtBot) -> None:
    world.window.start()
    settle(qtbot, world, 3)
    row = row_of(world.window, 0)
    draft = world.call("message.compose", account=world.account_id, folder="Inbox", message=row.message_id, kind="reply")
    window = new_composer(world, qtbot, draft, ("Inbox", row.message_id))
    assert window.to.text() == "Alice <alice@example.com>" and window.subject.text() == f"Re: {row.subject}" and window.windowTitle() == window.subject.text()
    assert window.body.toPlainText().startswith("\n\nOn ") and window.body.textCursor().position() == 0
    window.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    data = world.smtp.received[0].data
    assert b"In-Reply-To: " + draft.in_reply_to.encode() in data and b"References: " in data
    wait(qtbot, lambda: "replied" in world.call("message.show", account=world.account_id, folder="Inbox", message=row.message_id).flags)


def test_forward_can_leave_out_the_original_attachments(tmp_path: Path, qtbot: QtBot, dialogs: object) -> None:
    import base64

    raw = (b"From: A <a@x.org>\r\nTo: me@example.com\r\nSubject: Photos\r\nMessage-ID: <p@x>\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=B\r\n\r\n"
           b"--B\r\nContent-Type: text/plain\r\n\r\nlook\r\n--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"a.pdf\"\r\n"
           b"Content-Transfer-Encoding: base64\r\n\r\n" + base64.encodebytes(b"%PDF-x") + b"--B--\r\n")
    from tests.ui.world import Dialogs, build_world

    assert isinstance(dialogs, Dialogs)
    for world in build_world(tmp_path, qtbot, dialogs, messages={"p": raw}):
        message = world.call("message.list", account=world.account_id).messages[0]
        for include in (True, False):
            draft = world.call("message.compose", account=world.account_id, folder="Inbox", message=message.message_id, kind="forward")
            window = new_composer(world, qtbot, draft, ("Inbox", message.message_id))
            assert not window.include_original.isHidden() and window.include_original.isChecked() and "a.pdf" in window.include_original.text()
            window.include_original.setChecked(include)
            window.to.setText("friend@example.org")
            window.send_button.click()
            wait(qtbot, lambda: len(world.smtp.received) == (1 if include else 2))
        assert world.smtp.received[0].data.count(b"Content-Disposition: attachment") == 1
        assert b"Content-Disposition: attachment" not in world.smtp.received[1].data


def test_forward_as_attachment_attaches_the_original(world: World, qtbot: QtBot) -> None:
    message = world.call("message.list", account=world.account_id).messages[0]
    draft = world.call("message.compose", account=world.account_id, folder="Inbox", message=message.message_id, kind="forward_attachment")
    window = new_composer(world, qtbot, draft, ("Inbox", message.message_id))
    assert not window.include_original.isHidden() and window.include_original.isChecked() and window.include_original.text() == f"Attach the original message ({message.subject}.eml)"
    window.to.setText("friend@example.org")
    window.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    assert b"Content-Type: message/rfc822" in world.smtp.received[0].data


def test_two_accounts_can_be_chosen_as_the_sender(world: World, qtbot: QtBot) -> None:
    world.call("account.add", display_name="Work", email_address="work@example.com", username="user", password="secret", incoming_host="localhost", incoming_port=world.pop.port, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none")
    window = new_composer(world, qtbot)
    assert window.from_box.count() == 2
    window.from_box.setCurrentIndex(1 - window.from_box.currentIndex())
    fill(window)
    window.send_button.click()
    wait(qtbot, lambda: len(world.smtp.received) == 1)
    assert world.smtp.received[0].sender in ("me@example.com", "work@example.com") and world.smtp.received[0].sender != "me@example.com"



@pytest.mark.parametrize("text", ["a@x.org,,", "a@x.org, ,;; ", ",a@x.org", "a@x.org;\n;b@y.org"])
def test_stray_separators_never_lose_an_address(text: str) -> None:
    assert parse_recipients(text) is not None and "a@x.org" in (parse_recipients(text) or [])


@pytest.mark.parametrize("hostile", ['"', "<", ">", '"<', "a@x.org <", "<<a@x.org>>", "a@x.org\x00", "\n\n", ",,,", ";;", "é@ü.org", "a@x.org " * 500, "<" * 1000, '"' * 1001, "\r\n" * 50, "a@x.org\r\nBcc: evil@x.org"])
def test_hostile_recipient_text_never_raises(hostile: str) -> None:
    result = parse_recipients(hostile)
    assert result is None or all("\r" not in address and "\n" not in address for address in result)


# --- signatures (0.0.20) -----------------------------------------------------------------------------------------------------------------


def test_with_signature_puts_it_before_quoted_text() -> None:
    assert with_signature("", "") == "" and with_signature("text", "  ") == "text"
    assert with_signature("", "Rob\nChrono Mail") == "\n\n-- \nRob\nChrono Mail\n"
    assert with_signature("\n\nOn Monday, Ann wrote:\n> hi\n", "Rob") == "\n\n-- \nRob\n\nOn Monday, Ann wrote:\n> hi\n"
    assert with_signature("no leading blank lines", "Rob") == "\n\n-- \nRob\n\nno leading blank lines"
    assert signature_block("\nRob\n\n") == "-- \nRob"


def test_a_new_message_and_a_reply_get_the_signature_but_a_resumed_draft_does_not(world: World, qtbot: QtBot) -> None:
    world.call("account.edit", account=world.account_id, signature="Rob\nChrono Mail")
    fresh = new_composer(world, qtbot)
    assert fresh.body.toPlainText() == "\n\n-- \nRob\nChrono Mail\n" and not fresh.is_modified()
    assert fresh.close() and not world.dialogs.shown  # a signature alone is not a change worth asking about
    drafted = world.call("message.compose", account=world.account_id, folder="Inbox", message=world.call("message.list", account=world.account_id, folder="Inbox").messages[0].message_id, kind="reply")
    reply = new_composer(world, qtbot, drafted)
    text = reply.body.toPlainText()
    assert text.index("-- \nRob") < text.index("wrote:") and text.count("-- \nRob") == 1
    saved = world.call("draft.save", account=world.account_id, subject="x", body="mine\n\n-- \nRob\nChrono Mail\n")
    opened = world.call("draft.open", account=world.account_id, message=saved.message_id)
    resumed = ComposeWindow(world.backend, accounts(world), world.account_id, opened, None, (opened.folder, opened.message_id))
    qtbot.addWidget(resumed)
    assert resumed.body.toPlainText().count("-- \nRob") == 1
    for window in (reply, resumed):
        window._finished = True  # noqa: SLF001
        window.close()


def test_choosing_another_sender_swaps_the_signature(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    from tests.test_engine_commands import add_account

    second = add_account(world.engine, world.pop, world.smtp, email="second@example.com")
    world.call("account.edit", account=world.account_id, signature="First signature")
    world.call("account.edit", account=second, signature="Second signature")
    window = new_composer(world, qtbot)
    assert "First signature" in window.body.toPlainText()
    window.body.setPlainText("Hello\n\n" + window.body.toPlainText().lstrip("\n"))
    window.from_box.setCurrentIndex(window.from_box.findData(second))
    text = window.body.toPlainText()
    assert "Second signature" in text and "First signature" not in text and text.startswith("Hello")
    world.call("account.edit", account=second, signature="")
    window.from_box.setCurrentIndex(window.from_box.findData(world.account_id))
    assert window.body.toPlainText().count("-- \n") == 1  # the first account's signature is back once
    window._finished = True  # noqa: SLF001
    window.close()


def failing_draft(world: World, qtbot: QtBot, tmp_path: Path) -> ComposeWindow:
    """A message window whose next save fails: its attachment was removed from the disk after it was added."""
    attachment = tmp_path / "gone.txt"
    attachment.write_text("soon gone")
    window = new_composer(world, qtbot)
    fill(window, to="you@example.com", subject="Will not save")
    window.add_attachment(str(attachment))
    attachment.unlink()
    return window


def test_a_failed_automatic_save_is_told_in_red_on_the_bottom_line_without_a_box(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    """The owner (1.9.267): an automatic save never interrupts the writing; it tries again by itself at the next one."""
    window = failing_draft(world, qtbot, tmp_path)
    window._autosave()  # noqa: SLF001
    wait(qtbot, lambda: window.status.text().startswith("The draft could not be saved: "))
    assert red() in window.status.styleSheet() and world.dialogs.shown == []
    window._finished = True  # noqa: SLF001
    window.close()


def test_a_failed_save_asked_for_shows_the_box_too_and_the_red_goes_with_the_next_text(world: World, qtbot: QtBot, tmp_path: Path) -> None:
    window = failing_draft(world, qtbot, tmp_path)
    window.save_button.click()
    wait(qtbot, lambda: world.dialogs.shown != [])
    assert [(kind, title) for kind, title, _ in world.dialogs.shown] == [("warning", "Save draft")]
    assert window.status.text().startswith("The draft could not be saved: ") and red() in window.status.styleSheet()
    window.attachments.clear()  # without the missing file the next save works
    window.save_button.click()
    wait(qtbot, lambda: window.status.text().startswith("Draft saved at "))
    assert window.status.styleSheet() == ""
    window._finished = True  # noqa: SLF001
    window.close()
