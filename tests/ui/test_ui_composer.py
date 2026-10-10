from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from chrono_mail.config import ServerRole
from chrono_mail.ui.composer import ComposeWindow, parse_recipients
from tests.ui.world import World, row_of, settle, wait

# --- Version ------------------------------------------------------------
__version__ = "test_ui_composer.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial composer tests


def accounts(world: World) -> list[object]:
    return list(world.call("account.list"))


def new_composer(world: World, qtbot: QtBot, draft: object | None = None, source: tuple[str, str] | None = None) -> ComposeWindow:
    window = ComposeWindow(world.backend, accounts(world), world.account_id, draft, source)
    qtbot.addWidget(window)
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
    wait(qtbot, lambda: not window.isVisible())
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
    assert "Outbox" in text and "will not be sent again by itself" in text
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
    world.dialogs.answer = QMessageBox.StandardButton.No
    assert not window.close() and window.isVisible()
    world.dialogs.answer = QMessageBox.StandardButton.Yes
    assert window.close() and not window.isVisible()
    assert "Discard this message?" in box_titles(world, "question")
    event = QCloseEvent()
    other = new_composer(world, qtbot)
    fill(other)
    world.dialogs.answer = QMessageBox.StandardButton.No
    other.closeEvent(event)
    assert not event.isAccepted()
    other._finished = True  # noqa: SLF001
    other.close()


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


def test_two_accounts_can_be_chosen_as_the_sender(world: World, qtbot: QtBot) -> None:
    world.call("account.add", display_name="Work", email_address="work@example.com", username="user", password="secret", incoming_host="127.0.0.1", incoming_port=world.pop.port, incoming_security="none", outgoing_host="127.0.0.1", outgoing_port=world.smtp.port, outgoing_security="none")
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
